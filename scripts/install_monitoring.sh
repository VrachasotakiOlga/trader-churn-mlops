#!/usr/bin/env bash
# Install Prometheus, Alertmanager and Grafana into minikube.
# Usage: scripts/install_monitoring.sh
set -euo pipefail

context="trader-churn"
namespace="monitoring"

helm repo add prometheus-community \
  https://prometheus-community.github.io/helm-charts --force-update >/dev/null
helm repo update prometheus-community >/dev/null

# A fixed version, so this installs the same thing every time.
helm upgrade --install monitoring prometheus-community/kube-prometheus-stack \
  --version 92.18.0 \
  --kube-context "$context" \
  --namespace "$namespace" --create-namespace \
  --values deploy/monitoring-values.yaml \
  --wait --timeout 10m

echo
echo "Open Grafana with:"
echo "  kubectl --context $context -n $namespace port-forward svc/monitoring-grafana 3000:80"
echo "Then visit http://localhost:3000. The user is admin. Get the password with:"
echo "  kubectl --context $context -n $namespace get secret monitoring-grafana \\"
echo "    -o jsonpath='{.data.admin-password}' | base64 -d; echo"
