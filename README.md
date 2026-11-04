# Trader Churn MLOps

Predicts which clients of a trading broker are likely to stop trading, built as
an end-to-end MLOps project.

The data is synthetic. No real client data is used.

## Quick start

You need [uv](https://docs.astral.sh/uv/).

```bash
uv sync                                  # install dependencies
uv run ruff check .                      # check code quality
uv run python -m churn.data              # write data/clients.csv
```
