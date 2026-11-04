#!/usr/bin/env bash
# Build both images, load them into minikube, and deploy the Helm chart.
# Usage: scripts/deploy_minikube.sh
set -euo pipefail

context="trader-churn"   # Always named explicitly, so this can't touch another cluster.
namespace="trader-churn"
tag="$(git rev-parse --short HEAD)"
if [ -n "$(git status --porcelain)" ]; then
  tag="$tag-dirty"
fi

echo "Building images with tag $tag..."
docker build --quiet -t "trader-churn:$tag" .
docker build --quiet -f Dockerfile.train -t "trader-churn-train:$tag" .

# There's no image registry yet, so copy the images directly into the cluster.
echo "Loading images into minikube..."
minikube --profile "$context" image load "trader-churn:$tag"
minikube --profile "$context" image load "trader-churn-train:$tag"

# "restricted" makes the cluster refuse any pod that breaks these security rules.
kubectl --context "$context" create namespace "$namespace" \
  --dry-run=client -o yaml | kubectl --context "$context" apply -f - >/dev/null
kubectl --context "$context" label --overwrite namespace "$namespace" \
  pod-security.kubernetes.io/enforce=restricted >/dev/null

echo "Deploying..."
helm upgrade --install trader-churn charts/trader-churn \
  --kube-context "$context" \
  --namespace "$namespace" \
  --values charts/trader-churn/values-minikube.yaml \
  --set "api.image.tag=$tag" \
  --set "training.image.tag=$tag" \
  --wait --wait-for-jobs --timeout 5m

kubectl --context "$context" --namespace "$namespace" get pods
