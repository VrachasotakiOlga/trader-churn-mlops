#!/usr/bin/env bash
# Send realistic traffic to the API from inside the cluster and report the failures.
# Usage: scripts/load_test_minikube.sh [seconds] [parallel workers] [drift, 0.0 to 1.0]
set -euo pipefail

seconds="${1:-120}"
workers="${2:-8}"
drift="${3:-0.0}"
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
from churn.data import FEATURE_COLUMNS, generate_clients
URL = "http://trader-churn-api/predict"
clients = generate_clients(5000, seed=int(time.time()), drift=$drift)[FEATURE_COLUMNS]
bodies = [json.dumps(client).encode() for client in clients.to_dict("records")]
end = time.time() + $seconds
counts = {"ok": 0, "failed": 0}
lock = threading.Lock()
def work(start):
    position = start
    while time.time() < end:
        position += 1
        body = bodies[position % len(bodies)]
        request = urllib.request.Request(URL, body, {"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(request, timeout=5).read()
            result = "ok"
        except Exception as error:
            result = "failed"
            print("error:", error, flush=True)
        with lock:
            counts[result] += 1
threads = [threading.Thread(target=work, args=(n * 500,)) for n in range($workers)]
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
echo "Sending traffic for $seconds seconds with $workers workers (drift $drift)..."
"${kube[@]}" wait --for=jsonpath='{.status.phase}'=Succeeded pod/load-test \
  --timeout="$((seconds + 60))s" >/dev/null
"${kube[@]}" logs load-test | tail -n 15
"${kube[@]}" delete pod load-test --ignore-not-found >/dev/null
