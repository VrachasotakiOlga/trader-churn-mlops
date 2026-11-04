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
```
