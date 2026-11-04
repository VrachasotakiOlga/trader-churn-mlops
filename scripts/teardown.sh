#!/usr/bin/env bash
# Delete the local cluster, the AWS emulator and everything in them.
# Usage: scripts/teardown.sh
set -euo pipefail

docker rm --force trader-churn-ministack >/dev/null 2>&1 || true
minikube delete --profile trader-churn
rm -f infra/terraform.tfstate infra/terraform.tfstate.backup
echo "Everything is deleted."
