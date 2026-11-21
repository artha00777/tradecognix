"""Strategy DSL: the single structured representation every part of TradeCognix shares.

Plain English -> (parser) -> Strategy -> backtest engine / live engine / explain.

Operands in conditions are either numbers or references:
  close | open | high | low | hl2           price fields of the signal timeframe
  <indicator id>                            e.g. "ema9", "rsi14", "vwap", "st"
  <indicator id>.dir                        supertrend direction (+1 green / -1 red)
  <indicator id>.high | <indicator id>.low  opening-range high / low
  time                                      with op "at_time" and a "HH:MM" right operand
"""
from __future__ import annotations

import re
from typing import Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator

TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
PRICE_FIELDS = {"close", "open", "high", "low", "hl2"}

Timeframe = Literal["1m", "3m", "5m", "10m", "15m", "30m", "1h", "1d"]
OptionMode = Literal[
    "atm_buy", "itm_buy", "otm_buy",
    "atm_sell", "itm_sell", "otm_sell",
    "straddle_buy", "straddle_sell", "strangle_buy", "strangle_sell",
]
Op = Literal["crosses_above", "crosses_below", "gt", "lt", "gte", "lte", "at_time"]

INDEX_UNDERLYINGS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"}


def _check_time(v: str) -> str:
    if not TIME_RE.match(v):
        raise ValueError(f"time must be HH:MM (24h), got {v!r}")
    return v


class IndicatorSpec(BaseModel):
    id: str = Field(..., description="Reference name used in conditions, e.g. ema9")
    kind: Literal["ema", "sma", "rsi", "vwap", "atr", "supertrend", "orb"]
    period: Optional[int] = Field(None, ge=1, le=500)
    multiplier: Optional[float] = Field(None, gt=0, le=20)
    source: Literal["close", "open", "high", "low", "hl2"] = "close"
    start: Optional[str] = None  # orb window start
    end: Optional[str] = None  # orb window end

    @model_validator(mode="after")
    def _defaults(self):
        if self.kind in ("ema", "sma") and self.period is None:
            raise ValueError(f"{self.kind} needs a period")
        if self.kind == "rsi" and self.period is None:
            self.period = 14
        if self.kind == "atr" and self.period is None:
            self.period = 14
        if self.kind == "supertrend":
            self.period = self.period or 10
            self.multiplier = self.multiplier or 3.0
        if self.kind == "orb":
            self.start = _check_time(self.start or "09:15")
            self.end = _check_time(self.end or "09:30")
        return self


class Condition(BaseModel):
    left: str
    op: Op
    right: Union[float, str]

    @model_validator(mode="after")
    def _time(self):
        if self.op == "at_time":
            self.left = "time"
            self.right = _check_time(str(self.right))
        return self

    def mirrored(self) -> "Condition":
        """Opposite-direction version (crosses_above <-> crosses_below, gt <-> lt).

        Supertrend direction crossing 0 and RSI levels are mirrored around their neutral point.
        """
        swap = {"crosses_above": "crosses_below", "crosses_below": "crosses_above", "gt": "lt", "lt": "gt", "gte": "lte", "lte": "gte"}
        if self.op == "at_time":
            return self.model_copy()
        right = self.right
        if isinstance(right, (int, float)) and self.left.startswith("rsi"):
            right = 100 - float(right)
        return Condition(left=self.left, op=swap[self.op], right=right)


class EntryRules(BaseModel):
    long: list[Condition] = Field(default_factory=list, description="Bullish signal conditions")
    short: list[Condition] = Field(default_factory=list, description="Bearish signal conditions")
    logic: Literal["all", "any"] = "all"


class ExitRules(BaseModel):
    long: list[Condition] = Field(default_factory=list, description="Exit a bullish position when these hold")
    short: list[Condition] = Field(default_factory=list, description="Exit a bearish position when these hold")
    on_opposite_signal: bool = True
    stop_loss_pct: Optional[float] = Field(None, gt=0, le=100, description="% of entry premium/price, per leg")
    target_pct: Optional[float] = Field(None, gt=0, le=1000)
    trailing_sl_pct: Optional[float] = Field(None, gt=0, le=100)


class TimeWindow(BaseModel):
    start: str = "09:15"
    end: str = "15:00"

    @field_validator("start", "end")
    @classmethod
    def _times(cls, v: str) -> str:
        return _check_time(v)

    @model_validator(mode="after")
    def _order(self):
        if self.start > self.end:
            raise ValueError("entry window start must be before end")
        return self


class OptionSpec(BaseModel):
    mode: OptionMode = "atm_buy"
    strike_steps: int = Field(1, ge=0, le=20, description="Steps away from ATM for ITM/OTM/strangle")
    expiry: Literal["weekly", "monthly"] = "weekly"

    @property
    def side(self) -> str:
        return "sell" if self.mode.endswith("_sell") else "buy"


class RiskSpec(BaseModel):
    lots: int = Field(1, ge=1, le=500, description="Lots for F&O, shares for equity")
    max_trades_per_day: int = Field(3, ge=1, le=100)
    daily_loss_cap: Optional[float] = Field(None, gt=0, description="Stop trading for the day after this net loss, INR")
    capital: float = Field(500000.0, gt=0)


class Strategy(BaseModel):
    name: str = "Untitled strategy"
    description: str = ""
    underlying: str = "NIFTY"
    instrument_type: Literal["option", "future", "equity", "index"] = "option"
    exchange: Literal["NSE", "BSE"] = "NSE"
    timeframe: Timeframe = "5m"
    indicators: list[IndicatorSpec] = Field(default_factory=list)
    direction: Literal["long", "short", "both", "neutral"] = "long"
    entry: EntryRules = Field(default_factory=EntryRules)
    exit: ExitRules = Field(default_factory=ExitRules)
    entry_window: TimeWindow = Field(default_factory=TimeWindow)
    hard_exit: str = "15:15"
    option: Optional[OptionSpec] = None
    risk: RiskSpec = Field(default_factory=RiskSpec)
    intraday: bool = True

    @field_validator("hard_exit")
    @classmethod
    def _hard_exit(cls, v: str) -> str:
        return _check_time(v)

    @field_validator("underlying")
    @classmethod
    def _upper(cls, v: str) -> str:
        return re.sub(r"\s+", "", v.upper())

    @model_validator(mode="after")
    def _consistency(self):
        if self.instrument_type == "option" and self.option is None:
            self.option = OptionSpec()
        if self.instrument_type != "option":
            self.option = None
        if self.underlying == "SENSEX":
            self.exchange = "BSE"
        if self.option and self.option.mode.startswith(("straddle", "strangle")):
            self.direction = "neutral"
        ids = {i.id for i in self.indicators}
        if len(ids) != len(self.indicators):
            raise ValueError("indicator ids must be unique")
        for c in self.all_conditions():
            for operand in (c.left, c.right):
                if isinstance(operand, str) and c.op != "at_time":
                    if _is_number(operand):
                        continue
                    base = operand.split(".")[0]
                    if base not in PRICE_FIELDS and base not in ids:
                        raise ValueError(f"condition references unknown operand {operand!r}")
        if not (self.entry.long or self.entry.short):
            raise ValueError("strategy needs at least one entry condition")
        if self.hard_exit <= self.entry_window.start:
            raise ValueError("hard exit must be after entry window start")
        return self

    def all_conditions(self) -> list[Condition]:
        return [*self.entry.long, *self.entry.short, *self.exit.long, *self.exit.short]

    def long_conditions(self) -> list[Condition]:
        return self.entry.long or ([c.mirrored() for c in self.entry.short] if self.direction == "both" else [])

    def short_conditions(self) -> list[Condition]:
        return self.entry.short or ([c.mirrored() for c in self.entry.long] if self.direction == "both" else [])

    def opposite_of_long(self) -> list[Condition]:
        return self.entry.short or [c.mirrored() for c in self.entry.long]

    def opposite_of_short(self) -> list[Condition]:
        return self.entry.long or [c.mirrored() for c in self.entry.short]

    @property
    def is_index(self) -> bool:
        return self.underlying in INDEX_UNDERLYINGS


def _is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def strategy_json_schema() -> dict:
    return Strategy.model_json_schema()
