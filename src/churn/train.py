"""Train the churn model and check it is good enough to use.

Run it with:
    uv run python -m churn.data
    uv run python -m churn.train
"""

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import joblib
import mlflow
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from churn.data import (
    CATEGORICAL_COLUMNS,
    FEATURE_COLUMNS,
    NUMERIC_COLUMNS,
    TARGET_COLUMN,
)
from churn.drift import REFERENCE_FILENAME, build_reference
from churn.storage import (
    MODEL_FILENAME,
    download_production,
    read_pointer,
    upload_candidate,
)

# Measured on five generated datasets: the model scored 0.82-0.84. A score below
# 0.80 means something is wrong, so that model should never be used.
MIN_ROC_AUC = 0.80

# A replacement must clearly beat the production model, not just match it by chance.
MIN_IMPROVEMENT = 0.01

EXPERIMENT_NAME = "trader-churn"

# MLflow refuses to load tree models unless their tree type is trusted, because a
# tampered file could crash the process or read memory it shouldn't. Trusting it
# is safe here only because this code creates the file itself. Never extend this
# to model files from somewhere else.
TRUSTED_MODEL_TYPES = [
    "sklearn.ensemble._hist_gradient_boosting.predictor.TreePredictor"
]


@dataclass
class TrainingResult:
    model: Pipeline
    metrics: dict[str, float]
    baseline_metrics: dict[str, float]
    # The production model's score on the same new data, if there is one.
    champion_metrics: dict[str, float] | None = None


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


def train(
    clients: pd.DataFrame, seed: int = 42, champion: Pipeline | None = None
) -> TrainingResult:
    """Fit both models on 80% of the data and score them on the other 20%.

    If a production model is given, it's scored on the same 20%, so the comparison
    is fair: both models are judged on the newest data, which neither has seen.
    """
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
        champion_metrics=(
            evaluate(champion, test_features, test_labels) if champion else None
        ),
    )


def check_quality(
    metrics: dict,
    baseline_metrics: dict,
    min_roc_auc: float = MIN_ROC_AUC,
    champion_metrics: dict | None = None,
) -> list[str]:
    """Return the reasons a model should be rejected. An empty list means it passed."""
    failures = []
    if metrics["roc_auc"] < min_roc_auc:
        failures.append(f"ROC AUC {metrics['roc_auc']:.3f} is below {min_roc_auc:.3f}")
    if metrics["roc_auc"] <= baseline_metrics["roc_auc"]:
        failures.append(
            f"ROC AUC {metrics['roc_auc']:.3f} doesn't beat the baseline "
            f"({baseline_metrics['roc_auc']:.3f})"
        )
    if (
        champion_metrics is not None
        and metrics["roc_auc"] < champion_metrics["roc_auc"] + MIN_IMPROVEMENT
    ):
        failures.append(
            f"ROC AUC {metrics['roc_auc']:.3f} doesn't beat the production model "
            f"({champion_metrics['roc_auc']:.3f}) by {MIN_IMPROVEMENT}"
        )
    return failures


def log_run(
    result: TrainingResult,
    clients: pd.DataFrame,
    args: argparse.Namespace,
    failures: list[str],
) -> None:
    """Record this run in MLflow, including runs that failed the quality gate."""
    mlflow.set_tracking_uri(args.tracking_uri)
    mlflow.set_experiment(EXPERIMENT_NAME)
    with mlflow.start_run():
        mlflow.log_params(
            {
                "seed": args.seed,
                "n_clients": len(clients),
                "min_roc_auc": args.min_roc_auc,
                "classifier": type(result.model["classifier"]).__name__,
            }
        )
        mlflow.log_metrics(result.metrics)
        mlflow.log_metrics(
            {
                f"baseline_{name}": value
                for name, value in result.baseline_metrics.items()
            }
        )
        if result.champion_metrics:
            mlflow.log_metrics(
                {
                    f"champion_{name}": value
                    for name, value in result.champion_metrics.items()
                }
            )
        mlflow.set_tag("quality_gate", "failed" if failures else "passed")
        if not failures:
            mlflow.sklearn.log_model(
                result.model,
                name="model",
                input_example=clients[FEATURE_COLUMNS].head(5),
                skops_trusted_types=TRUSTED_MODEL_TYPES,
            )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Train the churn model.")
    parser.add_argument("--data", type=Path, default=Path("data/clients.csv"))
    parser.add_argument("--output", type=Path, default=Path("models/model.joblib"))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--min-roc-auc", type=float, default=MIN_ROC_AUC)
    parser.add_argument("--tracking-uri", default="sqlite:///mlflow.db")
    parser.add_argument("--s3-bucket", help="Upload a passing model to this bucket")
    parser.add_argument(
        "--skip-if-production-exists",
        action="store_true",
        help="Do nothing if the bucket already has a production model",
    )
    parser.add_argument(
        "--compare-with-production",
        action="store_true",
        help="Only accept a model that beats the current production model",
    )
    args = parser.parse_args(argv)

    # Both options look up the production model, which lives in the bucket. Without
    # one, stop with a clear message instead of a confusing crash later on.
    if (args.skip_if_production_exists or args.compare_with_production) and (
        not args.s3_bucket
    ):
        parser.error(
            "--skip-if-production-exists and --compare-with-production need --s3-bucket"
        )

    # Deploying a new version of the code shouldn't replace the model.
    if args.skip_if_production_exists and read_pointer(args.s3_bucket) is not None:
        print("A production model already exists, so there's nothing to do")
        return

    clients = pd.read_csv(args.data)
    champion = None
    if args.compare_with_production and read_pointer(args.s3_bucket) is not None:
        # The download checks the checksums, which makes it safe to load.
        champion_dir = args.output.parent / "champion"
        download_production(args.s3_bucket, champion_dir)
        champion = joblib.load(champion_dir / MODEL_FILENAME)

    result = train(clients, args.seed, champion)
    print(f"Model:    {result.metrics}")
    print(f"Baseline: {result.baseline_metrics}")
    if result.champion_metrics:
        print(f"Current production model: {result.champion_metrics}")

    # A non-zero exit code makes the CI job fail, and the model is never saved.
    failures = check_quality(
        result.metrics,
        result.baseline_metrics,
        args.min_roc_auc,
        result.champion_metrics,
    )
    log_run(result, clients, args, failures)
    if failures:
        print("Quality gate FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        sys.exit(1)
    print("Quality gate passed")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Saved first, because the API treats the model file as "everything is ready".
    reference_path = args.output.with_name(REFERENCE_FILENAME)
    reference_path.write_text(json.dumps(build_reference(clients)))

    # Write under a temporary name, then rename, which happens all at once. An API
    # starting right now sees either no model or a whole one, never half a file.
    temporary_path = args.output.with_name(f"{args.output.name}.tmp")
    joblib.dump(result.model, temporary_path)
    temporary_path.replace(args.output)
    print(f"Saved model to {args.output}")

    if args.s3_bucket:
        candidate = upload_candidate(args.s3_bucket, args.output.parent, result.metrics)
        print(f"Uploaded {candidate}")


if __name__ == "__main__":
    main()
