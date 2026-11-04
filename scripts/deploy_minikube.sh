#!/usr/bin/env bash
# Create the model store, build both images, load them into minikube, and deploy.
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

echo "Setting up the model store..."
store_ip="$(scripts/setup_model_store.sh "$context" 2>/dev/null | tail -n 1)"

# Dummy keys for the emulator, kept in a Secret just as real ones would be.
kubectl --context "$context" --namespace "$namespace" create secret generic \
  trader-churn-aws \
  --from-literal=AWS_ACCESS_KEY_ID=test \
  --from-literal=AWS_SECRET_ACCESS_KEY=test \
  --dry-run=client -o yaml | kubectl --context "$context" apply -f - >/dev/null

echo "Deploying..."
helm upgrade --install trader-churn charts/trader-churn \
  --kube-context "$context" \
  --namespace "$namespace" \
  --values charts/trader-churn/values-minikube.yaml \
  --set "api.image.tag=$tag" \
  --set "training.image.tag=$tag" \
  --set "modelStore.endpoint=http://$store_ip:4566" \
  --set "modelStore.credentialsSecret=trader-churn-aws" \
  --set "modelStore.egressCidr=$store_ip/32" \
  --set "modelStore.egressPort=4566" \
  --wait --wait-for-jobs --timeout 5m

kubectl --context "$context" --namespace "$namespace" get pods
