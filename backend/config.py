"""Settings. Shared file: owned by the integration session (see CONTRACT.md)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KIT = ROOT / "reference" / "vexo-open-kit"
RECORDINGS = ROOT / "recordings"
MODELS = ROOT / "models"
DB_PATH = Path(os.environ.get("BELTGUARD_DB", ROOT / "beltguard.db"))
API_PORT = 8010

ACTION_POINTS = 100.0   # UK exposure action value
LIMIT_POINTS = 400.0    # UK exposure limit value


def load_env() -> None:
    """Read KEY=value lines from the repo's .env. A real environment variable wins."""
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip().strip('"').strip("'"))


def add_kit_to_path() -> None:
    """Make `vexo_open`, `speech` and `ai` importable from the unmodified kit."""
    for folder in (KIT / "python", KIT / "examples" / "python"):
        if str(folder) not in sys.path:
            sys.path.insert(0, str(folder))


class BandNumberMissing(RuntimeError):
    pass


def band_number() -> int:
    """The team's band number, from VEXO_BAND only. There is no default on purpose."""
    load_env()
    raw = os.environ.get("VEXO_BAND", "").strip()
    if not raw.isdigit() or not 1 <= int(raw) <= 999:
        raise BandNumberMissing(
            "Set VEXO_BAND to your team's band number (written on the band's bag). "
            "BeltGuard never guesses it and never connects without it."
        )
    return int(raw)


def current_shift() -> dict:
    return {
        "shift_id": os.environ.get("BELTGUARD_SHIFT", "shift-demo"),
        "worker_id": os.environ.get("BELTGUARD_WORKER", "worker-1"),
        "conveyor_id": os.environ.get("BELTGUARD_CONVEYOR", "CV-102"),
    }
