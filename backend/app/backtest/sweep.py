"""Parameter sweep: timeframes x option modes x entry-window starts (like the reference video)."""
from __future__ import annotations

import itertools
from typing import Iterable

import pandas as pd

from ..data.candles import resample
from ..strategy.dsl import Strategy
from .costs import CostConfig
from .engine import run_backtest


def sweep(
    strategy: Strategy,
    base_candles: pd.DataFrame,
    timeframes: Iterable[str] = ("5m", "15m", "30m"),
    modes: Iterable[str] | None = ("atm_buy", "itm_buy", "atm_sell"),
    entry_starts: Iterable[str] = ("09:30",),
    cost_cfg: CostConfig | None = None,
    data_source: str = "synthetic",
    sort_by: str = "net_pnl",
) -> list[dict]:
    rows = []
    mode_list = list(modes) if (modes and strategy.instrument_type == "option") else [None]
    for tf, mode, start in itertools.product(list(timeframes), mode_list, list(entry_starts)):
        spec = strategy.model_dump()
        spec["timeframe"] = tf
        spec["entry_window"]["start"] = start
        if mode:
            spec["option"]["mode"] = mode
        try:
            s = Strategy.model_validate(spec)
            res = run_backtest(s, resample(base_candles, tf), data_source, cost_cfg=cost_cfg)
            m = res.metrics
            rows.append({
                "timeframe": tf, "mode": mode, "entry_start": start, "trades": m["trades"], "win_rate": m["win_rate"],
                "gross_pnl": m["gross_pnl"], "net_pnl": m["net_pnl"], "profit_factor": m["profit_factor"],
                "max_drawdown": m["max_drawdown"], "expectancy": m["expectancy"], "whipsaws": m["whipsaw"]["count"],
                "total_costs": m["total_costs"],
            })
        except Exception as e:  # keep sweeping; report the failing combo
            rows.append({"timeframe": tf, "mode": mode, "entry_start": start, "error": str(e)})
    return sorted(rows, key=lambda r: (r.get(sort_by) is None, -(r.get(sort_by) or 0)))
