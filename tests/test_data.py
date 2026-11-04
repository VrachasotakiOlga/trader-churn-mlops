import pandas as pd
import pytest

from churn.data import FEATURE_COLUMNS, TARGET_COLUMN, generate_clients


@pytest.fixture(scope="module")
def clients() -> pd.DataFrame:
    """One dataset shared by the tests below, so it's only generated once."""
    return generate_clients(n_clients=20_000, seed=1)


def test_has_expected_columns_and_size(clients):
    assert list(clients.columns) == FEATURE_COLUMNS + [TARGET_COLUMN]
    assert len(clients) == 20_000


def test_same_seed_gives_same_data():
    first = generate_clients(n_clients=100, seed=7)
    second = generate_clients(n_clients=100, seed=7)
    pd.testing.assert_frame_equal(first, second)


def test_different_seed_gives_different_data():
    first = generate_clients(n_clients=100, seed=7)
    second = generate_clients(n_clients=100, seed=8)
    assert not first.equals(second)


def test_recent_trades_never_exceed_monthly_trades(clients):
    assert (clients["trades_7d"] <= clients["trades_30d"]).all()


def test_last_trade_date_agrees_with_trade_counts(clients):
    days = clients["days_since_last_trade"]
    traded_this_week = clients["trades_7d"] > 0
    traded_this_month = clients["trades_30d"] > 0

    assert days[traded_this_week].between(0, 6).all()
    assert days[~traded_this_week & traded_this_month].between(7, 29).all()
    assert (days[~traded_this_month] == 30).all()


def test_churn_rate_is_realistic(clients):
    assert 0.10 < clients[TARGET_COLUMN].mean() < 0.60


def test_inactive_clients_churn_more_than_active_clients(clients):
    inactive = clients[clients["days_since_last_trade"] >= 14]
    active = clients[clients["days_since_last_trade"] < 7]
    assert inactive[TARGET_COLUMN].mean() > active[TARGET_COLUMN].mean() + 0.2


def test_drift_lowers_trading_and_raises_churn(clients):
    drifted = generate_clients(n_clients=20_000, seed=1, drift=1.0)
    assert drifted["trades_30d"].mean() < clients["trades_30d"].mean() * 0.5
    assert drifted[TARGET_COLUMN].mean() > clients[TARGET_COLUMN].mean()


@pytest.mark.parametrize(
    "arguments", [{"n_clients": 0}, {"drift": -0.1}, {"drift": 1.1}]
)
def test_invalid_arguments_are_rejected(arguments):
    with pytest.raises(ValueError):
        generate_clients(**arguments)
