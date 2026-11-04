from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from churn.retrain_trigger import (
    DRIFT_ALERT,
    app,
    drift_alert_is_firing,
    reason_not_to_retrain,
    reconcile,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
COOLDOWN = timedelta(minutes=15)


def payload(status="firing", alertname=DRIFT_ALERT):
    return {"alerts": [{"status": status, "labels": {"alertname": alertname}}]}


class FakeCluster:
    def __init__(self, rolled_out=None, jobs=()):
        self.rolled_out = rolled_out
        self.jobs = list(jobs)
        self.created = []
        self.recorded = []
        self.rollouts = []

    def retraining_jobs(self):
        return self.jobs

    def create_retraining_job(self, name):
        self.created.append(name)

    def rolled_out_candidate(self):
        return self.rolled_out

    def record_candidate(self, candidate):
        self.recorded.append(candidate)

    def roll_out_candidate(self, candidate):
        self.rollouts.append(candidate)


@pytest.fixture
def cluster():
    fake = FakeCluster()
    app.state.cluster = fake
    yield fake
    del app.state.cluster


@pytest.fixture
def api(cluster):
    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.parametrize(
    ("alert_payload", "expected"),
    [
        (payload(), True),
        (payload(status="resolved"), False),
        (payload(alertname="ChurnHighLatency"), False),
        ({"alerts": []}, False),
        ({}, False),
    ],
)
def test_only_a_firing_drift_alert_counts(alert_payload, expected):
    assert drift_alert_is_firing(alert_payload) is expected


def test_retrains_when_nothing_has_run():
    assert reason_not_to_retrain([], NOW, COOLDOWN) is None


def test_does_not_retrain_while_a_job_is_running():
    jobs = [{"created": NOW - timedelta(hours=5), "active": True}]
    assert "running" in reason_not_to_retrain(jobs, NOW, COOLDOWN)


def test_does_not_retrain_during_the_cooldown():
    jobs = [{"created": NOW - timedelta(minutes=5), "active": False}]
    assert "within" in reason_not_to_retrain(jobs, NOW, COOLDOWN)


def test_retrains_again_after_the_cooldown():
    jobs = [{"created": NOW - timedelta(minutes=20), "active": False}]
    assert reason_not_to_retrain(jobs, NOW, COOLDOWN) is None


def test_reconcile_does_nothing_without_a_model():
    fake = FakeCluster()
    reconcile(fake, None)
    assert fake.recorded == [] and fake.rollouts == []


def test_reconcile_records_without_replacing_freshly_deployed_pods():
    fake = FakeCluster()
    reconcile(fake, "candidates/1")
    assert fake.recorded == ["candidates/1"]
    assert fake.rollouts == []


def test_reconcile_does_nothing_when_up_to_date():
    fake = FakeCluster(rolled_out="candidates/1")
    reconcile(fake, "candidates/1")
    assert fake.recorded == [] and fake.rollouts == []


def test_reconcile_rolls_out_a_new_model():
    fake = FakeCluster(rolled_out="candidates/1")
    reconcile(fake, "candidates/2")
    assert fake.rollouts == ["candidates/2"]


def test_reconcile_also_rolls_back():
    fake = FakeCluster(rolled_out="candidates/2")
    reconcile(fake, "candidates/1")
    assert fake.rollouts == ["candidates/1"]


def test_alert_starts_a_retraining_job(api, cluster):
    response = api.post("/alerts", json=payload())
    assert response.status_code == 202
    assert len(cluster.created) == 1


def test_resolved_alert_starts_nothing(api, cluster):
    assert api.post("/alerts", json=payload(status="resolved")).status_code == 200
    assert cluster.created == []


def test_repeated_alert_during_cooldown_starts_nothing(api, cluster):
    cluster.jobs = [{"created": datetime.now(UTC), "active": False}]
    assert api.post("/alerts", json=payload()).json()["status"] == "skipped"
    assert cluster.created == []


def test_health_check(api):
    assert api.get("/healthz").status_code == 200
