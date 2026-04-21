"""LightGBM model trainer — Phase 3.

Trains a binary LightGBM classifier to predict next-day direction
(BUY = close[t+1] > close[t] * 1.005, else SELL/HOLD).

Training data source: ``ohlcv_daily`` table for all active symbols.
Features: see ``feature_engineer.FEATURE_NAMES``.
Target:   1 if next-day return >= 0.5%, else 0.

The trained model artifact is saved as a ``.pkl`` file under
``/app/models/lgbm/`` and registered in the ``ml_models`` table.

Usage (Celery task or one-off CLI):
    from app.services.lgbm_trainer import train_lgbm
    train_lgbm()
"""
from __future__ import annotations

import math
import os
import pickle
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import structlog

from app.core.database import get_sync_session
from app.services.feature_engineer import FEATURE_NAMES, build_features

logger = structlog.get_logger(__name__)

_MODEL_DIR = Path(os.getenv("MODEL_DIR", "/app/models/lgbm"))
_MIN_RETURN_BUY  =  0.005   # +0.5 % next-day → label 1
_MIN_SYMBOLS     = 10       # skip training if fewer valid symbols
_LOOKBACK_BARS   = 500      # maximum history per symbol for training data


def _fetch_all_ohlcv(session) -> dict[str, list[dict]]:
    """Return {symbol: [{ts, open, high, low, close, volume}, ...]} newest-last."""
    from sqlalchemy import text

    rows = session.execute(
        text("""
            SELECT symbol, ts, open, high, low, close, volume
            FROM   ohlcv_daily
            WHERE  ts >= NOW() - INTERVAL :lb
            ORDER  BY symbol, ts ASC
        """),
        {"lb": f"{_LOOKBACK_BARS} days"},
    ).fetchall()

    out: dict[str, list] = {}
    for row in rows:
        sym = row[0]
        out.setdefault(sym, []).append({
            "ts": row[1], "open": float(row[2]), "high": float(row[3]),
            "low": float(row[4]), "close": float(row[5]), "volume": float(row[6]),
        })
    return out


def _build_dataset(
    symbol_ohlcv: dict[str, list[dict]],
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Build X, y arrays from OHLCV history of all symbols.

    For each symbol and each bar (with sufficient lookback), compute features
    using the *preceding* bars and label using the *next* bar's close.

    Returns (X, y, valid_symbols_used).
    """
    X_rows: list[list[float]] = []
    y_rows: list[int]         = []
    syms_used: list[str]      = []

    for sym, bars in symbol_ohlcv.items():
        closes  = [b["close"]  for b in bars]
        highs   = [b["high"]   for b in bars]
        lows    = [b["low"]    for b in bars]
        volumes = [b["volume"] for b in bars]

        # Slide a window: use bars[0..i] to predict bars[i+1]
        # Minimum lookback is 60 bars; walk forward from there
        min_lb = 60
        for i in range(min_lb, len(bars) - 1):
            c_slice = closes[:i + 1]
            h_slice = highs[:i + 1]
            l_slice = lows[:i + 1]
            v_slice = volumes[:i + 1]

            feats = build_features(sym, c_slice, h_slice, l_slice, v_slice,
                                   sentiment_score=0.0)  # no historical sentiment

            row = [feats.get(n, float("nan")) for n in FEATURE_NAMES]
            if any(math.isnan(v) for v in row):
                continue

            next_ret = (closes[i + 1] - closes[i]) / closes[i]
            label    = 1 if next_ret >= _MIN_RETURN_BUY else 0

            X_rows.append(row)
            y_rows.append(label)
            if sym not in syms_used:
                syms_used.append(sym)

    X = np.array(X_rows, dtype=np.float32)
    y = np.array(y_rows, dtype=np.int32)
    return X, y, syms_used


def train_lgbm(
    *,
    n_estimators: int   = 500,
    learning_rate: float = 0.05,
    max_depth: int      = 6,
    num_leaves: int     = 31,
    min_child_samples: int = 20,
    subsample: float    = 0.8,
    colsample_bytree: float = 0.8,
    class_weight: str   = "balanced",
    notes: str          = "",
) -> dict[str, Any]:
    """Train LightGBM classifier and register in ml_models table.

    Returns a dict with model metadata.
    """
    try:
        import lightgbm as lgb
        from sklearn.model_selection import train_test_split
        from sklearn.metrics import roc_auc_score, accuracy_score
    except ImportError as exc:
        raise RuntimeError(
            "lightgbm and scikit-learn are required for training. "
            "Install them: pip install lightgbm scikit-learn"
        ) from exc

    logger.info("lgbm_trainer.start")

    # ── 1. Fetch data ─────────────────────────────────────────────────────────
    with get_sync_session() as session:
        symbol_ohlcv = _fetch_all_ohlcv(session)

    if len(symbol_ohlcv) < _MIN_SYMBOLS:
        raise RuntimeError(
            f"Insufficient symbols in ohlcv_daily: {len(symbol_ohlcv)} < {_MIN_SYMBOLS}. "
            "Run EOD ingest first."
        )

    # ── 2. Build feature matrix ───────────────────────────────────────────────
    X, y, syms_used = _build_dataset(symbol_ohlcv)
    logger.info("lgbm_trainer.dataset_ready",
                samples=len(X), symbols=len(syms_used),
                positive_rate=round(y.mean(), 3))

    if len(X) < 200:
        raise RuntimeError(f"Too few training samples: {len(X)}. Need at least 200.")

    X_train, X_val, y_train, y_val = train_test_split(
        X, y, test_size=0.15, shuffle=False,  # temporal split: no shuffle to prevent leakage
    )

    # ── 3. Train ──────────────────────────────────────────────────────────────
    hp: dict[str, Any] = dict(
        n_estimators     = n_estimators,
        learning_rate    = learning_rate,
        max_depth        = max_depth,
        num_leaves       = num_leaves,
        min_child_samples= min_child_samples,
        subsample        = subsample,
        colsample_bytree = colsample_bytree,
        class_weight     = class_weight,
        random_state     = 42,
        n_jobs           = -1,
        verbosity        = -1,
        # GPU acceleration: set LGBM_DEVICE=gpu in docker-compose env to use RTX 3050
        device_type      = os.getenv("LGBM_DEVICE", "cpu"),
    )
    clf = lgb.LGBMClassifier(**hp)
    clf.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        callbacks=[lgb.early_stopping(50, verbose=False), lgb.log_evaluation(-1)],
    )

    # ── 4. Evaluate ───────────────────────────────────────────────────────────
    val_proba = clf.predict_proba(X_val)[:, 1]
    val_pred  = (val_proba >= 0.5).astype(int)
    metrics: dict[str, float] = {
        "val_auc":      round(float(roc_auc_score(y_val, val_proba)), 4),
        "val_accuracy": round(float(accuracy_score(y_val, val_pred)), 4),
        "n_train":      int(len(X_train)),
        "n_val":        int(len(X_val)),
        "n_symbols":    len(syms_used),
        "best_iteration": int(clf.best_iteration_),
    }
    logger.info("lgbm_trainer.metrics", **metrics)

    # ── 5. Persist artifact ───────────────────────────────────────────────────
    _MODEL_DIR.mkdir(parents=True, exist_ok=True)
    model_id  = str(uuid.uuid4())
    version   = f"lgbm-{datetime.now(tz=timezone.utc).strftime('%Y%m%d-%H%M')}"
    artifact  = _MODEL_DIR / f"{version}.pkl"

    with open(artifact, "wb") as fh:
        pickle.dump({"model": clf, "feature_names": FEATURE_NAMES, "version": version}, fh)

    logger.info("lgbm_trainer.artifact_saved", path=str(artifact))

    # ── 6. Register in ml_models ──────────────────────────────────────────────
    import json
    from sqlalchemy import text

    with get_sync_session() as session:
        session.execute(
            text("""
                INSERT INTO ml_models
                    (id, model_type, version, artifact_path, metrics,
                     hyperparams, feature_names, is_active, trained_at)
                VALUES
                    (:id, 'lgbm', :version, :artifact_path, CAST(:metrics AS jsonb),
                     CAST(:hp AS jsonb), CAST(:fn AS jsonb), FALSE, NOW())
            """),
            {
                "id":           model_id,
                "version":      version,
                "artifact_path": str(artifact),
                "metrics":      json.dumps(metrics),
                "hp":           json.dumps(hp),
                "fn":           json.dumps(FEATURE_NAMES),
            },
        )
        session.commit()

    logger.info("lgbm_trainer.registered", model_id=model_id, version=version)

    # ── Release C++ LightGBM memory (prevents RAM inflation on repeated runs) ─
    import gc
    try:
        del clf
        del train_data
    except NameError:
        pass
    gc.collect()

    return {"model_id": model_id, "version": version, "metrics": metrics, "artifact": str(artifact)}
