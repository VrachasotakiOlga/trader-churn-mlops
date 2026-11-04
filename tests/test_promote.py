import hashlib
import importlib.util
import json
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

BUCKET = "models"
CANDIDATE = "candidates/1"

# The Lambda isn't part of the churn package, so load it from its file.
spec = importlib.util.spec_from_file_location(
    "promote", Path(__file__).parent.parent / "infra" / "lambda" / "promote.py"
)
promote = importlib.util.module_from_spec(spec)
spec.loader.exec_module(promote)


@pytest.fixture
def s3(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    with mock_aws():
        client = boto3.client("s3")
        client.create_bucket(Bucket=BUCKET)
        yield client


def upload_candidate(s3, name=CANDIDATE, gate="passed", checksum=None):
    model = f"model of {name}".encode()
    s3.put_object(Bucket=BUCKET, Key=f"{name}/model.joblib", Body=model)
    metrics = {
        "quality_gate": gate,
        "metrics": {"roc_auc": 0.8},
        "sha256": {"model.joblib": checksum or hashlib.sha256(model).hexdigest()},
    }
    key = f"{name}/metrics.json"
    s3.put_object(Bucket=BUCKET, Key=key, Body=json.dumps(metrics).encode())
    return {"Records": [{"s3": {"bucket": {"name": BUCKET}, "object": {"key": key}}}]}


def pointer(s3):
    body = s3.get_object(Bucket=BUCKET, Key=promote.POINTER_KEY)["Body"].read()
    return json.loads(body)


def test_passing_candidate_becomes_production(s3):
    result = promote.handler(upload_candidate(s3), None)

    assert result["results"][0]["promoted"] is True
    assert pointer(s3)["candidate"] == CANDIDATE
    assert pointer(s3)["previous"] is None


def test_next_promotion_remembers_the_previous_model(s3):
    promote.handler(upload_candidate(s3), None)
    promote.handler(upload_candidate(s3, name="candidates/2"), None)

    assert pointer(s3)["candidate"] == "candidates/2"
    assert pointer(s3)["previous"] == CANDIDATE


def test_failing_candidate_changes_nothing(s3):
    promote.handler(upload_candidate(s3), None)
    event = upload_candidate(s3, name="candidates/2", gate="failed")

    assert promote.handler(event, None)["results"][0]["promoted"] is False
    assert pointer(s3)["candidate"] == CANDIDATE


def test_tampered_model_is_refused(s3):
    promote.handler(upload_candidate(s3), None)
    event = upload_candidate(s3, name="candidates/2", checksum="0" * 64)

    with pytest.raises(ValueError, match="checksum"):
        promote.handler(event, None)
    assert pointer(s3)["candidate"] == CANDIDATE


def test_event_keys_are_decoded(s3):
    event = upload_candidate(s3, name="candidates/with space")
    record = event["Records"][0]["s3"]["object"]
    record["key"] = record["key"].replace(" ", "+")

    assert promote.handler(event, None)["results"][0]["promoted"] is True
