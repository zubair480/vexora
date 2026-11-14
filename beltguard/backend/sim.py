"""SIMULATED band motion for BeltGuard (S2 band-backend).

Uses the kit's own pretend band (vexo_open.transports.sim.SimTransport) and directs it with
`play_motion`, so every sample still travels through the real protocol and library. Every
message that comes from it carries source "sim" and the UI labels it SIMULATED.

Scenes: walk, idle, gesture, tool. A loop plays a short shift over and over; POST
/api/sim/scene {"scene": "tool"} plays one scene now (for testing the detector and the UI).
"""
from __future__ import annotations

import math
import random
import threading
import time

TAU = 2 * math.pi


def _walk(s: float) -> dict:
    step = TAU * 1.8 * s          # ~108 steps/min
    sway = TAU * 0.9 * s
    return dict(ax=0.20 * math.sin(step) + random.gauss(0, 0.03),
                ay=0.10 * math.sin(sway) + random.gauss(0, 0.03),
                az=1.0 + 0.30 * abs(math.sin(step)) - 0.15 + random.gauss(0, 0.03),
                gx=40 * math.sin(sway) + random.gauss(0, 3),
                gy=25 * math.sin(step + 1.0) + random.gauss(0, 3),
                gz=18 * math.sin(sway + 0.5) + random.gauss(0, 3))


def _idle(s: float) -> dict:
    return dict(ax=random.gauss(0, 0.003), ay=random.gauss(0, 0.003), az=1.0 + random.gauss(0, 0.003),
                gx=random.gauss(0, 0.08), gy=random.gauss(0, 0.08), gz=random.gauss(0, 0.08))


def _gesture(s: float) -> dict:
    # two quick wrist flicks inside 3 s, still in between
    burst = math.exp(-((s % 1.5) - 0.35) ** 2 / 0.01)
    return dict(ax=0.9 * burst * math.sin(TAU * 4 * s), ay=0.5 * burst, az=1.0 - 0.4 * burst,
                gx=260 * burst * math.sin(TAU * 3 * s), gy=120 * burst, gz=200 * burst * math.cos(TAU * 3 * s))


def _tool(s: float) -> dict:
    # a hand-held power tool: strong broadband vibration (motor ~37 Hz + hammering noise),
    # steady grip so little wrist rotation. Aliased when sampled at 50 Hz, like the real thing.
    return dict(ax=0.45 * math.sin(TAU * 37 * s) + random.gauss(0, 0.45),
                ay=0.30 * math.sin(TAU * 74 * s + 0.7) + random.gauss(0, 0.35),
                az=1.0 + 0.50 * math.sin(TAU * 37 * s + 1.3) + random.gauss(0, 0.5),
                gx=random.gauss(0, 9), gy=random.gauss(0, 9), gz=random.gauss(0, 7))


SCENES = {
    "walk": (_walk, 10.0),
    "idle": (_idle, 4.0),
    "gesture": (_gesture, 3.0),
    "tool": (_tool, 15.0),
}
# The auto loop never plays "tool": tool use (and so exposure) only happens when someone asks
# for it with POST /api/sim/scene, so an idle demo does not pile up tool sessions and points.
LOOP = ["walk", "idle", "gesture", "walk", "idle"]


class SimDirector:
    """Queues scenes on the pretend band. `transport` is `band.transport` (the library runs
    each call on the band's own thread)."""

    def __init__(self, transport, post) -> None:
        self.transport = transport
        self.post = post
        self.current = None
        self.next_at = 0.0
        self.manual_until = 0.0
        self.auto = True
        self._stop = threading.Event()
        self._thread = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="beltguard-sim", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def play(self, name: str, seconds: float | None = None) -> dict:
        if name not in SCENES:
            raise ValueError("scene must be one of %s" % ", ".join(SCENES))
        fn, default = SCENES[name]
        secs = float(seconds or default)
        self.transport.stop_motion()
        self.transport.play_motion(fn, secs)
        now = time.monotonic()
        self.manual_until = now + secs + 2.0        # hold the auto loop off a moment afterwards
        self.next_at = now + secs
        self.current = name
        self.post({"kind": "sim_scene", "source": "sim", "scene": name, "seconds": secs, "manual": True})
        return {"scene": name, "seconds": secs}

    def set_auto(self, on: bool) -> dict:
        """Turn the background loop on or off. Off: the pretend band lies still between directed scenes."""
        self.auto = bool(on)
        if not self.auto:
            self.transport.stop_motion()
            self.transport.play_motion(SCENES["idle"][0])        # hold still (a function plays until replaced)
        else:
            self.transport.stop_motion()
            self.next_at = 0.0
        self.post({"kind": "sim_scene", "source": "sim", "scene": "auto-on" if self.auto else "auto-off",
                   "seconds": 0, "manual": True})
        return {"auto": self.auto}

    def _run(self) -> None:
        i = 0
        while not self._stop.is_set():
            now = time.monotonic()
            if self.auto and now >= self.next_at and now >= self.manual_until:
                name = LOOP[i % len(LOOP)]
                i += 1
                fn, secs = SCENES[name]
                try:
                    self.transport.play_motion(fn, secs)
                except Exception:          # band gone: stop quietly
                    return
                self.current = name
                self.next_at = now + secs
                self.post({"kind": "sim_scene", "source": "sim", "scene": name, "seconds": secs, "manual": False})
            self._stop.wait(0.2)
