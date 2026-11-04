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

You need [minikube](https://minikube.sigs.k8s.io/) and [Helm](https://helm.sh/).

```bash
minikube start -p trader-churn --cni=calico --cpus=4 --memory=4g --addons=metrics-server
scripts/deploy_minikube.sh
helm test trader-churn --kube-context trader-churn -n trader-churn --logs
```

The API is only reachable from inside the cluster, because it has no authentication.
