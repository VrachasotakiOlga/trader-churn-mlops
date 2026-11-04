"""Detect when the clients we're scoring stop looking like the training data.

This uses the Population Stability Index (PSI). Split a feature into bins using the
training data, then compare the share of values in each bin, then and now. As a rule
of thumb: below 0.1 is stable, 0.1-0.25 is a moderate shift, above 0.25 is a large one.

We can't wait for real answers to check the model: whether a client churned is only
known weeks later. Inputs are available immediately, so they're the early warning.
"""

from collections import deque

import numpy as np
import pandas as pd

from churn.data import NUMERIC_COLUMNS

REFERENCE_FILENAME = "reference.json"

# Avoids dividing by zero, or taking the log of zero, for empty bins.
EPSILON = 1e-4


def build_reference(clients: pd.DataFrame, n_bins: int = 10) -> dict:
    """Record the shape of each numeric feature in the training data."""
    reference = {}
    for feature in NUMERIC_COLUMNS:
        values = clients[feature].to_numpy()
        # Count data repeats a lot, so some quantiles coincide. unique removes those.
        edges = np.unique(np.quantile(values, np.linspace(0, 1, n_bins + 1)[1:-1]))
        reference[feature] = {
            "edges": edges.tolist(),
            "proportions": _proportions(values, edges).tolist(),
        }
    return reference


def _proportions(values: np.ndarray, edges: np.ndarray) -> np.ndarray:
    counts = np.bincount(np.searchsorted(edges, values), minlength=len(edges) + 1)
    return counts / len(values)


def psi(expected: np.ndarray, actual: np.ndarray) -> float:
    """Population Stability Index: 0 means the two distributions are identical."""
    expected = np.clip(expected, EPSILON, None)
    actual = np.clip(actual, EPSILON, None)
    return float(np.sum((actual - expected) * np.log(actual / expected)))


class DriftMonitor:
    """Keep the most recent requests and score them against the training data."""

    def __init__(
        self, reference: dict, window_size: int = 1000, min_samples: int = 200
    ) -> None:
        self.reference = reference
        self.min_samples = min_samples
        # deque drops the oldest value by itself, so memory use stays fixed.
        self.windows = {
            feature: deque(maxlen=window_size) for feature in self.reference
        }

    def observe(self, client: dict) -> None:
        for feature, window in self.windows.items():
            window.append(client[feature])

    def sample_count(self) -> int:
        return min((len(window) for window in self.windows.values()), default=0)

    def scores(self) -> dict[str, float]:
        """PSI per feature. Empty until there's enough data to be meaningful."""
        if self.sample_count() < self.min_samples:
            return {}
        scores = {}
        for feature, window in self.windows.items():
            details = self.reference[feature]
            actual = _proportions(np.array(window), np.array(details["edges"]))
            scores[feature] = psi(np.array(details["proportions"]), actual)
        return scores
