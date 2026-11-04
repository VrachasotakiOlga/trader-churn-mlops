"""Store models in S3, and fetch the one currently in production.

Training uploads a candidate. The promotion Lambda then decides whether production
should point to it. The API only ever reads what the pointer names, and checks each
file's checksum, because loading a tampered model file can run code.

Fetch the production model, or go back to the previous one, with:
    python -m churn.storage --bucket trader-churn-models --output models
    python -m churn.storage --bucket trader-churn-models --rollback
"""

import argparse
import hashlib
import json
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

from churn.drift import REFERENCE_FILENAME

MODEL_FILENAME = "model.joblib"
POINTER_KEY = "production/current.json"


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def read_pointer(bucket: str) -> dict | None:
    """Describe the production model, or return None if there isn't one yet."""
    try:
        response = boto3.client("s3").get_object(Bucket=bucket, Key=POINTER_KEY)
    except ClientError as error:
        if error.response["Error"]["Code"] in {"NoSuchKey", "404"}:
            return None
        raise
    return json.loads(response["Body"].read())


def upload_candidate(bucket: str, model_dir: Path, metrics: dict) -> str:
    """Upload a model that passed the gate, and return where it went."""
    s3 = boto3.client("s3")
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    candidate = f"candidates/{timestamp}-{uuid.uuid4().hex[:8]}"

    checksums = {}
    for filename in (MODEL_FILENAME, REFERENCE_FILENAME):
        content = (model_dir / filename).read_bytes()
        checksums[filename] = sha256(content)
        s3.put_object(Bucket=bucket, Key=f"{candidate}/{filename}", Body=content)

    # Must be last: uploading this is what starts the promotion Lambda.
    summary = {"quality_gate": "passed", "metrics": metrics, "sha256": checksums}
    s3.put_object(
        Bucket=bucket,
        Key=f"{candidate}/metrics.json",
        Body=json.dumps(summary).encode(),
        ContentType="application/json",
    )
    return candidate


def download_production(bucket: str, output_dir: Path, wait_seconds: int = 0) -> dict:
    """Download the production model, waiting for one to exist if asked to."""
    deadline = time.monotonic() + wait_seconds
    while (pointer := read_pointer(bucket)) is None:
        if time.monotonic() >= deadline:
            raise TimeoutError(f"No production model in {bucket}")
        print("Waiting for a production model...", flush=True)
        time.sleep(3)

    s3 = boto3.client("s3")
    output_dir.mkdir(parents=True, exist_ok=True)
    # Check everything before writing anything, so a bad file is never left behind.
    verified = {}
    for filename, expected in pointer["sha256"].items():
        key = f"{pointer['candidate']}/{filename}"
        content = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        if sha256(content) != expected:
            raise ValueError(f"{key} failed its checksum, refusing to use it")
        verified[filename] = content

    # The model is written last, because the API treats it as "everything is ready".
    for filename in sorted(verified, key=lambda name: name == MODEL_FILENAME):
        (output_dir / filename).write_bytes(verified[filename])
    return pointer


def rollback(bucket: str) -> str:
    """Point production back at the previous model, and return which that is."""
    s3 = boto3.client("s3")
    pointer = read_pointer(bucket)
    if pointer is None or not pointer.get("previous"):
        raise ValueError("there's no previous model to go back to")

    previous = pointer["previous"]
    body = s3.get_object(Bucket=bucket, Key=f"{previous}/metrics.json")["Body"].read()
    summary = json.loads(body)
    new_pointer = {
        "candidate": previous,
        "sha256": summary["sha256"],
        "metrics": summary["metrics"],
        "previous": pointer["candidate"],
    }
    s3.put_object(
        Bucket=bucket,
        Key=POINTER_KEY,
        Body=json.dumps(new_pointer).encode(),
        ContentType="application/json",
    )
    return previous


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Fetch or roll back the model.")
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--wait-seconds", type=int, default=0)
    parser.add_argument("--rollback", action="store_true")
    args = parser.parse_args(argv)

    if args.rollback:
        try:
            print(f"Production now points to {rollback(args.bucket)}")
        except ValueError as error:
            sys.exit(f"Could not roll back: {error}")
        return

    if args.output is None:
        parser.error("--output is required unless using --rollback")

    try:
        pointer = download_production(args.bucket, args.output, args.wait_seconds)
    except (TimeoutError, ValueError) as error:
        sys.exit(f"Could not fetch the model: {error}")
    print(f"Fetched {pointer['candidate']}")


if __name__ == "__main__":
    main()
