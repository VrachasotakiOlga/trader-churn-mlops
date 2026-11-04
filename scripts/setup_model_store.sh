#!/usr/bin/env bash
# Start the MiniStack AWS emulator and create the S3 bucket and Lambda with Terraform.
# Usage: scripts/setup_model_store.sh <docker network of the cluster>
# Prints the emulator's address on that network as the last line.
set -euo pipefail

network="${1:?Usage: setup_model_store.sh <docker network>}"
name="trader-churn-ministack"

if [ "$(docker inspect --format '{{.State.Running}}' "$name" 2>/dev/null)" != "true" ]; then
  docker rm --force "$name" >/dev/null 2>&1 || true
  # Published on this machine only. The network lets the cluster reach it too.
  docker run --detach --name "$name" --network "$network" \
    --publish 127.0.0.1:14566:4566 \
    ministackorg/ministack:1.5.13 >/dev/null
fi

for _ in $(seq 1 30); do
  if curl --silent --fail http://127.0.0.1:14566/_ministack/health >/dev/null; then
    break
  fi
  sleep 1
done

terraform -chdir=infra init -input=false >&2
terraform -chdir=infra apply -input=false -auto-approve >&2

docker inspect --format "{{(index .NetworkSettings.Networks \"$network\").IPAddress}}" "$name"
