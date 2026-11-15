"""Shared by the flood tests: runs one Python project as its own program while a faulty
band floods it. The program is started the usual way (`--sim`, so there is no hardware),
and from the moment it asks for one kind of event, its own handler for that event is
called as fast as the computer can, again and again with the same report. That is what a
band does that sends frames without a pause and with a time that does not move.

    python3 flood.py PROGRAM EVENT FIELDS-AS-JSON [once] -- the program's own arguments

With `once` the handler gets the report one time only (for a report with a strange time).
When the program ends, one line says how much it still held:  kept NAME=COUNT ...
"""
import json
import os
import runpy
import sys
import threading
import types

KIT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
program, event, fields = sys.argv[1], sys.argv[2], json.loads(sys.argv[3], parse_constant=float)
rest = sys.argv[4:]
once = rest[0] == "once"
sys.argv = [program] + rest[rest.index("--") + 1:]
sys.path.insert(0, os.path.join(KIT, "python"))
from vexo_open import sync  # noqa: E402

real_on = sync.Band.on


def on(self, name, handler):
    if name == event:
        report = types.SimpleNamespace(**fields)

        def flood():
            handler(report)
            while not once:
                handler(report)
        threading.Thread(target=flood, daemon=True).start()
    return real_on(self, name, handler)


sync.Band.on = on
held = {}
try:
    held = runpy.run_path(program, run_name="__main__")
finally:
    sizes = ["%s=%d" % (name, len(held[name])) for name in ("happened", "readings", "waiting") if name in held]
    print("kept " + " ".join(sizes), flush=True)
