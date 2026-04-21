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

_FINBERT_MODEL  = "ProsusAI/finbert"
_BATCH_SIZE     = 64    # RTX 3050 4 GB VRAM comfortably handles 64 FP16 sequences
_MAX_LENGTH     = 512   # BERT hard limit; we chunk longer texts
_CHUNK_STRIDE   = 384   # token overlap window (128-token overlap)


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
                        logger.warning("finbert.cuda_unavailable", fallback="cpu")

                    # RTX 3050 GPU optimisations: TF32 matmuls + FP16 weights
                    # Both are backward-compatible; on CPU these are no-ops.
                    if device.startswith("cuda") and torch.cuda.is_available():
                        torch.backends.cuda.matmul.allow_tf32 = True
                        torch.backends.cudnn.allow_tf32       = True

                    dtype = (
                        torch.float16
                        if device.startswith("cuda") and torch.cuda.is_available()
                        else torch.float32
                    )

                    _PIPELINE = pipeline(
                        "text-classification",
                        model=_FINBERT_MODEL,
                        tokenizer=_FINBERT_MODEL,
                        device=device,
                        torch_dtype=dtype,       # FP16 on GPU → ~2× throughput
                        truncation=False,        # we handle chunking manually
                        max_length=_MAX_LENGTH,
                        top_k=None,              # return all three labels
                        batch_size=_BATCH_SIZE,
                    )
                    logger.info("finbert.loaded", model=_FINBERT_MODEL, device=device)
                except Exception as exc:
                    logger.error("finbert.load_failed", err=str(exc))
                    _PIPELINE = False  # sentinel
    return _PIPELINE if _PIPELINE else None


# ── Public API ────────────────────────────────────────────────────────────────

def _score_one(text: str, pipe) -> SentimentResult:
    """Score a single text, chunking if it exceeds BERT's 512-token limit.

    Tokenises the text without truncation, splits into overlapping 512-token
    windows (stride = _CHUNK_STRIDE), scores each chunk independently, then
    returns a weighted-average result where each chunk's weight is proportional
    to its token count × its winning confidence.
    """
    if not text or not text.strip():
        return SentimentResult("neutral", 0.33, 0.33)

    tokenizer = pipe.tokenizer
    ids = tokenizer.encode(text, add_special_tokens=False)

    if len(ids) <= _MAX_LENGTH - 2:
        # Short enough for a single forward pass (accounts for [CLS]/[SEP])
        label_scores = pipe(text, truncation=True, max_length=_MAX_LENGTH)[0]
        lm = {item["label"].lower(): item["score"] for item in label_scores}
        pos = lm.get("positive", 0.0)
        neg = lm.get("negative", 0.0)
        neu = lm.get("neutral",  0.0)
        best = max(lm, key=lm.get)  # type: ignore[arg-type]
        return SentimentResult(best, round(pos, 4), round(max(pos, neg, neu), 4))

    # Sliding-window chunking
    effective_len = _MAX_LENGTH - 2   # leave room for [CLS] and [SEP]
    chunks: list[str] = []
    start = 0
    while start < len(ids):
        chunk_ids = ids[start : start + effective_len]
        chunks.append(tokenizer.decode(chunk_ids, skip_special_tokens=True))
        if start + effective_len >= len(ids):
            break
        start += _CHUNK_STRIDE

    # Score all chunks (pipeline handles batching)
    chunk_outputs = pipe(chunks, truncation=True, max_length=_MAX_LENGTH)

    # Weighted aggregate: weight = n_tokens × confidence
    agg = {"positive": 0.0, "negative": 0.0, "neutral": 0.0}
    total_weight = 0.0
    for chunk_text, label_scores in zip(chunks, chunk_outputs):
        n_tokens = len(tokenizer.encode(chunk_text, add_special_tokens=False))
        lm = {item["label"].lower(): item["score"] for item in label_scores}
        confidence = max(lm.values())
        weight = n_tokens * confidence
        for label in agg:
            agg[label] += lm.get(label, 0.0) * weight
        total_weight += weight

    if total_weight == 0:
        return SentimentResult("neutral", 0.33, 0.33)

    for label in agg:
        agg[label] /= total_weight

    best_label = max(agg, key=agg.get)  # type: ignore[arg-type]
    return SentimentResult(
        sentiment  = best_label,
        score      = round(agg["positive"], 4),
        confidence = round(agg[best_label], 4),
    )


def score_headlines(headlines: list[str]) -> list[SentimentResult]:
    """Score a list of headlines and return one ``SentimentResult`` per headline.

    Falls back to neutral (0.33 each) if the pipeline is unavailable.
    Short texts that fit within the 512-token limit are scored in a single
    batch pass. Longer texts are handled via sliding-window chunking.
    """
    pipe = _get_pipeline()
    neutral_fallback = SentimentResult("neutral", 0.33, 0.33)

    if not pipe or not headlines:
        return [neutral_fallback] * len(headlines)

    results: list[SentimentResult] = []
    try:
        tokenizer = pipe.tokenizer
        short_texts: list[str] = []
        short_indices: list[int] = []
        long_indices: list[int] = []

        # Pre-allocate results
        results = [neutral_fallback] * len(headlines)

        # Separate short vs long texts
        for i, text in enumerate(headlines):
            if not text or not text.strip():
                continue
            ids = tokenizer.encode(text, add_special_tokens=False)
            if len(ids) <= _MAX_LENGTH - 2:
                short_texts.append(text)
                short_indices.append(i)
            else:
                long_indices.append(i)

        # Batch score short texts in one pass
        if short_texts:
            batch_outputs = pipe(short_texts, truncation=True, max_length=_MAX_LENGTH,
                                 batch_size=_BATCH_SIZE)
            for idx, label_scores in zip(short_indices, batch_outputs):
                lm = {item["label"].lower(): item["score"] for item in label_scores}
                pos = lm.get("positive", 0.0)
                neg = lm.get("negative", 0.0)
                neu = lm.get("neutral", 0.0)
                best = max(lm, key=lm.get)  # type: ignore[arg-type]
                results[idx] = SentimentResult(best, round(pos, 4), round(max(pos, neg, neu), 4))

        # Score long texts individually via chunking
        for idx in long_indices:
            results[idx] = _score_one(headlines[idx], pipe)

    except Exception as exc:
        logger.error("finbert.score_failed", err=str(exc))
        results = [neutral_fallback] * len(headlines)

    return results
