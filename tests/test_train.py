import joblib
import mlflow
import pytest

from churn.data import FEATURE_COLUMNS, generate_clients
from churn.train import EXPERIMENT_NAME, check_quality, evaluate, main, train


@pytest.fixture(autouse=True)
def isolated_mlflow(tmp_path, monkeypatch):
    """Run from a temporary folder, so tests never touch the real MLflow data."""
    monkeypatch.chdir(tmp_path)


def finished_runs(tracking_uri):
    client = mlflow.MlflowClient(tracking_uri)
    experiment = client.get_experiment_by_name(EXPERIMENT_NAME)
    return client.search_runs([experiment.experiment_id])


@pytest.fixture(scope="module")
def clients():
    return generate_clients(n_clients=5_000, seed=1)


@pytest.fixture(scope="module")
def result(clients):
    return train(clients, seed=1)


def test_model_is_much_better_than_guessing(result):
    # Random guessing scores a ROC AUC of 0.5.
    assert result.metrics["roc_auc"] > 0.7


def test_same_seed_gives_same_metrics(clients, result):
    assert train(clients, seed=1).metrics == result.metrics


def test_model_outputs_a_probability_per_client(clients, result):
    probabilities = result.model.predict_proba(clients[FEATURE_COLUMNS])[:, 1]
    assert len(probabilities) == len(clients)
    assert ((probabilities >= 0) & (probabilities <= 1)).all()


def test_model_handles_a_country_it_has_never_seen(clients, result):
    new_client = clients[FEATURE_COLUMNS].head(1).assign(country="Atlantis")
    assert len(result.model.predict_proba(new_client)) == 1


def test_command_line_saves_a_model_that_can_be_loaded(tmp_path):
    # Full size, because on small datasets the simple baseline can win.
    clients = generate_clients(n_clients=20_000, seed=1)
    data_path = tmp_path / "clients.csv"
    model_path = tmp_path / "model.joblib"
    clients.to_csv(data_path, index=False)

    main(["--data", str(data_path), "--output", str(model_path), "--seed", "1"])

    saved_model = joblib.load(model_path)
    assert len(saved_model.predict_proba(clients[FEATURE_COLUMNS].head(3))) == 3
    assert [path.name for path in tmp_path.glob("*.tmp")] == []
    assert (tmp_path / "reference.json").exists()


def test_good_model_passes_the_gate():
    assert check_quality({"roc_auc": 0.85}, {"roc_auc": 0.80}, min_roc_auc=0.80) == []


def test_model_below_the_minimum_fails_the_gate():
    failures = check_quality({"roc_auc": 0.75}, {"roc_auc": 0.70}, min_roc_auc=0.80)
    assert len(failures) == 1
    assert "below" in failures[0]


@pytest.mark.parametrize("baseline_roc_auc", [0.85, 0.90])
def test_model_that_does_not_beat_the_baseline_fails_the_gate(baseline_roc_auc):
    failures = check_quality(
        {"roc_auc": 0.85}, {"roc_auc": baseline_roc_auc}, min_roc_auc=0.80
    )
    assert len(failures) == 1
    assert "baseline" in failures[0]


def test_command_line_rejects_a_bad_model_and_does_not_save_it(tmp_path, clients):
    data_path = tmp_path / "clients.csv"
    model_path = tmp_path / "model.joblib"
    clients.to_csv(data_path, index=False)

    with pytest.raises(SystemExit) as exit_info:
        main(
            [
                "--data",
                str(data_path),
                "--output",
                str(model_path),
                "--seed",
                "1",
                "--min-roc-auc",
                "0.99",
            ]
        )

    assert exit_info.value.code == 1
    assert not model_path.exists()


def test_passing_run_is_recorded_with_a_model_that_can_be_loaded(tmp_path):
    clients = generate_clients(n_clients=20_000, seed=1)
    data_path = tmp_path / "clients.csv"
    model_path = tmp_path / "model.joblib"
    tracking_uri = f"sqlite:///{tmp_path / 'mlflow.db'}"
    clients.to_csv(data_path, index=False)

    main(
        [
            "--data",
            str(data_path),
            "--output",
            str(model_path),
            "--seed",
            "1",
            "--tracking-uri",
            tracking_uri,
        ]
    )

    [run] = finished_runs(tracking_uri)
    assert run.data.tags["quality_gate"] == "passed"
    assert run.data.metrics["roc_auc"] > run.data.metrics["baseline_roc_auc"]
    assert run.data.params["n_clients"] == "20000"

    client = mlflow.MlflowClient(tracking_uri)
    [logged_model] = client.search_logged_models([run.info.experiment_id])
    loaded_model = mlflow.sklearn.load_model(logged_model.model_uri)
    sample = clients[FEATURE_COLUMNS].head(50)
    assert (
        loaded_model.predict_proba(sample)
        == joblib.load(model_path).predict_proba(sample)
    ).all()


def test_failing_run_is_recorded_without_a_model(tmp_path, clients):
    data_path = tmp_path / "clients.csv"
    tracking_uri = f"sqlite:///{tmp_path / 'mlflow.db'}"
    clients.to_csv(data_path, index=False)

    with pytest.raises(SystemExit):
        main(
            [
                "--data",
                str(data_path),
                "--output",
                str(tmp_path / "model.joblib"),
                "--min-roc-auc",
                "0.99",
                "--tracking-uri",
                tracking_uri,
            ]
        )

    [run] = finished_runs(tracking_uri)
    assert run.data.tags["quality_gate"] == "failed"
    assert not mlflow.MlflowClient(tracking_uri).search_logged_models(
        [run.info.experiment_id]
    )


def test_drift_makes_an_old_model_worse_and_retraining_helps():
    old_model = train(generate_clients(n_clients=20_000, seed=1), seed=1).model
    drifted = generate_clients(n_clients=20_000, seed=2, drift=1.0)
    retrained = train(drifted, seed=2)
    new_clients = generate_clients(n_clients=5_000, seed=3, drift=1.0)
    features, labels = new_clients[FEATURE_COLUMNS], new_clients["churned"]

    old_score = evaluate(old_model, features, labels)["roc_auc"]
    new_score = evaluate(retrained.model, features, labels)["roc_auc"]

    assert old_score < 0.65
    assert new_score > old_score + 0.05
