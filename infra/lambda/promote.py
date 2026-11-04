"""Decide whether a newly trained model becomes the production model.

S3 runs this when a candidate's metrics.json is uploaded, which training does last, so
the other files are already there. Production is a small pointer file, so promoting or
rolling back only changes which candidate it names. Nothing else moves.
"""

import hashlib
import json
from urllib.parse import unquote_plus

import boto3

POINTER_KEY = "production/current.json"


def handler(event: dict, context: object) -> dict:
    s3 = boto3.client("s3")
    results = []
    for record in event["Records"]:
        bucket = record["s3"]["bucket"]["name"]
        # S3 event keys are URL-encoded.
        key = unquote_plus(record["s3"]["object"]["key"])
        results.append(promote(s3, bucket, key))
    return {"results": results}


def promote(s3: object, bucket: str, metrics_key: str) -> dict:
    metrics = json.loads(read(s3, bucket, metrics_key))
    candidate = metrics_key.rsplit("/", 1)[0]

    if metrics.get("quality_gate") != "passed":
        print(f"Rejected {candidate}: it didn't pass the quality gate")
        return {"candidate": candidate, "promoted": False}

    # Refuse files that don't match what training recorded.
    for filename, expected in metrics["sha256"].items():
        actual = hashlib.sha256(read(s3, bucket, f"{candidate}/{filename}")).hexdigest()
        if actual != expected:
            raise ValueError(f"{candidate}/{filename} failed its checksum")

    pointer = {
        "candidate": candidate,
        "sha256": metrics["sha256"],
        "metrics": metrics["metrics"],
        # Remembered, so rolling back only needs to read this file.
        "previous": current_candidate(s3, bucket),
    }
    s3.put_object(
        Bucket=bucket,
        Key=POINTER_KEY,
        Body=json.dumps(pointer).encode(),
        ContentType="application/json",
    )
    print(f"Promoted {candidate}")
    return {"candidate": candidate, "promoted": True}


def read(s3: object, bucket: str, key: str) -> bytes:
    return s3.get_object(Bucket=bucket, Key=key)["Body"].read()


def current_candidate(s3: object, bucket: str) -> str | None:
    try:
        return json.loads(read(s3, bucket, POINTER_KEY))["candidate"]
    except s3.exceptions.NoSuchKey:
        return None
