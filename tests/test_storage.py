import json

import boto3
import pytest
from moto import mock_aws

from churn import storage
from churn.drift import REFERENCE_FILENAME

BUCKET = "models"


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


@pytest.fixture
def model_dir(tmp_path):
    folder = tmp_path / "trained"
    folder.mkdir()
    (folder / storage.MODEL_FILENAME).write_bytes(b"the model")
    (folder / REFERENCE_FILENAME).write_text('{"feature": 1}')
    return folder


def promote(s3, candidate):
    """Do what the Lambda does, in the simplest way."""
    body = s3.get_object(Bucket=BUCKET, Key=f"{candidate}/metrics.json")["Body"].read()
    summary = json.loads(body)
    pointer = {"candidate": candidate, "sha256": summary["sha256"]}
    s3.put_object(Bucket=BUCKET, Key=storage.POINTER_KEY, Body=json.dumps(pointer))


def test_no_pointer_before_the_first_promotion(s3):
    assert storage.read_pointer(BUCKET) is None


def test_upload_does_not_change_production_by_itself(s3, model_dir):
    storage.upload_candidate(BUCKET, model_dir, {"roc_auc": 0.8})
    assert storage.read_pointer(BUCKET) is None


def test_metrics_file_records_the_checksums(s3, model_dir):
    candidate = storage.upload_candidate(BUCKET, model_dir, {"roc_auc": 0.8})
    body = s3.get_object(Bucket=BUCKET, Key=f"{candidate}/metrics.json")["Body"].read()
    summary = json.loads(body)

    assert summary["quality_gate"] == "passed"
    assert summary["sha256"][storage.MODEL_FILENAME] == storage.sha256(b"the model")


def test_download_gets_the_production_files(s3, model_dir, tmp_path):
    promote(s3, storage.upload_candidate(BUCKET, model_dir, {"roc_auc": 0.8}))
    output = tmp_path / "fetched"

    storage.download_production(BUCKET, output)

    assert (output / storage.MODEL_FILENAME).read_bytes() == b"the model"
    assert (output / REFERENCE_FILENAME).read_text() == '{"feature": 1}'


def test_tampered_model_is_refused_and_nothing_is_written(s3, model_dir, tmp_path):
    candidate = storage.upload_candidate(BUCKET, model_dir, {"roc_auc": 0.8})
    promote(s3, candidate)
    s3.put_object(
        Bucket=BUCKET, Key=f"{candidate}/{storage.MODEL_FILENAME}", Body=b"tampered"
    )
    output = tmp_path / "fetched"

    with pytest.raises(ValueError, match="checksum"):
        storage.download_production(BUCKET, output)
    assert list(output.glob("*")) == []


def test_command_line_exits_with_an_error_without_a_model(s3, tmp_path):
    with pytest.raises(SystemExit) as exit_info:
        storage.main(["--bucket", BUCKET, "--output", str(tmp_path / "x")])
    assert "No production model" in str(exit_info.value.code)
