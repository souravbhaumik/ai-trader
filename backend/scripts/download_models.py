#!/usr/bin/env python
"""Download LSTM / TFT model artifacts from Google Drive and register them.

Usage (inside the backend container):
    docker compose exec backend python scripts/download_models.py
    docker compose exec backend python scripts/download_models.py --lstm-only
    docker compose exec backend python scripts/download_models.py --tft-only

Environment variables required in .env:
    LSTM_GDRIVE_ID   — Google Drive file ID for the LSTM autoencoder artifact
    TFT_GDRIVE_ID    — Google Drive file ID for the TFT forecaster artifact

Both IDs are printed at the end of each Colab training notebook.

Workflow
--------
1. Run colab/train_lstm_autoencoder.ipynb on Colab (free T4 GPU).
   The notebook saves the artifact to Google Drive and prints its file ID.
2. Run colab/train_tft_forecaster.ipynb on Colab similarly.
3. Copy both IDs into .env.
4. Inside the container run:
       python scripts/download_models.py
5. Restart backend so the services pick up the new artifacts:
       docker compose restart backend celery-worker
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

_LSTM_PATH = Path(os.getenv("LSTM_MODEL_PATH", "/app/models/lstm/latest.pt"))
_TFT_PATH  = Path(os.getenv("TFT_MODEL_PATH",  "/app/models/tft/latest.pt"))


# ═══════════════════════════════════════════════════════════════════════════════
#  Google Drive download
# ═══════════════════════════════════════════════════════════════════════════════

def _download_gdrive(file_id: str, dest: Path) -> bool:
    """Download a public/unlisted Google Drive file using gdown."""
    try:
        import gdown  # noqa: PLC0415
    except ImportError:
        logger.error("download.gdown_missing",
                     hint="Add 'gdown' to requirements.txt and rebuild the image")
        return False

    dest.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://drive.google.com/uc?id={file_id}"
    logger.info("download.start", file_id=file_id, dest=str(dest))
    try:
        gdown.download(url, str(dest), quiet=False)
        if not dest.exists():
            logger.error("download.missing_after_download", dest=str(dest))
            return False
        size_kb = dest.stat().st_size // 1024
        logger.info("download.done", dest=str(dest), size_kb=size_kb)
        return True
    except Exception as exc:
        logger.error("download.failed", file_id=file_id, error=str(exc))
        return False


# ═══════════════════════════════════════════════════════════════════════════════
#  Database registration
# ═══════════════════════════════════════════════════════════════════════════════

def _register_model(model_type: str, artifact_path: Path) -> bool:
    """Load artifact metadata and upsert + promote in ml_models table."""
    try:
        import torch  # noqa: PLC0415
        payload = torch.load(artifact_path, map_location="cpu", weights_only=False)
    except Exception as exc:
        logger.error("register.load_failed", path=str(artifact_path), error=str(exc))
        return False

    version       = payload.get("version",       f"{model_type}-unknown")
    metrics       = payload.get("metrics",        {})
    hyperparams   = payload.get("config",         {})
    feature_names = payload.get("feature_names",  [])

    logger.info("register.start", model_type=model_type, version=version)

    try:
        # Ensure /app is on path so the app package is importable
        if "/app" not in sys.path:
            sys.path.insert(0, "/app")
        from app.core.database import get_sync_session  # noqa: PLC0415
        from sqlalchemy import text                      # noqa: PLC0415

        with get_sync_session() as session:
            # 1. Deactivate any previous active model of this type
            session.execute(
                text("UPDATE ml_models SET is_active = FALSE WHERE model_type = :t"),
                {"t": model_type},
            )
            # 2. Insert new version (skip if exact same version already exists)
            session.execute(
                text("""
                    INSERT INTO ml_models
                        (model_type, version, artifact_path, metrics, hyperparams,
                         feature_names, is_active, promoted_at, trained_at)
                    VALUES
                        (:type, :version, :path,
                         CAST(:metrics AS jsonb), CAST(:hp AS jsonb), CAST(:fn AS jsonb),
                         TRUE, NOW(), NOW())
                    ON CONFLICT DO NOTHING
                """),
                {
                    "type":    model_type,
                    "version": version,
                    "path":    str(artifact_path),
                    "metrics": json.dumps(metrics),
                    "hp":      json.dumps(hyperparams),
                    "fn":      json.dumps(feature_names),
                },
            )
            # 3. If ON CONFLICT hit (same version already exists), still promote it
            session.execute(
                text("""
                    UPDATE ml_models
                    SET    is_active = TRUE, promoted_at = NOW()
                    WHERE  model_type = :t AND version = :v
                """),
                {"t": model_type, "v": version},
            )
            session.commit()

        logger.info("register.done", model_type=model_type, version=version)
        return True
    except Exception as exc:
        logger.error("register.db_failed", error=str(exc))
        return False


# ═══════════════════════════════════════════════════════════════════════════════
#  Entry point
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download LSTM/TFT artifacts from Google Drive"
    )
    parser.add_argument("--lstm-only", action="store_true", help="Download LSTM only")
    parser.add_argument("--tft-only",  action="store_true", help="Download TFT only")
    args = parser.parse_args()

    lstm_id = os.getenv("LSTM_GDRIVE_ID", "").strip()
    tft_id  = os.getenv("TFT_GDRIVE_ID",  "").strip()

    do_lstm = (not args.tft_only)  and (lstm_id or args.lstm_only)
    do_tft  = (not args.lstm_only) and (tft_id  or args.tft_only)

    if not lstm_id and not tft_id:
        print(
            "\nERROR: Neither LSTM_GDRIVE_ID nor TFT_GDRIVE_ID is set in .env\n\n"
            "Steps:\n"
            "  1. Open colab/train_lstm_autoencoder.ipynb in Google Colab and run all cells.\n"
            "  2. Copy the Google Drive file ID printed at the end.\n"
            "  3. Add to .env:  LSTM_GDRIVE_ID=<paste_here>\n"
            "  4. Repeat for colab/train_tft_forecaster.ipynb → TFT_GDRIVE_ID=<paste_here>\n"
            "  5. Re-run:  docker compose exec backend python scripts/download_models.py\n"
            "  6. Restart: docker compose restart backend celery-worker\n"
        )
        sys.exit(1)

    success = True

    if do_lstm and lstm_id:
        ok = _download_gdrive(lstm_id, _LSTM_PATH)
        if ok:
            ok = _register_model("lstm", _LSTM_PATH)
        if not ok:
            print("LSTM download/registration FAILED — check logs above.")
        success = success and ok
    elif not lstm_id:
        print("LSTM_GDRIVE_ID not set — skipping LSTM download.")

    if do_tft and tft_id:
        ok = _download_gdrive(tft_id, _TFT_PATH)
        if ok:
            ok = _register_model("tft", _TFT_PATH)
        if not ok:
            print("TFT download/registration FAILED — check logs above.")
        success = success and ok
    elif not tft_id:
        print("TFT_GDRIVE_ID not set — skipping TFT download.")

    if success:
        print(
            "\n✓ Done.  Restart backend to activate new models:\n"
            "  docker compose restart backend celery-worker\n"
        )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
