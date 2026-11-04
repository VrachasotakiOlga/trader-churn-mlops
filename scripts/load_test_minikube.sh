#!/usr/bin/env bash
# Send traffic to the API from inside the cluster and report the failures.
# Usage: scripts/load_test_minikube.sh [seconds] [parallel workers]
set -euo pipefail

seconds="${1:-120}"
workers="${2:-8}"
context="trader-churn"
namespace="trader-churn"
kube=(kubectl --context "$context" --namespace "$namespace")
image="$("${kube[@]}" get deployment trader-churn-api \
  -o jsonpath='{.spec.template.spec.containers[0].image}')"

# The namespace enforces the restricted standard, so this pod has to follow it too.
read -r -d '' overrides <<JSON || true
{
  "spec": {
    "automountServiceAccountToken": false,
    "securityContext": {
      "runAsNonRoot": true, "runAsUser": 1000,
      "seccompProfile": {"type": "RuntimeDefault"}
    },
    "containers": [{
      "name": "load-test",
      "image": "$image",
      "imagePullPolicy": "IfNotPresent",
      "securityContext": {
        "allowPrivilegeEscalation": false,
        "capabilities": {"drop": ["ALL"]}
      },
      "command": ["python", "-c", $(python3 -c 'import json, sys; print(json.dumps(sys.stdin.read()))' <<PY
import json, threading, time, urllib.request
URL = "http://trader-churn-api/predict"
BODY = json.dumps({"tenure_days": 400, "country": "Greece", "account_type": "standard",
    "trades_30d": 40, "trades_7d": 10, "days_since_last_trade": 1,
    "deposits_30d": 4, "withdrawals_30d": 0}).encode()
end = time.time() + $seconds
counts = {"ok": 0, "failed": 0}
lock = threading.Lock()
def work():
    while time.time() < end:
        request = urllib.request.Request(URL, BODY, {"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(request, timeout=5).read()
            result = "ok"
        except Exception as error:
            result = "failed"
            print("error:", error, flush=True)
        with lock:
            counts[result] += 1
threads = [threading.Thread(target=work) for _ in range($workers)]
[t.start() for t in threads]
[t.join() for t in threads]
print("RESULT", counts, flush=True)
PY
)]
    }]
  }
}
JSON

"${kube[@]}" delete pod load-test --ignore-not-found >/dev/null
"${kube[@]}" run load-test --image="$image" --restart=Never \
  --labels="trader-churn-api-access=true" --overrides="$overrides" >/dev/null
"${kube[@]}" wait --for=condition=Ready pod/load-test --timeout=60s >/dev/null
echo "Sending traffic for $seconds seconds with $workers workers..."
"${kube[@]}" wait --for=jsonpath='{.status.phase}'=Succeeded pod/load-test \
  --timeout="$((seconds + 60))s" >/dev/null
"${kube[@]}" logs load-test | tail -n 15
"${kube[@]}" delete pod load-test --ignore-not-found >/dev/null
