"""LLM-powered signal explainer service.

Architecture
------------
Primary   → Groq  (llama-3.3-70b-versatile, 30 RPM / 14 400/day free)
Fallback  → Gemini (gemini-2.0-flash, 15 RPM / 1 500/day free)
Last resort → Local GGUF model via llama-cpp-python (unlimited, CPU-only)
Disabled  → return None immediately

The cascade is fully transparent to callers: ``explain()`` always returns
a plain ``str | None``.  Failures are logged but never raised — an
explanation is *enrichment*, never required for signal delivery.

Guardrails
----------
- Prompt explicitly instructs: describe data, do NOT advise to buy or sell.
- Regex post-filter strips any advisory language that slips through.
- Output is capped at 3 sentences / ~80 words.
- All API keys are read from settings at call-time (no module-level init).
"""
from __future__ import annotations

import re
import time
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)

# ── Advisory-language guardrail ───────────────────────────────────────────────
_ADVISORY_RE = re.compile(
    r"\b(you should|i recommend|i suggest|consider buying|consider selling"
    r"|buy now|sell now|take a position|invest in|avoid this stock)\b",
    re.IGNORECASE,
)

_FALLBACK_MSG = (
    "Explanation unavailable — please review the raw signal data directly."
)


def _build_prompt(
    symbol: str,
    company_name: str,
    signal_type: str,
    confidence: float,
    features: dict[str, Any],
    headlines: list[str],
    macro_regime: str,
    macro_events: list[str],
) -> str:
    """Construct the LLM prompt from signal context."""
    rsi  = features.get("rsi14", features.get("rsi_14", "N/A"))
    macd = features.get("macd", "N/A")
    macd_sig = features.get("macd_signal", "N/A")
    bb_upper = features.get("bb_upper", "N/A")
    bb_lower = features.get("bb_lower", "N/A")
    close    = features.get("close", "N/A")
    anomaly  = features.get("anomaly_score", None)
    ml_prob  = features.get("ml_probability", None)
    sentiment_score = features.get("sentiment_score", None)
    blend    = features.get("blend_score", None)

    # RSI context
    if isinstance(rsi, float):
        rsi_ctx = "overbought" if rsi > 70 else ("oversold" if rsi < 30 else "neutral")
        rsi_str = f"{rsi:.1f} ({rsi_ctx})"
    else:
        rsi_str = str(rsi)

    news_block = "\n".join(f"  - {h}" for h in headlines[:3]) if headlines else "  - No recent headlines available"
    macro_block = "\n".join(f"  - {e}" for e in macro_events[:3]) if macro_events else "  - No macro events"

    ml_block = ""
    if ml_prob is not None:
        ml_block = f"- ML model probability (BUY): {ml_prob:.1%}"
    if blend is not None:
        ml_block += f"\n- Blended ensemble score: {blend:.3f}"

    anomaly_block = ""
    if anomaly is not None:
        anomaly_ctx = "unusual pattern detected" if anomaly > 0.8 else "normal"
        anomaly_block = f"- Anomaly score: {anomaly:.2f} ({anomaly_ctx})"

    macro_regime_str = {
        "risk_off": "RISK OFF — bearish macro (BUY confidence reduced by macro filter)",
        "risk_on":  "RISK ON — bullish macro tailwind",
        "neutral":  "Neutral",
    }.get(macro_regime, "Neutral")

    return f"""System: You are a concise financial data analyst. Your role is to explain what the AI model's data shows — factually and objectively. You MUST NOT recommend buying or selling. You MUST NOT use advisory language. Describe the data only. Limit your response to 2-3 sentences.

User: An AI trading model generated a {signal_type} signal for {symbol} ({company_name}) with {confidence:.0%} confidence.

Technical indicators (most recent daily close):
- RSI(14): {rsi_str}
- MACD line: {macd}, Signal line: {macd_sig}
- Bollinger Bands: Upper={bb_upper}, Lower={bb_lower}, Close={close}
{ml_block}
{anomaly_block}

FinBERT news sentiment score: {sentiment_score if sentiment_score is not None else "N/A"} (scale: -1 negative to +1 positive)
Recent company headlines:
{news_block}

Macro market regime: {macro_regime_str}
Key macro events:
{macro_block}

Explain in 2-3 sentences what the data pattern shows that produced this {signal_type} signal."""


def _post_filter(text: str) -> Optional[str]:
    """Strip advisory phrases; return None if the text is suspiciously short."""
    if not text or len(text.strip()) < 20:
        return None
    if _ADVISORY_RE.search(text):
        logger.warning("explainer.advisory_language_detected", snippet=text[:120])
        return _FALLBACK_MSG
    return text.strip()


# ── Backend drivers ───────────────────────────────────────────────────────────

def _call_groq(prompt: str, api_key: str) -> Optional[str]:
    """Call Groq API (llama-3.3-70b-versatile)."""
    try:
        from groq import Groq  # type: ignore[import-untyped]
        client = Groq(api_key=api_key)
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=150,
            temperature=0.3,
        )
        return resp.choices[0].message.content
    except Exception as exc:
        logger.warning("explainer.groq_failed", err=str(exc))
        return None


def _call_gemini(prompt: str, api_key: str) -> Optional[str]:
    """Call Gemini 2.0 Flash API."""
    try:
        import google.generativeai as genai  # type: ignore[import-untyped]
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.0-flash")
        resp = model.generate_content(
            prompt,
            generation_config={"max_output_tokens": 150, "temperature": 0.3},
        )
        return resp.text
    except Exception as exc:
        logger.warning("explainer.gemini_failed", err=str(exc))
        return None


_LOCAL_LLM_CACHE: dict[str, Any] = {}  # model_path → Llama instance


def _call_local(prompt: str, model_path: str) -> Optional[str]:
    """Call a local GGUF model via llama-cpp-python (cached singleton)."""
    try:
        from llama_cpp import Llama  # type: ignore[import-untyped]
        if model_path not in _LOCAL_LLM_CACHE:
            _LOCAL_LLM_CACHE[model_path] = Llama(
                model_path=model_path, n_ctx=1024, n_threads=4, verbose=False
            )
        llm = _LOCAL_LLM_CACHE[model_path]
        out = llm(prompt, max_tokens=150, temperature=0.3, stop=["\n\n"])
        return out["choices"][0]["text"]
    except Exception as exc:
        logger.warning("explainer.local_failed", err=str(exc))
        return None


# ── Public API ────────────────────────────────────────────────────────────────

def explain(
    symbol: str,
    company_name: str,
    signal_type: str,
    confidence: float,
    features: dict[str, Any],
    headlines: list[str],
    macro_regime: str = "neutral",
    macro_events: list[str] | None = None,
) -> Optional[str]:
    """Generate a plain-English explanation for a trading signal.

    Returns ``None`` if explainability is disabled or all backends fail.
    Never raises — exceptions are logged and swallowed.
    """
    from app.core.config import settings

    if settings.explainability_backend == "disabled":
        return None

    if confidence < settings.explainability_confidence_threshold:
        return None  # Don't waste quota on low-confidence signals

    macro_events = macro_events or []
    prompt = _build_prompt(
        symbol, company_name, signal_type, confidence,
        features, headlines, macro_regime, macro_events,
    )

    raw: Optional[str] = None
    backend_used = "none"

    # ── Cascade ───────────────────────────────────────────────────────────────
    if settings.explainability_backend in ("groq", "auto") and settings.groq_api_key:
        t0 = time.monotonic()
        raw = _call_groq(prompt, settings.groq_api_key)
        if raw:
            backend_used = "groq"
            logger.info("explainer.groq_ok", symbol=symbol, latency_ms=int((time.monotonic()-t0)*1000))

    if raw is None and settings.gemini_api_key:
        t0 = time.monotonic()
        raw = _call_gemini(prompt, settings.gemini_api_key)
        if raw:
            backend_used = "gemini"
            logger.info("explainer.gemini_ok", symbol=symbol, latency_ms=int((time.monotonic()-t0)*1000))

    if raw is None and settings.local_llm_path:
        t0 = time.monotonic()
        raw = _call_local(prompt, settings.local_llm_path)
        if raw:
            backend_used = "local"
            logger.info("explainer.local_ok", symbol=symbol, latency_ms=int((time.monotonic()-t0)*1000))

    if raw is None:
        logger.warning("explainer.all_backends_failed", symbol=symbol)
        return None

    result = _post_filter(raw)
    if result and result != _FALLBACK_MSG:
        logger.debug("explainer.success", symbol=symbol, backend=backend_used, chars=len(result))
    return result
