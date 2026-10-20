"""Project-relative paths (data/, reports/, logs/)."""

from __future__ import annotations

import os
from pathlib import Path

# pauper_meta/ → src/ → pauper-meta/
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Optional override for flaky network filesystems: PAUPER_META_DATA_DIR=/tmp/...
_data_override = os.environ.get("PAUPER_META_DATA_DIR", "").strip()
DATA_DIR = Path(_data_override) if _data_override else (PROJECT_ROOT / "data")
RAW_DIR = DATA_DIR / "raw" / "mtgo"
DB_PATH = DATA_DIR / "pauper.sqlite"
REPORTS_DIR = PROJECT_ROOT / "reports" / "daily"
LOGS_DIR = PROJECT_ROOT / "logs"
CONFIG_DIR = PROJECT_ROOT / "config"
FIXTURES_DIR = PROJECT_ROOT / "fixtures"

ALIASES_CSV = CONFIG_DIR / "aliases.csv"
OVERRIDES_CSV = CONFIG_DIR / "overrides.csv"


def ensure_dirs() -> None:
    for d in (DATA_DIR, RAW_DIR, REPORTS_DIR, LOGS_DIR, CONFIG_DIR, FIXTURES_DIR):
        d.mkdir(parents=True, exist_ok=True)
