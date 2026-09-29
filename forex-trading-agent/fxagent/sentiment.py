"""Turn news headlines into a per-currency sentiment score (-1 bearish .. +1 bullish).

Two scorers:
- Claude (when ANTHROPIC_API_KEY is set): reads the headlines and the
  upcoming economic calendar and scores each currency, with a one-line reason.
- Keyword scorer (always available): matches headlines to currencies and
  counts bullish/bearish words. Cruder, but free and never fails.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from .sources.calendar import Event
from .sources.news import Headline
from .universe import KEYWORDS

log = logging.getLogger(__name__)

CURRENCY_CODES = list(KEYWORDS)  # USD, EUR, JPY, GBP, AUD, CAD, CHF

BULLISH = [
    "rises", "rise", "rally", "rallies", "gains", "gain", "jumps", "surges", "climbs", "strengthens", "firmer",
    "hawkish", "rate hike", "hikes", "beats", "better than expected", "stronger than expected", "upbeat", "robust",
    "higher", "bid", "outperforms", "rebounds", "recovers", "hot inflation", "sticky inflation",
]
BEARISH = [
    "falls", "fall", "drops", "slides", "slumps", "tumbles", "plunges", "weakens", "weaker", "softer", "dovish",
    "rate cut", "cuts", "misses", "worse than expected", "weaker than expected", "downbeat", "recession",
    "lower", "offered", "underperforms", "sell-off", "selloff", "slows", "contraction", "intervention",
]


@dataclass
class CurrencyView:
    score: float = 0.0          # -1..+1
    headlines: int = 0          # how many headlines mentioned it
    reason: str = ""
    top: list[str] = field(default_factory=list)


def _mentions(text: str) -> list[str]:
    t = f" {text.lower()} "
    return [c for c, words in KEYWORDS.items() if any(w in t for w in words)]


def _pair_direction(text: str) -> dict[str, int]:
    """Headlines like 'EUR/USD rises' say base up / quote down."""
    out: dict[str, int] = {}
    m = re.search(r"\b([A-Z]{3})/([A-Z]{3})\b", text)
    if not m or m.group(1) not in KEYWORDS or m.group(2) not in KEYWORDS:
        return out
    t = text.lower()
    up = sum(w in t for w in BULLISH)
    down = sum(w in t for w in BEARISH)
    if up != down:
        sign = 1 if up > down else -1
        out[m.group(1)] = sign
        out[m.group(2)] = -sign
    return out


def keyword_scores(headlines: list[Headline]) -> dict[str, CurrencyView]:
    raw: dict[str, list[float]] = {c: [] for c in CURRENCY_CODES}
    examples: dict[str, list[str]] = {c: [] for c in CURRENCY_CODES}
    for h in headlines:
        pair = _pair_direction(h.title)
        if pair:
            for c, s in pair.items():
                raw[c].append(float(s))
                examples[c].append(h.title)
            continue
        t = h.text.lower()
        up = sum(w in t for w in BULLISH)
        down = sum(w in t for w in BEARISH)
        if up == down:
            continue
        s = (up - down) / (up + down)
        for c in _mentions(h.text):
            raw[c].append(s)
            examples[c].append(h.title)
    views = {}
    for c in CURRENCY_CODES:
        vals = raw[c]
        if not vals:
            views[c] = CurrencyView(reason="no clear news")
            continue
        # shrink toward 0 when few headlines back the view
        score = sum(vals) / len(vals) * min(1.0, len(vals) / 5)
        views[c] = CurrencyView(
            score=max(-1.0, min(1.0, score)),
            headlines=len(vals),
            reason=f"{len(vals)} headlines, keyword tone {score:+.2f}",
            top=examples[c][:3],
        )
    return views


# ---------------------------------------------------------------- Claude scorer

MODEL = "claude-opus-5-5"

SYSTEM = (
    "You are a foreign-exchange research analyst supporting an intraday trading system. "
    "You receive recent news headlines and this week's economic calendar. For each currency "
    "(USD, EUR, JPY, GBP, AUD, CAD, CHF) judge the likely direction of that currency against the "
    "others over the next few hours, based only on the evidence given. Score from -1 (strongly "
    "bearish) to +1 (strongly bullish); use 0 when the news is mixed, stale or absent. Weigh central-bank "
    "signals and data surprises most, and discount opinion pieces and already-priced moves. "
    "Give a short plain-English reason citing the specific news."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "currencies": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "enum": CURRENCY_CODES},
                    "score": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["code", "score", "reason"],
                "additionalProperties": False,
            },
        },
        "market_summary": {"type": "string"},
    },
    "required": ["currencies", "market_summary"],
    "additionalProperties": False,
}


def build_prompt(headlines: list[Headline], events: list[Event]) -> str:
    lines = ["Recent headlines (newest first, UTC):"]
    for h in headlines[:120]:
        lines.append(f"- [{h.published:%a %H:%M}] ({h.source}) {h.title}" + (f" — {h.summary[:200]}" if h.summary else ""))
    lines.append("\nEconomic calendar (next 24h, medium/high impact):")
    if not events:
        lines.append("- (none available)")
    for e in events[:40]:
        lines.append(f"- {e.when:%a %H:%M %Z} {e.currency} {e.title} [{e.impact}] forecast={e.forecast or '?'} previous={e.previous or '?'}")
    return "\n".join(lines)


def parse_claude_json(text: str) -> tuple[dict[str, CurrencyView], str]:
    data = json.loads(text)
    views = {c: CurrencyView(reason="not scored") for c in CURRENCY_CODES}
    for item in data.get("currencies", []):
        code = str(item.get("code", "")).upper()
        if code in views:
            score = max(-1.0, min(1.0, float(item.get("score", 0.0))))
            views[code] = CurrencyView(score=score, headlines=0, reason=str(item.get("reason", ""))[:300])
    return views, str(data.get("market_summary", ""))[:600]


def claude_scores(api_key: str, headlines: list[Headline], events: list[Event]) -> tuple[dict[str, CurrencyView], str] | None:
    """Returns None if Claude is unavailable or declines, so the caller falls back."""
    if not api_key or not headlines:
        return None
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key, timeout=120.0)
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=4000,
            system=SYSTEM,
            messages=[{"role": "user", "content": build_prompt(headlines, events)}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",  # re-run on a fallback model if declined
        )
        if response.stop_reason == "refusal":
            log.warning("Claude declined the news request; using keyword scorer")
            return None
        text = next((b.text for b in response.content if b.type == "text"), "")
        return parse_claude_json(text)
    except Exception as exc:  # noqa: BLE001 - research falls back, never blocks trading safety
        log.warning("Claude news scoring failed (%s); using keyword scorer", exc)
        return None


def score_news(api_key: str, headlines: list[Headline], events: list[Event]) -> tuple[dict[str, CurrencyView], str, str]:
    """Returns (views, market_summary, method)."""
    kw = keyword_scores(headlines)
    result = claude_scores(api_key, headlines, events)
    if result is None:
        return kw, "", "keywords"
    views, summary = result
    for c, v in views.items():  # keep headline counts / examples from the keyword pass
        v.headlines, v.top = kw[c].headlines, kw[c].top
    return views, summary, "claude"
