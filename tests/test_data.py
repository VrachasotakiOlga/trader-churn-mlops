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


@pytest.mark.parametrize(
    "arguments", [{"n_clients": 0}, {"drift": -0.1}, {"drift": 1.1}]
)
def test_invalid_arguments_are_rejected(arguments):
    with pytest.raises(ValueError):
        generate_clients(**arguments)
