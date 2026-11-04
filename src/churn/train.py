"""Train the churn model and compare it with a simple baseline.

Run it with:
    uv run python -m churn.data
    uv run python -m churn.train
"""

import argparse
from dataclasses import dataclass
from pathlib import Path

import joblib
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from churn.data import FEATURE_COLUMNS, TARGET_COLUMN

CATEGORICAL_COLUMNS = ["country", "account_type"]
NUMERIC_COLUMNS = [c for c in FEATURE_COLUMNS if c not in CATEGORICAL_COLUMNS]


@dataclass
class TrainingResult:
    model: Pipeline
    metrics: dict[str, float]
    baseline_metrics: dict[str, float]


def build_preprocessor() -> ColumnTransformer:
    """Turn the raw columns into the numbers a model can use."""
    return ColumnTransformer(
        [
            (
                "categorical",
                # Countries the model hasn't seen become all zeros, not a crash.
                OneHotEncoder(handle_unknown="ignore", sparse_output=False),
                CATEGORICAL_COLUMNS,
            ),
            ("numeric", StandardScaler(), NUMERIC_COLUMNS),
        ]
    )


def build_baseline() -> Pipeline:
    """A simple model. The real model has to do better to justify itself."""
    return Pipeline(
        [
            ("preprocess", build_preprocessor()),
            ("classifier", LogisticRegression(max_iter=1000)),
        ]
    )


def build_model(seed: int) -> Pipeline:
    """The model we actually want to use."""
    return Pipeline(
        [
            ("preprocess", build_preprocessor()),
            ("classifier", HistGradientBoostingClassifier(random_state=seed)),
        ]
    )


def evaluate(model: Pipeline, features: pd.DataFrame, labels: pd.Series) -> dict:
    """Score a model. Both metrics use the probability of churn, not yes/no."""
    churn_probability = model.predict_proba(features)[:, 1]
    return {
        "roc_auc": float(roc_auc_score(labels, churn_probability)),
        "average_precision": float(average_precision_score(labels, churn_probability)),
    }


def train(clients: pd.DataFrame, seed: int = 42) -> TrainingResult:
    """Fit both models on 80% of the data and score them on the other 20%."""
    train_features, test_features, train_labels, test_labels = train_test_split(
        clients[FEATURE_COLUMNS],
        clients[TARGET_COLUMN],
        test_size=0.2,
        random_state=seed,
        # Keep the same share of churned clients on both sides of the split.
        stratify=clients[TARGET_COLUMN],
    )

    baseline = build_baseline().fit(train_features, train_labels)
    model = build_model(seed).fit(train_features, train_labels)

    return TrainingResult(
        model=model,
        metrics=evaluate(model, test_features, test_labels),
        baseline_metrics=evaluate(baseline, test_features, test_labels),
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Train the churn model.")
    parser.add_argument("--data", type=Path, default=Path("data/clients.csv"))
    parser.add_argument("--output", type=Path, default=Path("models/model.joblib"))
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)

    result = train(pd.read_csv(args.data), args.seed)
    print(f"Model:    {result.metrics}")
    print(f"Baseline: {result.baseline_metrics}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(result.model, args.output)
    print(f"Saved model to {args.output}")


if __name__ == "__main__":
    main()
