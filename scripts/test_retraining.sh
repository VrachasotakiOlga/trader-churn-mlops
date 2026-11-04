#!/usr/bin/env bash
# Send a fake drift alert and check the whole loop: a retraining Job runs, the new model
# gets promoted, the API rolls out, and it still answers. It needs a production model
# trained without drift, and no recent retraining, as on a fresh install.
# Usage: scripts/test_retraining.sh [kubectl context]
set -euo pipefail

namespace="trader-churn"
kube=(kubectl --namespace "$namespace")
helm_context=()
if [ -n "${1:-}" ]; then
  kube+=(--context "$1")
  helm_context=(--kube-context "$1")
fi
deployment="deployment/trader-churn-api"
annotation='{.metadata.annotations.trader-churn/rolled-out-candidate}'
before=""

echo "Waiting for the controller to record the current model..."
for _ in $(seq 1 40); do
  before="$("${kube[@]}" get "$deployment" -o jsonpath="$annotation")"
  [ -n "$before" ] && break
  sleep 3
done
[ -n "$before" ] || { echo "FAILED: nothing was recorded"; exit 1; }
echo "Current model: $before"

alert='{"alerts": [{"status": "firing", "labels": {"alertname": "ChurnFeatureDrift"}}]}'
"${kube[@]}" port-forward svc/trader-churn-retrain-trigger 18080:8080 >/dev/null &
forward=$!
trap 'kill "$forward" 2>/dev/null || true' EXIT
sleep 3
response="$(curl --silent --show-error --fail -X POST http://127.0.0.1:18080/alerts \
  -H "Content-Type: application/json" -d "$alert")"
echo "Alert response: $response"
[[ "$response" == *started* ]] || { echo "FAILED: retraining didn't start"; exit 1; }

echo "Waiting for the new model to be promoted and rolled out..."
after=""
for _ in $(seq 1 80); do
  after="$("${kube[@]}" get "$deployment" -o jsonpath="$annotation")"
  [ "$after" != "$before" ] && break
  sleep 5
done
if [ "$after" = "$before" ]; then
  echo "FAILED: the model never changed"
  "${kube[@]}" get jobs
  exit 1
fi
echo "New model: $after"

"${kube[@]}" rollout status "$deployment" --timeout=180s
helm test trader-churn --namespace "$namespace" ${helm_context[@]+"${helm_context[@]}"} --logs \
  | grep "OK:"
echo "OK: the retraining loop works"
