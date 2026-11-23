"""Optional LLM parsing: Google Gemini (free tier) or any OpenAI-compatible endpoint.

The model must return a JSON object matching the Strategy schema. We validate with pydantic;
the caller falls back to the rule-based parser on any exception.
"""
from __future__ import annotations

import json
import re

import httpx

from ..config import get_settings
from .dsl import Strategy, strategy_json_schema

SYSTEM_PROMPT = """You convert an Indian trader's plain-English (or Hinglish) intraday/positional trading
strategy into a JSON object that matches the given JSON Schema exactly. Rules:
- Output ONLY the JSON object, no prose, no markdown fences.
- Times are 24h "HH:MM" IST. "3:15" during market hours means "15:15".
- Indicator ids are referenced in conditions: e.g. {"id":"ema9","kind":"ema","period":9}.
- Supertrend direction is "<id>.dir" (+1 green/-1 red); opening range is "<id>.high"/"<id>.low".
- Fixed-time entries use {"left":"time","op":"at_time","right":"09:20"}.
- direction: long (bullish), short (bearish), both, or neutral (straddles/strangles).
- Option modes: atm_buy, itm_buy, otm_buy, atm_sell, itm_sell, otm_sell, straddle_buy, straddle_sell,
  strangle_buy, strangle_sell. A bullish signal with a sell mode sells the PUT ("sell the opposite option").
- Indices: NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, SENSEX. Equities use NSE symbols (RELIANCE, HDFCBANK).
- Put the user's original text in "description". Do not invent risk numbers the user did not give;
  use schema defaults instead.
"""


def llm_available() -> bool:
    s = get_settings()
    return bool(s.gemini_api_key or (s.openai_base_url and s.openai_model))


def _extract_json(text: str) -> dict:
    if len(text) > 100_000:
        raise ValueError("LLM response too large")
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("LLM response contained no JSON object")
    data = json.loads(text[start: end + 1])
    if not isinstance(data, dict):
        raise ValueError("LLM response JSON is not an object")
    return data


def _user_prompt(text: str) -> str:
    return f"JSON Schema:\n{json.dumps(strategy_json_schema())}\n\nStrategy text:\n{text}"


def call_gemini(text: str) -> str:
    s = get_settings()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{s.gemini_model}:generateContent"
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": _user_prompt(text)}]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
    }
    # Key in a header, not the query string: httpx error messages and proxy logs include the URL.
    r = httpx.post(url, headers={"x-goog-api-key": s.gemini_api_key}, json=body, timeout=s.llm_timeout_seconds)
    r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def call_openai_compatible(text: str) -> str:
    s = get_settings()
    headers = {"Authorization": f"Bearer {s.openai_api_key}"} if s.openai_api_key else {}
    body = {
        "model": s.openai_model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": _user_prompt(text)}],
    }
    r = httpx.post(s.openai_base_url.rstrip("/") + "/chat/completions", json=body, headers=headers, timeout=s.llm_timeout_seconds)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def llm_parse(text: str) -> Strategy:
    s = get_settings()
    raw = call_gemini(text) if s.gemini_api_key else call_openai_compatible(text)
    data = _extract_json(raw)
    data.setdefault("description", text)
    return Strategy.model_validate(data)
