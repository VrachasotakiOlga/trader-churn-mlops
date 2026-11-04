"""Serve churn predictions over HTTP.

Run it with:
    uv run python -m churn.train
    MODEL_PATH=models/model.joblib uv run uvicorn churn.serve:app --port 8000
"""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Self

import joblib
import pandas as pd
from fastapi import FastAPI, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from churn.data import FEATURE_COLUMNS

logger = logging.getLogger(__name__)

DEFAULT_MODEL_PATH = "/models/model.joblib"


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
    if model_path.exists():
        app.state.model = joblib.load(model_path)
        logger.info("Loaded model from %s", model_path)
    else:
        # Keep running but report "not ready", so the reason is easy to see.
        logger.error("No model found at %s", model_path)
    yield


app = FastAPI(title="Trader Churn API", lifespan=lifespan)


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

    features = pd.DataFrame([client.model_dump()])[FEATURE_COLUMNS]
    return Prediction(churn_probability=float(model.predict_proba(features)[0, 1]))
