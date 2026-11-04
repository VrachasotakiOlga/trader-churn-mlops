"""Start retraining when drift is detected, and roll out newly promoted models.

It does two independent jobs:

1. Alertmanager sends drift alerts to POST /alerts, which starts a retraining Job,
   unless one is running or finished recently.
2. A background loop compares the production pointer in S3 with what's deployed, and
   starts a rolling update when they differ.

The second job is a "reconcile loop", as used by Kubernetes itself: compare what should
be true with what is true, and fix any difference. It doesn't matter how the pointer
changed (retraining, or a manual rollback) or if this process restarted along the way.
"""

import logging
import os
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Protocol

from fastapi import FastAPI, Request, Response, status
from kubernetes import client, config

from churn.storage import read_pointer

logger = logging.getLogger("uvicorn.error")

DRIFT_ALERT = "ChurnFeatureDrift"
RETRAIN_LABEL = "trader-churn/retraining"
# On the Deployment: the model the running pods were last rolled out with.
ROLLED_OUT_ANNOTATION = "trader-churn/rolled-out-candidate"
# On the pod template: changing this is what makes Kubernetes replace the pods.
POD_ANNOTATION = "trader-churn/model-candidate"


class Cluster(Protocol):
    """Everything this needs from Kubernetes. Tests provide a fake one."""

    def retraining_jobs(self) -> list[dict]: ...

    def create_retraining_job(self, name: str) -> None: ...

    def rolled_out_candidate(self) -> str | None: ...

    def record_candidate(self, candidate: str) -> None: ...

    def roll_out_candidate(self, candidate: str) -> None: ...


def drift_alert_is_firing(alert_payload: dict) -> bool:
    return any(
        alert.get("status") == "firing"
        and alert.get("labels", {}).get("alertname") == DRIFT_ALERT
        for alert in alert_payload.get("alerts", [])
    )


def reason_not_to_retrain(
    jobs: list[dict], now: datetime, cooldown: timedelta
) -> str | None:
    """Alerts repeat, and training is expensive. Return why to skip, if we should."""
    if any(job["active"] for job in jobs):
        return "retraining is already running"
    if any(now - job["created"] < cooldown for job in jobs):
        return f"retraining ran within the last {cooldown}"
    return None


def reconcile(cluster: Cluster, candidate: str | None) -> str:
    """Bring the running pods in line with the production pointer."""
    if candidate is None:
        return "no production model yet"

    rolled_out = cluster.rolled_out_candidate()
    if rolled_out == candidate:
        return "up to date"
    if rolled_out is None:
        # Nothing recorded means the pods have just been deployed, and they fetched
        # the current model when they started. Replacing them would achieve nothing.
        cluster.record_candidate(candidate)
        return f"recorded {candidate}"

    cluster.roll_out_candidate(candidate)
    return f"rolling out {candidate}"


class KubernetesCluster:
    def __init__(self, namespace: str, cron_job: str, deployment: str) -> None:
        config.load_incluster_config()
        self.batch = client.BatchV1Api()
        self.apps = client.AppsV1Api()
        self.namespace = namespace
        self.cron_job = cron_job
        self.deployment = deployment

    def retraining_jobs(self) -> list[dict]:
        jobs = self.batch.list_namespaced_job(
            self.namespace, label_selector=f"{RETRAIN_LABEL}=true"
        )
        return [
            {
                "created": job.metadata.creation_timestamp,
                "active": bool(job.status.active),
            }
            for job in jobs.items
        ]

    def create_retraining_job(self, name: str) -> None:
        # The same thing as "kubectl create job --from=cronjob/...".
        template = self.batch.read_namespaced_cron_job(self.cron_job, self.namespace)
        job = client.V1Job(
            metadata=client.V1ObjectMeta(
                name=name,
                labels={RETRAIN_LABEL: "true"},
                owner_references=[
                    client.V1OwnerReference(
                        api_version="batch/v1",
                        kind="CronJob",
                        name=template.metadata.name,
                        uid=template.metadata.uid,
                    )
                ],
            ),
            spec=template.spec.job_template.spec,
        )
        self.batch.create_namespaced_job(self.namespace, job)

    def rolled_out_candidate(self) -> str | None:
        deployment = self.apps.read_namespaced_deployment(
            self.deployment, self.namespace
        )
        return (deployment.metadata.annotations or {}).get(ROLLED_OUT_ANNOTATION)

    def record_candidate(self, candidate: str) -> None:
        self._patch({"metadata": {"annotations": {ROLLED_OUT_ANNOTATION: candidate}}})

    def roll_out_candidate(self, candidate: str) -> None:
        self._patch(
            {
                "metadata": {"annotations": {ROLLED_OUT_ANNOTATION: candidate}},
                "spec": {
                    "template": {
                        "metadata": {"annotations": {POD_ANNOTATION: candidate}}
                    }
                },
            }
        )

    def _patch(self, body: dict) -> None:
        self.apps.patch_namespaced_deployment(self.deployment, self.namespace, body)


def reconcile_forever(app: FastAPI, stop: threading.Event) -> None:
    bucket = os.environ["MODEL_BUCKET"]
    interval = int(os.environ.get("RECONCILE_SECONDS", "15"))
    last_outcome = None
    while not stop.is_set():
        try:
            pointer = read_pointer(bucket)
            candidate = pointer["candidate"] if pointer else None
            outcome = reconcile(app.state.cluster, candidate)
            if outcome != last_outcome:
                logger.info("Reconcile: %s", outcome)
                last_outcome = outcome
            app.state.reconcile_ok = True
        except Exception:
            # A brief S3 or Kubernetes outage shouldn't stop the loop for good.
            logger.exception("Reconcile failed, will try again")
            app.state.reconcile_ok = False
        stop.wait(interval)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.cooldown = timedelta(seconds=int(os.environ.get("COOLDOWN_SECONDS", 900)))
    app.state.job_prefix = os.environ.get("CRON_JOB_NAME", "retrain")
    stop = threading.Event()
    thread = None
    if not hasattr(app.state, "cluster"):
        app.state.cluster = KubernetesCluster(
            os.environ["NAMESPACE"],
            os.environ["CRON_JOB_NAME"],
            os.environ["DEPLOYMENT_NAME"],
        )
        thread = threading.Thread(
            target=reconcile_forever, args=(app, stop), daemon=True
        )
        thread.start()
    yield
    stop.set()
    if thread:
        thread.join(timeout=5)


app = FastAPI(title="Trader Churn Retrain Trigger", lifespan=lifespan)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/alerts")
def alerts(payload: dict, request: Request, response: Response) -> dict[str, str]:
    """Receive alerts from Alertmanager."""
    if not drift_alert_is_firing(payload):
        return {"status": "ignored", "reason": "no drift alert is firing"}

    state = request.app.state
    now = datetime.now(UTC)
    reason = reason_not_to_retrain(state.cluster.retraining_jobs(), now, state.cooldown)
    if reason:
        logger.info("Not retraining: %s", reason)
        return {"status": "skipped", "reason": reason}

    name = f"{state.job_prefix}-{int(now.timestamp())}"
    state.cluster.create_retraining_job(name)
    logger.info("Started retraining job %s", name)
    response.status_code = status.HTTP_202_ACCEPTED
    return {"status": "started", "job": name}
