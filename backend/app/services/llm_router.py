"""Multi-provider LLM router for AI narrative generation.

Tries providers in priority order based on available API keys.
Auto-skips providers with recent errors and retries after cooldown.
All providers are free-tier compatible — no paid key required.

Priority (runtime auto-selection):
  1. GitHub Models  — GITHUB_TOKEN       (GPT-4o-mini, free with GitHub Pro)
  2. Google Gemini  — GOOGLE_API_KEY     (Gemini 2.0 Flash, free)
  3. OpenRouter     — OPENROUTER_API_KEY (Llama-3.3-70B free tier)
  4. Qwen / Dashscope — DASHSCOPE_API_KEY (Qwen-Plus)

Cache: 30-min per (symbol, score, market_regime).
Narrative is None when all providers fail — rule-based signals still show.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from app.core.config import settings

# ── Provider registry ──────────────────────────────────────────────────────────

_PROVIDERS: list[dict[str, Any]] = [
    {
        "name": "github",
        "key_getter": lambda: settings.github_token,
        "type": "openai_compat",
        "base_url": "https://models.inference.ai.azure.com",
        "model": "gpt-4o-mini",
    },
    {
        "name": "gemini",
        "key_getter": lambda: settings.google_api_key,
        "type": "gemini",
        "model": "gemini-2.0-flash-exp",
    },
    {
        "name": "openrouter",
        "key_getter": lambda: settings.openrouter_api_key,
        "type": "openai_compat",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "meta-llama/llama-3.3-70b-instruct:free",
    },
    {
        "name": "qwen",
        "key_getter": lambda: settings.dashscope_api_key,
        "type": "openai_compat",
        "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
    },
]

# ── Error / cooldown tracking ──────────────────────────────────────────────────

_ERROR_STATE: dict[str, dict] = {}
_COOLDOWN_SECONDS = 300  # 5 min cooldown after 2 consecutive errors
_MAX_CONSECUTIVE = 2

# ── Narrative cache ────────────────────────────────────────────────────────────

_CACHE: dict[tuple, tuple[str, float]] = {}
_CACHE_TTL = 1800  # 30 minutes


def _cache_key(symbol: str, score: float, regime: str) -> tuple:
    return (symbol, round(score, 1), regime)


def _cached_narrative(key: tuple) -> str | None:
    entry = _CACHE.get(key)
    if entry and time.time() - entry[1] < _CACHE_TTL:
        return entry[0]
    return None


def _store_narrative(key: tuple, text: str) -> None:
    _CACHE[key] = (text, time.time())


# ── Provider selection ─────────────────────────────────────────────────────────

def _available_providers() -> list[dict]:
    now = time.time()
    result = []
    for p in _PROVIDERS:
        key = p["key_getter"]()
        if not key:
            continue
        state = _ERROR_STATE.get(p["name"], {})
        if (
            state.get("consecutive", 0) >= _MAX_CONSECUTIVE
            and now - state.get("last_error", 0) < _COOLDOWN_SECONDS
        ):
            continue
        result.append(p)
    return result


def _record_error(name: str) -> None:
    s = _ERROR_STATE.setdefault(name, {"consecutive": 0, "last_error": 0})
    s["consecutive"] += 1
    s["last_error"] = time.time()


def _record_success(name: str) -> None:
    _ERROR_STATE.pop(name, None)


def active_provider_names() -> list[str]:
    """List currently usable provider names (for status endpoint)."""
    return [p["name"] for p in _available_providers()]


# ── HTTP calls ─────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "You are a concise portfolio analyst. "
    "Reply in exactly 1-2 sentences. "
    "Be specific — cite numbers from the signals. "
    "No disclaimers, no markdown, no preamble."
)

_SELL_VERIFY_SYSTEM = (
    "You are a skeptical risk analyst. "
    "Your job: decide if a SELL signal is genuine or a false positive caused by short-term noise. "
    "Reply with exactly one word: SELL or WATCH. Nothing else."
)


def _build_sell_verify_prompt(rec: dict) -> str:
    reasons = "\n".join(f"  - {r}" for r in rec.get("reasons", []))
    return (
        f"Stock: {rec['symbol']} — Score {rec['score']}/10 — Confidence: {rec.get('confidence', 'unknown')}\n"
        f"Tech score: {rec.get('tech_score', 'N/A')}  Fund score: {rec.get('fund_score', 'N/A')}  "
        f"Macro score: {rec.get('macro_score', 'N/A')}  Sentiment: {rec.get('sentiment', 'N/A')}\n"
        f"Stage: {rec.get('stage')}  RSI: {rec.get('rsi')}  Regime: {rec.get('market_regime')}\n"
        f"Total return: {rec.get('total_return_pct')}%  Weight: {rec.get('weight')}%\n"
        f"Signals:\n{reasons}\n\n"
        "Is this a genuine SELL (structural deterioration) or short-term noise (WATCH)? "
        "Reply SELL or WATCH only."
    )


def _build_user_prompt(rec: dict) -> str:
    reasons = "\n".join(f"  - {r}" for r in rec.get("reasons", [])[:4])
    upside = rec.get("analyst_upside_pct")
    upside_str = f"+{upside}%" if upside and upside > 0 else (
        f"{upside}%" if upside else "N/A"
    )
    return (
        f"Stock: {rec['symbol']} ({rec.get('name', rec['symbol'])})\n"
        f"Action: {rec['action']} (score {rec['score']}/10)\n"
        f"Market regime: {rec.get('market_regime', 'unknown')}\n"
        f"Analyst upside: {upside_str}\n"
        f"Key signals:\n{reasons}\n\n"
        "Write a 1-2 sentence analyst note explaining this recommendation."
    )


async def _call_openai_compat(
    provider: dict,
    api_key: str,
    prompt: str,
    system: str = _SYSTEM_PROMPT,
    max_tokens: int = 120,
    temperature: float = 0.3,
) -> str:
    url = f"{provider['base_url']}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if provider["name"] == "openrouter":
        headers["HTTP-Referer"] = "https://aifolimizer.local"
        headers["X-Title"] = "aifolimizer"

    body = {
        "model": provider["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()


async def _call_gemini(
    api_key: str,
    model: str,
    prompt: str,
    system: str = _SYSTEM_PROMPT,
    max_tokens: int = 120,
    temperature: float = 0.3,
) -> str:
    url = (
        f"https://generativelanguage.googleapis.com/v1beta"
        f"/models/{model}:generateContent?key={api_key}"
    )
    body = {
        "contents": [
            {
                "parts": [
                    {"text": f"{system}\n\n{prompt}"}
                ]
            }
        ],
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": temperature,
        },
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            url, json=body, headers={"Content-Type": "application/json"}
        )
        resp.raise_for_status()
        data = resp.json()
        return (
            data["candidates"][0]["content"]["parts"][0]["text"].strip()
        )


async def _call_provider(
    provider: dict,
    prompt: str,
    system: str = _SYSTEM_PROMPT,
    max_tokens: int = 120,
    temperature: float = 0.3,
) -> str:
    api_key = provider["key_getter"]()
    if provider["type"] == "gemini":
        return await _call_gemini(api_key, provider["model"], prompt, system, max_tokens, temperature)
    return await _call_openai_compat(provider, api_key, prompt, system, max_tokens, temperature)


# ── Public API ─────────────────────────────────────────────────────────────────

async def generate_narrative(rec: dict) -> str | None:
    """Generate AI narrative for one recommendation. Returns None if all fail."""
    symbol = rec["symbol"]
    ck = _cache_key(symbol, rec["score"], rec.get("market_regime", ""))
    cached = _cached_narrative(ck)
    if cached:
        return cached

    prompt = _build_user_prompt(rec)

    for provider in _available_providers():
        try:
            text = await _call_provider(provider, prompt)
            if text:
                _record_success(provider["name"])
                _store_narrative(ck, text)
                return text
        except Exception as e:
            print(
                f"[llm_router] {provider['name']} failed for {symbol}: "
                f"{type(e).__name__}: {e}"
            )
            _record_error(provider["name"])

    return None


async def generate_narratives_batch(
    recommendations: list[dict],
    max_count: int = 15,
    concurrency: int = 4,
) -> dict[str, str | None]:
    """Generate narratives for top N actionable recommendations concurrently.

    Skips HOLDs unless fewer than max_count non-HOLD positions exist.
    Returns {symbol: narrative_text_or_None}.
    """
    if not _available_providers():
        return {}

    # Prioritise SELL / BUY / WATCH; fill with HOLDs if under max_count
    priority = [r for r in recommendations if r["action"] != "HOLD"]
    if len(priority) < max_count:
        remaining = max_count - len(priority)
        priority += [
            r for r in recommendations if r["action"] == "HOLD"
        ][:remaining]

    targets = priority[:max_count]
    sem = asyncio.Semaphore(concurrency)

    async def bounded(rec: dict) -> tuple[str, str | None]:
        async with sem:
            return rec["symbol"], await generate_narrative(rec)

    results = await asyncio.gather(*[bounded(r) for r in targets])
    return {sym: narr for sym, narr in results}


async def verify_sell_signal(rec: dict) -> bool:
    """Ask LLM if a SELL action is genuine or noise. Returns True = confirmed SELL.

    Only called for SELL-rated positions with low/medium confidence.
    Falls back to True (keep SELL) if no LLM provider is available.
    """
    providers = _available_providers()
    if not providers:
        return True  # no LLM — trust the rules

    prompt = _build_sell_verify_prompt(rec)

    for provider in providers:
        try:
            if provider["type"] == "gemini":
                text = await _call_gemini(
                    provider["key_getter"](),
                    provider["model"],
                    f"{_SELL_VERIFY_SYSTEM}\n\n{prompt}",
                )
            else:
                text = await _call_openai_compat_with_system(
                    provider, provider["key_getter"](), prompt, _SELL_VERIFY_SYSTEM
                )
            verdict = text.strip().upper().split()[0] if text.strip() else "SELL"
            _record_success(provider["name"])
            return verdict == "SELL"
        except Exception as e:
            print(f"[llm_router] verify_sell {rec['symbol']} via {provider['name']}: {e}")
            _record_error(provider["name"])

    return True  # all providers failed — keep SELL


async def _call_openai_compat_with_system(
    provider: dict, api_key: str, prompt: str, system: str
) -> str:
    url = f"{provider['base_url']}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if provider.get("name") == "openrouter":
        headers["HTTP-Referer"] = "https://aifolimizer.local"
        headers["X-Title"] = "aifolimizer"
    body = {
        "model": provider["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 10,
        "temperature": 0.0,
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
