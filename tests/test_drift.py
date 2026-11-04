import numpy as np
import pytest

from churn.data import NUMERIC_COLUMNS, generate_clients
from churn.drift import DriftMonitor, build_reference, psi


@pytest.fixture(scope="module")
def reference():
    return build_reference(generate_clients(n_clients=20_000, seed=1))


def monitor_after(reference, clients, **options):
    monitor = DriftMonitor(reference, **options)
    for client in clients.to_dict("records"):
        monitor.observe(client)
    return monitor


def test_psi_is_zero_for_identical_distributions():
    assert psi(np.array([0.5, 0.5]), np.array([0.5, 0.5])) == 0


def test_psi_grows_as_distributions_move_apart():
    expected = np.array([0.5, 0.5])
    small = psi(expected, np.array([0.55, 0.45]))
    large = psi(expected, np.array([0.9, 0.1]))
    assert 0 < small < large


def test_psi_handles_empty_bins():
    assert np.isfinite(psi(np.array([0.5, 0.5, 0.0]), np.array([0.0, 0.5, 0.5])))


def test_reference_covers_every_numeric_feature(reference):
    assert set(reference) == set(NUMERIC_COLUMNS)
    for details in reference.values():
        assert len(details["proportions"]) == len(details["edges"]) + 1
        assert sum(details["proportions"]) == pytest.approx(1)


def test_normal_traffic_stays_below_the_alert_level(reference):
    clients = generate_clients(n_clients=1_000, seed=2)
    assert max(monitor_after(reference, clients).scores().values()) < 0.1


def test_drifted_traffic_goes_above_the_alert_level(reference):
    clients = generate_clients(n_clients=1_000, seed=2, drift=1.0)
    assert monitor_after(reference, clients).scores()["trades_30d"] > 0.25


def test_no_scores_until_there_is_enough_data(reference):
    clients = generate_clients(n_clients=50, seed=2)
    assert monitor_after(reference, clients, min_samples=200).scores() == {}


def test_window_only_keeps_recent_requests(reference):
    clients = generate_clients(n_clients=500, seed=2)
    assert monitor_after(reference, clients, window_size=100).sample_count() == 100
