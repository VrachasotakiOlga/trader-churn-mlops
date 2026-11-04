import joblib
import pytest

from churn.data import FEATURE_COLUMNS, generate_clients
from churn.train import main, train


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


def test_command_line_saves_a_model_that_can_be_loaded(tmp_path, clients):
    data_path = tmp_path / "clients.csv"
    model_path = tmp_path / "model.joblib"
    clients.to_csv(data_path, index=False)

    main(["--data", str(data_path), "--output", str(model_path), "--seed", "1"])

    saved_model = joblib.load(model_path)
    assert len(saved_model.predict_proba(clients[FEATURE_COLUMNS].head(3))) == 3
