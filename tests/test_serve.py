import joblib
import pytest
from fastapi.testclient import TestClient

from churn.data import FEATURE_COLUMNS, generate_clients
from churn.serve import Client, app
from churn.train import build_model

ACTIVE_CLIENT = {
    "tenure_days": 400,
    "country": "Greece",
    "account_type": "standard",
    "trades_30d": 40,
    "trades_7d": 10,
    "days_since_last_trade": 1,
    "deposits_30d": 4,
    "withdrawals_30d": 0,
}
INACTIVE_CLIENT = ACTIVE_CLIENT | {
    "tenure_days": 40,
    "trades_30d": 0,
    "trades_7d": 0,
    "days_since_last_trade": 30,
    "deposits_30d": 0,
    "withdrawals_30d": 2,
}


@pytest.fixture(scope="module")
def model_file(tmp_path_factory):
    clients = generate_clients(n_clients=3_000, seed=1)
    model = build_model(seed=1).fit(clients[FEATURE_COLUMNS], clients["churned"])
    path = tmp_path_factory.mktemp("models") / "model.joblib"
    joblib.dump(model, path)
    return path


@pytest.fixture
def api(model_file, monkeypatch):
    monkeypatch.setenv("MODEL_PATH", str(model_file))
    # Using "with" runs the startup code that loads the model.
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def api_without_model(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_PATH", str(tmp_path / "missing.joblib"))
    with TestClient(app) as test_client:
        yield test_client


def test_request_fields_match_what_the_model_expects():
    assert set(Client.model_fields) == set(FEATURE_COLUMNS)


def test_health_checks_pass_when_the_model_is_loaded(api):
    assert api.get("/healthz").status_code == 200
    assert api.get("/readyz").status_code == 200


def test_predict_returns_a_probability(api):
    response = api.post("/predict", json=ACTIVE_CLIENT)
    assert response.status_code == 200
    assert 0 <= response.json()["churn_probability"] <= 1


def test_inactive_client_is_riskier_than_active_client(api):
    inactive = api.post("/predict", json=INACTIVE_CLIENT).json()
    active = api.post("/predict", json=ACTIVE_CLIENT).json()
    assert inactive["churn_probability"] > active["churn_probability"]


def test_predict_accepts_a_country_the_model_has_never_seen(api):
    response = api.post("/predict", json=ACTIVE_CLIENT | {"country": "Atlantis"})
    assert response.status_code == 200


@pytest.mark.parametrize(
    "bad_fields",
    [
        {"trades_30d": -1},
        {"trades_7d": 41},
        {"days_since_last_trade": 31},
        {"account_type": "platinum"},
        {"country": ""},
        {"tenure_days": "a lot"},
        {"unexpected_field": 1},
    ],
)
def test_predict_rejects_bad_input(api, bad_fields):
    assert api.post("/predict", json=ACTIVE_CLIENT | bad_fields).status_code == 422


def test_predict_rejects_a_missing_field(api):
    incomplete = {k: v for k, v in ACTIVE_CLIENT.items() if k != "country"}
    assert api.post("/predict", json=incomplete).status_code == 422


def test_without_a_model_it_is_alive_but_not_ready(api_without_model):
    assert api_without_model.get("/healthz").status_code == 200
    assert api_without_model.get("/readyz").status_code == 503
    assert api_without_model.post("/predict", json=ACTIVE_CLIENT).status_code == 503
