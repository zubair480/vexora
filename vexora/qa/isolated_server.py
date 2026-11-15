#!/usr/bin/env python3
"""ISOLATED BeltGuard server for UI checks (S5 qa-verify). Never the team's backend.

    python beltguard/qa/isolated_server.py [port]          # default 8799, no band at all
    python beltguard/qa/isolated_server.py 8799 --sim      # + S2's pretend band, so qa/e2e.py can run on a copy:
    python beltguard/qa/e2e.py --base http://127.0.0.1:8799 --db "$(cat beltguard/qa/.isolated_db)"

Same isolation as qa/voice_e2e.py: a temp COPY of data/beltguard.db (BELTGUARD_DB), a lifespan that starts
only the event consumer (no band bridge, no USB/Bluetooth search, no sim), app.heartbeat silenced. It serves
the real frontend/ folder, so http://127.0.0.1:<port>/ shows the live UI code against throwaway data.
Drive it with POST /api/report {"text","source":"band-mic"} and a `test: true` /ws/ingest client.
Stop it with Ctrl+C (or kill the process); the temp copy is left in %TEMP%\\bg-iso-*.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

QA = Path(__file__).resolve().parent
sys.path.insert(0, str(QA))

from voice_e2e import copy_db, load_isolated_app  # noqa: E402


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    sim = "--sim" in sys.argv[1:]
    port = int(args[0]) if args else 8799
    if port == 8765:
        raise SystemExit("refusing port 8765: that is the team's backend")
    import socket                                    # never announce a port someone else already owns
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", port))
    except OSError:
        raise SystemExit("port %d is already in use (another isolated server?): pick another port, "
                         "e.g. python beltguard/qa/isolated_server.py %d" % (port, port + 1))
    finally:
        probe.close()
    tmp = Path(tempfile.mkdtemp(prefix="bg-iso-"))
    db = copy_db(tmp)
    A, _S, _sent = load_isolated_app(db, sim=sim)
    import uvicorn
    print("ISOLATED BeltGuard on http://127.0.0.1:%d  (DB copy %s; %s; no band search, no heartbeat, raw -> %s)"
          % (port, db, "SIM band" if sim else "no band", tmp / "raw"), flush=True)
    (Path(__file__).resolve().parent / ".isolated_db").write_text(str(db), encoding="utf-8")
    uvicorn.run(A.app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
