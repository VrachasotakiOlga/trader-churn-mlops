#!/usr/bin/env bash
# Start the real image and check it answers correctly.
# Usage: scripts/smoke_test_image.sh <image> <folder containing model.joblib>
set -euo pipefail

image="${1:?Usage: smoke_test_image.sh <image> <model folder>}"
model_dir="$(cd "${2:?Usage: smoke_test_image.sh <image> <model folder>}" && pwd)"
name="churn-smoke-test"
url="http://127.0.0.1:18000"

# Remove the container however this script ends.
trap 'docker rm -f "$name" >/dev/null 2>&1 || true' EXIT

# Locked down on purpose: read-only filesystem, no extra Linux privileges, and the
# model mounted read-only. The image has to work under these limits.
docker run --detach --name "$name" \
  --read-only --tmpfs /tmp --cap-drop ALL \
  --publish 127.0.0.1:18000:8000 \
  --volume "$model_dir:/models:ro" \
  "$image" >/dev/null

echo "Waiting for the API to be ready..."
for _ in $(seq 1 30); do
  if curl --silent --fail "$url/readyz" >/dev/null; then
    break
  fi
  sleep 1
done
if ! curl --silent --fail "$url/readyz" >/dev/null; then
  echo "FAILED: the API never became ready. Container logs:"
  docker logs "$name"
  exit 1
fi

user_id="$(docker exec "$name" id -u)"
if [ "$user_id" = "0" ]; then
  echo "FAILED: the container is running as root"
  exit 1
fi
echo "OK: running as non-root user $user_id"

client='{"tenure_days": 400, "country": "Greece", "account_type": "standard",
  "trades_30d": 40, "trades_7d": 10, "days_since_last_trade": 1,
  "deposits_30d": 4, "withdrawals_30d": 0}'

prediction="$(curl --silent --fail -X POST "$url/predict" \
  -H "Content-Type: application/json" -d "$client")"
if [[ "$prediction" != *churn_probability* ]]; then
  echo "FAILED: unexpected prediction: $prediction"
  exit 1
fi
echo "OK: prediction $prediction"

bad_status="$(curl --silent --output /dev/null --write-out '%{http_code}' \
  -X POST "$url/predict" -H "Content-Type: application/json" -d '{"country": ""}')"
if [ "$bad_status" != "422" ]; then
  echo "FAILED: bad input returned $bad_status instead of 422"
  exit 1
fi
echo "OK: bad input rejected with 422"
