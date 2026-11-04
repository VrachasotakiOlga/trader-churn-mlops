"""Serve churn predictions over HTTP.

Run it with:
    uv run python -m churn.train
    MODEL_PATH=models/model.joblib uv run uvicorn churn.serve:app --port 8000
"""

import json
import logging
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Self

import joblib
import pandas as pd
from fastapi import FastAPI, HTTPException, Request, Response, status
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from pydantic import BaseModel, ConfigDict, Field, model_validator

from churn.data import FEATURE_COLUMNS
from churn.drift import REFERENCE_FILENAME, DriftMonitor

logger = logging.getLogger(__name__)

DEFAULT_MODEL_PATH = "/models/model.joblib"

# What Prometheus collects from /metrics.
REQUESTS = Counter(
    "churn_http_requests_total", "HTTP requests received", ["path", "status"]
)
PREDICTION_LATENCY = Histogram(
    "churn_prediction_latency_seconds", "Time taken to produce a prediction"
)
PREDICTED_PROBABILITY = Histogram(
    "churn_predicted_probability",
    "Predicted churn probabilities",
    buckets=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
)
FEATURE_DRIFT = Gauge(
    "churn_feature_drift_psi",
    "How far recent inputs have moved from the training data (PSI)",
    ["feature"],
)
DRIFT_SAMPLES = Gauge(
    "churn_drift_window_samples", "Recent requests available for drift detection"
)
KNOWN_PATHS = {"/predict", "/healthz", "/readyz", "/metrics"}


class Client(BaseModel):
    """What a caller must send. Anything else is rejected before reaching the model."""

    model_config = ConfigDict(extra="forbid")

    tenure_days: int = Field(ge=0)
    country: str = Field(min_length=1, max_length=64)
    account_type: Literal["micro", "standard", "ultra_low"]
    trades_30d: int = Field(ge=0)
    trades_7d: int = Field(ge=0)
    days_since_last_trade: int = Field(ge=0, le=30)
    deposits_30d: int = Field(ge=0)
    withdrawals_30d: int = Field(ge=0)

    @model_validator(mode="after")
    def recent_trades_fit_within_the_month(self) -> Self:
        if self.trades_7d > self.trades_30d:
            raise ValueError("trades_7d can't be greater than trades_30d")
        return self


class Prediction(BaseModel):
    churn_probability: float


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Load the model once at startup, not on every request.

    Joblib can run code from the file it loads, so MODEL_PATH must only ever point
    to a model our own training created.
    """
    model_path = Path(os.environ.get("MODEL_PATH", DEFAULT_MODEL_PATH))
    app.state.model = None
    app.state.drift_monitor = None
    reference_path = model_path.with_name(REFERENCE_FILENAME)
    if reference_path.exists():
        app.state.drift_monitor = DriftMonitor(json.loads(reference_path.read_text()))
    else:
        logger.warning("No drift reference at %s", reference_path)
    if model_path.exists():
        app.state.model = joblib.load(model_path)
        logger.info("Loaded model from %s", model_path)
    else:
        # Keep running but report "not ready", so the reason is easy to see.
        logger.error("No model found at %s", model_path)
    yield


app = FastAPI(title="Trader Churn API", lifespan=lifespan)


@app.middleware("http")
async def count_requests(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    response = await call_next(request)
    # Unknown paths share one label, so bots scanning random URLs can't create
    # unlimited metrics and exhaust Prometheus.
    path = request.url.path if request.url.path in KNOWN_PATHS else "other"
    REQUESTS.labels(path=path, status=response.status_code).inc()
    return response


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness: is the process running? Failing means "restart me"."""
    return {"status": "ok"}


@app.get("/readyz")
def readyz(request: Request, response: Response) -> dict[str, str]:
    """Readiness: can it answer predictions? Failing means "send no traffic yet"."""
    if request.app.state.model is None:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "model not loaded"}
    return {"status": "ready"}


@app.post("/predict")
def predict(client: Client, request: Request) -> Prediction:
    model = request.app.state.model
    if model is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Model not loaded")

    start = time.perf_counter()
    features = pd.DataFrame([client.model_dump()])[FEATURE_COLUMNS]
    probability = float(model.predict_proba(features)[0, 1])
    PREDICTION_LATENCY.observe(time.perf_counter() - start)
    PREDICTED_PROBABILITY.observe(probability)

    if request.app.state.drift_monitor is not None:
        request.app.state.drift_monitor.observe(client.model_dump())
    return Prediction(churn_probability=probability)


@app.get("/metrics")
def metrics(request: Request) -> Response:
    """Prometheus reads this every few seconds. Drift is calculated on demand."""
    monitor = request.app.state.drift_monitor
    if monitor is not None:
        DRIFT_SAMPLES.set(monitor.sample_count())
        for feature, score in monitor.scores().items():
            FEATURE_DRIFT.labels(feature=feature).set(score)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
