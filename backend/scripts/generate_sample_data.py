"""Generate synthetic NSE index 5-minute candles (CSV) so the demo runs without a broker.

    python -m scripts.generate_sample_data --symbol NIFTY --days 120 --out data/nifty_5m_synthetic.csv

NOT real market data. Use a connected broker (e.g. Angel One getCandleData) for real backtests.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from app.data.synthetic import generate_candles, save_csv


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", default="NIFTY")
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    out = Path(a.out or f"data/{a.symbol.lower()}_5m_synthetic.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    df = generate_candles(a.symbol, days=a.days, seed=a.seed)
    save_csv(df, str(out))
    print(f"wrote {len(df)} bars ({df.index[0]} -> {df.index[-1]}) to {out}")


if __name__ == "__main__":
    main()
