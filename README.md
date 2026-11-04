# Trader Churn MLOps

[![CI](https://github.com/VrachasotakiOlga/trader-churn-mlops/actions/workflows/ci.yml/badge.svg)](https://github.com/VrachasotakiOlga/trader-churn-mlops/actions/workflows/ci.yml)

Predicts which clients of a trading broker are likely to stop trading, built as
an end-to-end MLOps project.

The data is synthetic. No real client data is used.

## Quick start

You need [uv](https://docs.astral.sh/uv/).

```bash
uv sync                                  # install dependencies
uv run pytest                            # run the tests
uv run ruff check .                      # check code quality
uv run python -m churn.data              # write data/clients.csv
uv run python -m churn.train             # train the model and save it if it passes
uv run mlflow ui --backend-store-uri sqlite:///mlflow.db   # compare training runs
```

## Run the API in Docker

Train a model first, then:

```bash
docker build -t trader-churn .
scripts/smoke_test_image.sh trader-churn models     # check the image works
docker run --rm -p 8000:8000 -v "$PWD/models:/models:ro" trader-churn
```

The API documentation is then at http://localhost:8000/docs.

## Deploy to Kubernetes

You need [minikube](https://minikube.sigs.k8s.io/), [Helm](https://helm.sh/) and
[Terraform](https://developer.hashicorp.com/terraform). The deploy script also creates
the S3 bucket and Lambda on [MiniStack](https://ministack.org/), a local AWS emulator,
so it needs no AWS account and costs nothing.

```bash
minikube start -p trader-churn --cni=calico --cpus=4 --memory=4g --addons=metrics-server
scripts/deploy_minikube.sh
helm test trader-churn --kube-context trader-churn -n trader-churn --logs
```

The API is only reachable from inside the cluster, because it has no authentication.

To send traffic and watch it scale:

```bash
scripts/load_test_minikube.sh 120 8
kubectl --context trader-churn -n trader-churn get hpa --watch
```

## Monitoring

```bash
scripts/install_monitoring.sh                  # Prometheus, Alertmanager and Grafana
scripts/deploy_minikube.sh                     # adds the metrics, alerts and dashboard
scripts/load_test_minikube.sh 120 6 1.0        # send traffic with full drift
```

The API reports how far recent requests have moved from the training data, using the
Population Stability Index. When a feature stays above 0.25 for a minute, the
`ChurnFeatureDrift` alert fires.

## Automatic retraining

When that alert fires, Alertmanager tells a small controller, which starts a retraining
Job. The new model must beat the production model on the same new data. If it does, the
Lambda promotes it, and the controller rolls it out to the API pods.

```bash
scripts/load_test_minikube.sh 300 3 1.0        # drifted traffic starts the whole loop
scripts/test_retraining.sh trader-churn        # or test it with a fake alert

# Go back to the previous model. The controller rolls it out by itself.
AWS_ENDPOINT_URL=http://127.0.0.1:14566 AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test \
  AWS_DEFAULT_REGION=us-east-1 uv run python -m churn.storage \
  --bucket trader-churn-models --rollback
```
