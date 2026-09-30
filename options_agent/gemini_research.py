"""
Deep company research with Google Gemini, grounded in live Google Search.

Needs a Gemini API key (free from https://aistudio.google.com/apikey) in the
GEMINI_API_KEY secret. A Gemini Pro chat subscription can't be called by a
program; the API key comes from the same Google account.

Each call asks Gemini to research one company as of today (latest news,
guidance, analyst moves, earnings timing, sector and macro backdrop) and to
return a verdict as JSON. The agent treats that verdict as one of two votes:
the other is its own price/news/analyst data (options_agent/signals.py), and
a trade needs both to agree.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import date

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger("options_agent.gemini")

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
LIST_URL = "https://generativelanguage.googleapis.com/v1beta/models"
# Tried in order; a model that no longer exists (404) or has no quota (429)
# falls through to the next. Google retires model names often (the 2.5 models
# were closed to new users by Sept 2026), so after these the agent asks the API
# which models it offers now (discover_models).
MODELS = [m.strip() for m in os.environ.get("GEMINI_MODELS", "gemini-3.8-flash,gemini-flash-latest,gemini-3.1-pro-preview").split(",") if m.strip()]
_SKIP_WORDS = ("image", "tts", "audio", "live", "embedding", "vision", "robotics", "computer-use", "native", "thinking-exp")

VERDICT_FIELDS = ("direction", "conviction", "thesis")


class GeminiUnavailable(RuntimeError):
    pass


class _Transient(RuntimeError):
    pass


def api_key() -> str:
    return os.environ.get("GEMINI_API_KEY", "")


def build_prompt(symbol: str, company: str, today: date, facts: dict) -> str:
    facts_text = json.dumps(facts, indent=1, default=str)
    return f"""You are a buy-side equity research analyst. Today is {today:%A %d %B %Y}.
Research {company} ({symbol}) using Google Search for the most recent information:
latest news and press releases, the last earnings report and guidance, analyst upgrades/downgrades
and price-target changes, the next earnings date, product/legal/regulatory events, the sector and
the overall market backdrop.

A trading agent is deciding whether to BUY a CALL option (expects the stock to rise) or BUY a PUT
option (expects it to fall) that expires in 30-60 days. Its own market data says:
{facts_text}

Weigh the evidence honestly. If the evidence is mixed or thin, say "neutral" - a missed trade costs
nothing, a wrong one loses money. Do not rely on the data above alone; check it against the news.

Reply with ONLY one JSON object, no other text:
{{"direction": "bullish" | "bearish" | "neutral",
  "conviction": <integer 0-100, how confident you are the stock moves that way within 30 days>,
  "expected_move_pct": <number, expected % move over 30 days, negative for down>,
  "thesis": "<2-3 sentences: why, citing the specific facts>",
  "catalysts": ["<upcoming event with date if known>", ...],
  "risks": ["<what would make this wrong>", ...],
  "next_earnings_date": "<YYYY-MM-DD or null>"}}"""


def parse_verdict(text: str) -> dict:
    """Pull the JSON verdict out of Gemini's reply and normalise it."""
    text = re.sub(r"```(?:json)?", "", text or "")
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in Gemini reply")
    raw = json.loads(text[start : end + 1])
    direction = str(raw.get("direction", "neutral")).strip().lower()
    if direction not in ("bullish", "bearish", "neutral"):
        direction = "neutral"
    try:
        conviction = int(float(raw.get("conviction", 0)))
    except (TypeError, ValueError):
        conviction = 0
    try:
        move = float(raw.get("expected_move_pct")) if raw.get("expected_move_pct") is not None else None
    except (TypeError, ValueError):
        move = None
    earnings = raw.get("next_earnings_date")
    if not (isinstance(earnings, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", earnings)):
        earnings = None
    return {
        "direction": direction,
        "conviction": max(0, min(100, conviction)),
        "expected_move_pct": move,
        "thesis": str(raw.get("thesis", "")).strip()[:800],
        "catalysts": [str(c)[:200] for c in (raw.get("catalysts") or [])][:5],
        "risks": [str(r)[:200] for r in (raw.get("risks") or [])][:5],
        "next_earnings_date": earnings,
        # Market-moving events (Fed, inflation, jobs reports...), when a prompt asks for them.
        "major_event_within_2_days": str(raw.get("major_event_within_2_days", False)).strip().lower() == "true",
        "key_events": [str(e)[:200] for e in (raw.get("key_events") or [])][:5],
    }


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=4, min=5, max=40),
    retry=retry_if_exception_type((_Transient, requests.ConnectionError, requests.Timeout)),
    reraise=True,
)
def _call(model: str, prompt: str) -> dict:
    resp = requests.post(
        API_URL.format(model=model),
        headers={"x-goog-api-key": api_key(), "Content-Type": "application/json"},
        json={
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "tools": [{"google_search": {}}],
            "generationConfig": {"temperature": 0.2},
        },
        timeout=180,
    )
    if resp.status_code in (500, 502, 503, 504):
        raise _Transient(f"Gemini {resp.status_code}: {resp.text[:200]}")
    if resp.status_code in (404, 429):  # model retired, or its quota is spent: try the next model
        raise LookupError(f"model {model} not available ({resp.status_code}): {resp.text[:200]}")
    if resp.status_code >= 400:
        raise GeminiUnavailable(f"Gemini {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def _sources(payload: dict) -> list[dict]:
    cand = (payload.get("candidates") or [{}])[0]
    chunks = (cand.get("groundingMetadata") or {}).get("groundingChunks") or []
    seen, out = set(), []
    for ch in chunks:
        web = ch.get("web") or {}
        uri, title = web.get("uri"), web.get("title") or web.get("domain") or ""
        if uri and uri not in seen:
            seen.add(uri)
            out.append({"title": title, "url": uri})
    return out[:8]


def research(symbol: str, company: str, today: date, facts: dict) -> dict:
    """Returns the verdict dict plus "model" and "sources". Raises
    GeminiUnavailable when no key is set or every model fails."""
    return ask(build_prompt(symbol, company, today, facts), symbol)


def rank_models(names: list[str]) -> list[str]:
    """Text models worth trying, best first: flash (fast, generous free quota),
    then pro, newest version first; flash-lite last."""
    def version(n: str) -> float:
        m = re.search(r"gemini-(\d+(?:\.\d+)?)", n)
        return float(m.group(1)) if m else 0.0

    usable = [n for n in names if n.startswith("gemini-") and not any(w in n for w in _SKIP_WORDS)]
    tier = lambda n: 0 if "flash" in n and "lite" not in n else 1 if "pro" in n else 2  # noqa: E731
    return sorted(usable, key=lambda n: (tier(n), -version(n), "preview" in n or "exp" in n, n))


def discover_models() -> list[str]:
    """Models this key can call for generateContent, ranked; [] if the list can't be read."""
    try:
        resp = requests.get(LIST_URL, headers={"x-goog-api-key": api_key()}, params={"pageSize": 200}, timeout=30)
        resp.raise_for_status()
        names = [m["name"].removeprefix("models/") for m in resp.json().get("models") or []
                 if "generateContent" in (m.get("supportedGenerationMethods") or [])]
        return rank_models(names)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not list Gemini models: %s", exc)
        return []


_working_model: list[str] = []  # remembered for the rest of the run once one answers


def ask(prompt: str, label: str) -> dict:
    """Send a research prompt whose reply is the verdict JSON above."""
    if not api_key():
        raise GeminiUnavailable("GEMINI_API_KEY is not set")
    symbol = label
    last_error: Exception | None = None
    tried: set[str] = set()

    def candidates():
        yield from _working_model + MODELS
        yield from discover_models()[:6]

    for model in candidates():
        if model in tried:
            continue
        tried.add(model)
        try:
            payload = _call(model, prompt)
            parts = ((payload.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
            text = "".join(p.get("text", "") for p in parts)
            verdict = parse_verdict(text)
            verdict.update(model=model, sources=_sources(payload))
            logger.info("Gemini (%s) on %s: %s, conviction %d", model, symbol, verdict["direction"], verdict["conviction"])
            _working_model[:] = [model]
            return verdict
        except (LookupError, ValueError, json.JSONDecodeError, _Transient) as exc:
            logger.warning("Gemini %s failed for %s: %s", model, symbol, exc)
            last_error = exc
    raise GeminiUnavailable(f"all Gemini models failed for {symbol}: {last_error}")
