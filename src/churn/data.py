"""Generate synthetic client data for a trading broker.

Each row is one client, described by their last 30 days of behaviour. The label
`churned` says whether they stopped trading afterwards.

The data is made up, which gives us something real data cannot: the `drift`
setting changes client behaviour on demand, so later steps can test whether the
monitoring notices.

Run it with:
    uv run python -m churn.data --clients 20000 --output data/clients.csv
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

FEATURE_COLUMNS = [
    "tenure_days",
    "country",
    "account_type",
    "trades_30d",
    "trades_7d",
    "days_since_last_trade",
    "deposits_30d",
    "withdrawals_30d",
]
TARGET_COLUMN = "churned"


def generate_clients(
    n_clients: int = 20_000, seed: int = 42, drift: float = 0.0
) -> pd.DataFrame:
    """Return one row per client, with the feature columns and the label.

    Args:
        n_clients: How many clients to create.
        seed: The same seed always produces exactly the same data.
        drift: How far behaviour has shifted, from 0.0 (normal) to 1.0 (a lot
            less trading and depositing).
    """
    if n_clients < 1:
        raise ValueError(f"n_clients must be at least 1, got {n_clients}")
    if not 0.0 <= drift <= 1.0:
        raise ValueError(f"drift must be between 0.0 and 1.0, got {drift}")

    # A private random generator, so nothing outside this function affects it.
    rng = np.random.default_rng(seed)

    country = rng.choice(
        ["Greece", "Cyprus", "Germany", "Brazil", "Japan"],
        size=n_clients,
        p=[0.15, 0.10, 0.20, 0.35, 0.20],
    )
    account_type = rng.choice(
        ["micro", "standard", "ultra_low"], size=n_clients, p=[0.30, 0.50, 0.20]
    )

    # Starts at 31 so that every client has a full 30-day window of behaviour.
    tenure_days = 31 + rng.exponential(scale=300, size=n_clients).astype(int)

    # Clients have different habits: the average number of trades in 30 days.
    # Drift lowers everyone's average by up to 60%.
    usual_trades = rng.choice([3.0, 12.0, 40.0], size=n_clients, p=[0.4, 0.4, 0.2])
    trades_30d = rng.poisson(usual_trades * (1 - 0.6 * drift))

    # The last 7 days are part of the last 30, so trades_7d can't exceed trades_30d.
    trades_7d = rng.binomial(trades_30d, 7 / 30)

    # This has to agree with the trade counts above:
    #   traded in the last 7 days    -> last trade was 0-6 days ago
    #   traded only earlier          -> last trade was 7-29 days ago
    #   no trades in 30 days         -> recorded as 30 ("30 or more")
    days_since_last_trade = np.where(
        trades_7d > 0,
        rng.integers(0, 7, size=n_clients),
        np.where(trades_30d > 0, rng.integers(7, 30, size=n_clients), 30),
    )

    deposits_30d = rng.poisson(2.0 * (1 - 0.5 * drift), size=n_clients)
    withdrawals_30d = rng.poisson(1.0, size=n_clients)

    # The hidden rule the model will have to learn. Booleans count as 0 or 1, so
    # each line adds its amount only for the clients it applies to.
    churn_probability = (
        0.10
        + 0.30 * (days_since_last_trade >= 14)
        + 0.15 * (trades_7d == 0)
        + 0.15 * (trades_30d < 5)
        + 0.10 * (tenure_days < 90)
        + 0.10 * (withdrawals_30d > deposits_30d)
        - 0.10 * (trades_30d >= 20)
        - 0.05 * (deposits_30d >= 3)
    )
    churn_probability = np.clip(churn_probability, 0.02, 0.90)

    # Random draw: two identical clients can still have different outcomes, as
    # in real life. That's why no model can reach 100% accuracy on this data.
    churned = (rng.random(n_clients) < churn_probability).astype(int)

    return pd.DataFrame(
        {
            "tenure_days": tenure_days,
            "country": country,
            "account_type": account_type,
            "trades_30d": trades_30d,
            "trades_7d": trades_7d,
            "days_since_last_trade": days_since_last_trade,
            "deposits_30d": deposits_30d,
            "withdrawals_30d": withdrawals_30d,
            TARGET_COLUMN: churned,
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic broker clients.")
    parser.add_argument("--clients", type=int, default=20_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--drift", type=float, default=0.0)
    parser.add_argument("--output", type=Path, default=Path("data/clients.csv"))
    args = parser.parse_args()

    clients = generate_clients(args.clients, args.seed, args.drift)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    clients.to_csv(args.output, index=False)
    print(
        f"Wrote {len(clients)} clients to {args.output} "
        f"(churn rate: {clients[TARGET_COLUMN].mean():.1%})"
    )


if __name__ == "__main__":
    main()
