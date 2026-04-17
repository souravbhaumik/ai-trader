"""FinBERT sentiment scorer — Phase 4.

Loads ``ProsusAI/finbert`` once per worker process (lazy, thread-safe) and
scores batches of financial headlines. The model is kept on CPU by default;
set SENTIMENT_DEVICE=cuda to use GPU if available.

Output per headline:
    sentiment   str    'positive' | 'negative' | 'neutral'
    score       float  raw positive-class softmax probability  (0–1)
    confidence  float  max(positive, negative, neutral) probability (0–1)

The model weights (~440 MB) are downloaded to the HuggingFace cache on first
run. Subsequent runs are instant.
"""
from __future__ import annotations

import threading
from typing import NamedTuple

import structlog

logger = structlog.get_logger(__name__)

_FINBERT_MODEL = "ProsusAI/finbert"
_BATCH_SIZE    = 32
_MAX_LENGTH    = 128   # sufficient for most headlines; avoids OOM on RTX 3050


class SentimentResult(NamedTuple):
    sentiment:  str    # 'positive' | 'negative' | 'neutral'
    score:      float  # positive-class probability
    confidence: float  # max-class probability


# ── Lazy-loaded pipeline ──────────────────────────────────────────────────────
_PIPELINE = None
_PIPELINE_LOCK = threading.Lock()


def _get_pipeline():
    global _PIPELINE
    if _PIPELINE is None:
        with _PIPELINE_LOCK:
            if _PIPELINE is None:
                try:
                    from transformers import pipeline
                    import os, torch
                    device = os.getenv("SENTIMENT_DEVICE", "cpu")
                    if device == "cuda" and not torch.cuda.is_available():
                        device = "cpu"
                    _PIPELINE = pipeline(
                        "text-classification",
                        model=_FINBERT_MODEL,
                        tokenizer=_FINBERT_MODEL,
                        device=device,
                        truncation=True,
                        max_length=_MAX_LENGTH,
                        top_k=None,                # return all three labels
                        batch_size=_BATCH_SIZE,
                    )
                    logger.info("finbert.loaded", model=_FINBERT_MODEL, device=device)
                except Exception as exc:
                    logger.error("finbert.load_failed", error=str(exc))
                    _PIPELINE = False  # sentinel
    return _PIPELINE if _PIPELINE else None


# ── Public API ────────────────────────────────────────────────────────────────

def score_headlines(headlines: list[str]) -> list[SentimentResult]:
    """Score a list of headlines and return one ``SentimentResult`` per headline.

    Falls back to neutral (0.33 each) if the pipeline is unavailable.
    """
    pipe = _get_pipeline()
    neutral_fallback = SentimentResult("neutral", 0.33, 0.33)

    if not pipe or not headlines:
        return [neutral_fallback] * len(headlines)

    results: list[SentimentResult] = []
    try:
        outputs = pipe(headlines)  # list of list[{label, score}]
        for label_scores in outputs:
            label_map = {item["label"].lower(): item["score"] for item in label_scores}
            pos = label_map.get("positive", 0.0)
            neg = label_map.get("negative", 0.0)
            neu = label_map.get("neutral",  0.0)
            best_label = max(label_map, key=label_map.get)  # type: ignore[arg-type]
            results.append(SentimentResult(
                sentiment  = best_label,
                score      = round(pos, 4),
                confidence = round(max(pos, neg, neu), 4),
            ))
    except Exception as exc:
        logger.error("finbert.score_failed", error=str(exc))
        results = [neutral_fallback] * len(headlines)

    return results
