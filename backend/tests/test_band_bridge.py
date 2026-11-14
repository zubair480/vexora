"""VexoBridge and QualityTracker, with a fake of the SDK's Band. No band, real or pretend, is used.

The team's band number is never written here: `config.band_number` is replaced by a sentinel.
"""
import math
import time

import pytest

from backend import config, vexo_bridge
from backend.vexo_bridge import BridgeError, QualityTracker, VexoBridge

TEAM = object()        # stands for "the team's number", whatever it is
OTHER = object()       # some other band
ALL_CAPS = ["accel", "gyro", "events", "steps", "mic", "led", "battery", "temperature", "storage"]
RATES = [10, 25, 50, 100, 208, 416, 833, 1666]


class Msg(dict):
    __getattr__ = dict.get


class Denied(Exception):
    code = "denied"


class FakeBand:
    def __init__(self, number=TEAM, caps=None, grant=None):
        self.number, self.name = number, "test-band"
        self.info = Msg(firmware="1.1.0", capabilities=caps or list(ALL_CAPS), features=["fast-motion"])
        self.handlers, self.calls = {}, []
        self.streams = {"motion": 0, "audio": False, "mic_level": False, "battery": False}
        self.dropped_events, self.grant, self.led = 0, grant, None

    def on(self, name, fn):
        self.handlers[name] = fn

    def emit(self, name, **fields):
        self.handlers[name](Msg(fields))

    def motion(self, n, when, ax=0.0, ay=0.0, az=1.0, gx=0.0, gy=0.0, gz=0.0):
        self.emit("motion", n=n, t=when * 1000.0, time=when, ax=ax, ay=ay, az=az, gx=gx, gy=gy, gz=gz)

    def motion_limits(self):
        self.calls.append(("motion_limits",))
        return Msg(rates=list(RATES), accel=833, gyro=833, both=416, transport="ble")

    def set_motion(self, **kw):
        self.calls.append(("set_motion", kw))

    def get_motion(self):
        return Msg(rate=50, sensors="both", accel_range=8, gyro_range=2000)

    def configure(self, **kw):
        self.calls.append(("configure", kw))
        for key, value in kw.items():
            if key != "sensors":
                self.streams[key] = value
        if "motion" in kw and self.grant is not None:
            self.streams["motion"] = self.grant
        return dict(self.streams)

    def set_led(self, colour, brightness=1.0, pattern="solid", period=1000):
        if self.streams["audio"] or self.streams["mic_level"]:
            raise Denied("the microphone holds the LED")
        self.led = (colour, brightness, pattern, period)

    def led_off(self):
        self.led = "off"

    def disconnect(self):
        self.calls.append(("disconnect",))
        if "disconnect" in self.handlers:
            self.emit("disconnect", reason="closed")


@pytest.fixture()
def team(monkeypatch):
    monkeypatch.setattr(config, "band_number", lambda: TEAM)


@pytest.fixture()
def imu(test_bus):
    batches, events = [], []
    test_bus.subscribe_imu(batches.append)
    test_bus.subscribe(events.append)
    return test_bus, batches, events


def started(band, hold=True, **kw):
    """A bridge on the fake band. hold=True stops the flush thread so tests drain by hand."""
    seen = {}

    def connect(number, transport, auto_reconnect=True):
        seen.update(number=number, transport=transport, auto_reconnect=auto_reconnect)
        return band

    bridge = VexoBridge(connect=connect)
    bridge.start(**kw)
    if hold:
        bridge._halt_thread()
    bridge.seen = seen
    return bridge


def feed(band, count, start, hz=50.0, first_n=0):
    for i in range(count):
        band.motion(first_n + i, start + i / hz)
    return start + (count - 1) / hz


# ---------------------------------------------------------------- QualityTracker (pure)
def test_tracker_counts_missing_samples_from_n_jumps():
    q = QualityTracker(50)
    for n in (0, 1, 2, 10, 11):            # 3..9 lost: seven samples
        q.add(n, 100 + n / 50)
    assert q.missing == 7
    assert q.completeness() == round(5 / 12, 4)


def test_tracker_measures_rate_from_sample_time_not_from_configured():
    q = QualityTracker(50)
    for n in range(104):                   # the sensor really runs at 52 a second
        q.add(n, 100 + n / 52)
    snap = q.snapshot(now=100 + 103 / 52)
    assert snap["configured_hz"] == 50
    assert snap["effective_hz"] == pytest.approx(52, abs=0.2)
    assert snap["stale"] is False and snap["missing_samples"] == 0


def test_tracker_counter_restart_is_not_a_loss():
    q = QualityTracker(50)
    for n in range(50):
        q.add(n, 100 + n / 50)
    for n in range(50):                    # n starts again at 0 (reconnect or new rate)
        q.add(n, 101 + n / 50)
    assert q.missing == 0 and q.counter_resets == 1 and q.received == 100


def test_tracker_silence_is_a_gap_and_goes_stale():
    q = QualityTracker(50)
    for n in range(50):
        q.add(n, 100 + n / 50)
    last = 100 + 49 / 50
    assert q.stale(now=last + 0.5) is False
    assert q.stale(now=last + 1.5) is True
    assert q.effective_hz(now=last + 1.5) == 0.0          # no rate is claimed while stale
    for n in range(50, 100):
        q.add(n, 105 + (n - 50) / 50)                     # data again 4 s later
    assert q.gaps == [{"start": last, "end": 105.0, "reason": "no-data"}]
    assert q.effective_hz(now=105 + 49 / 50) == pytest.approx(50, abs=0.2)   # gap not averaged in


def test_tracker_link_gap_has_start_and_end():
    q = QualityTracker(50)
    q.add(7, 100.0)
    q.gap_started(100.4, "link-lost")
    assert q.stale(now=100.41) is True and q.gaps[0]["end"] is None
    q.gap_ended(103.0)
    q.add(0, 103.1)
    assert q.gaps == [{"start": 100.0, "end": 103.0, "reason": "link-lost"}]
    assert q.missing == 0


def test_tracker_saturation_and_empty_state():
    q = QualityTracker(50)
    assert q.snapshot(now=1.0)["stale"] is True and q.completeness() == 0.0
    q.add(0, 100.0, saturated=True)
    assert q.snapshot(now=100.0)["saturated"] is True and q.saturated_samples == 1


def test_tracker_gap_is_returned_even_if_samples_came_before_reconnected():
    q = QualityTracker(50)
    q.add(5, 100.0)
    q.gap_started(100.2, "link-lost")
    q.add(0, 104.0)                        # the new link's first sample closes the gap...
    q.add(1, 104.02)
    gap = q.gap_ended(104.05)              # ...and the "reconnected" event still gets it
    assert gap == {"start": 100.0, "end": 104.0, "reason": "link-lost"}
    q.add(2, 104.04)
    assert q.missing == 0 and q.counter_resets == 0 and len(q.gaps) == 1


def test_tracker_without_counter_does_not_claim_full_completeness():
    q = QualityTracker(50)                 # firmware 1.0: n is None, loss cannot be counted
    for i in range(50):
        q.add(None, 100 + i / 25)          # only half the readings arrive
    snap = q.snapshot(now=100 + 49 / 25)
    assert snap["missing_samples_known"] is False and snap["missing_samples"] == 0
    assert snap["effective_hz"] == pytest.approx(25, abs=0.2)
    assert snap["completeness"] == pytest.approx(0.5, abs=0.01)


def test_tracker_duplicate_counter_and_clock_step_back():
    q = QualityTracker(50)
    q.add(3, 100.0)
    q.add(3, 100.02)                       # same n twice is not a counter restart
    assert q.counter_resets == 0 and q.missing == 0
    for i in range(10):
        q.add(4 + i, 50.0 + i / 50)        # computer clock stepped back: rate measured afresh
    assert q.effective_hz(now=50.0 + 9 / 50) == pytest.approx(50, abs=0.5)


# ---------------------------------------------------------------- start
def test_start_refuses_without_band_number(monkeypatch):
    monkeypatch.setattr(config, "load_env", lambda: None)
    monkeypatch.delenv("VEXO_BAND", raising=False)
    called = []
    bridge = VexoBridge(connect=lambda *a, **k: called.append(a))
    with pytest.raises(config.BandNumberMissing):
        bridge.start()
    assert called == [] and bridge.status()["connected"] is False
    with pytest.raises(config.BandNumberMissing):
        bridge.start(transport="sim")       # the pretend band needs the team's number too
    assert called == []


def test_start_uses_config_number_and_reads_limits_before_configure(team, imu):
    bus, _, events = imu
    band = FakeBand()
    bridge = started(band)
    assert bridge.seen == {"number": TEAM, "transport": "ble", "auto_reconnect": True}
    names = [c[0] for c in band.calls]
    assert names.index("motion_limits") < names.index("configure")
    assert ("configure", {"motion": 50, "sensors": "both", "battery": True}) in band.calls
    assert set(band.handlers) == {"motion", "battery", "notice", "disconnect", "reconnecting", "reconnected"}
    status = bridge.status()
    assert status["connected"] and status["source"] == "band" and status["configured_hz"] == 50
    assert status["band_name"] == "test-band" and status["firmware"] == "1.1.0"
    bus.drain()
    assert [(e.type, e.source, e.payload["reason"]) for e in events] == [("device_connected", "band", "connected")]
    bridge.stop()


def test_rate_is_clamped_to_the_link_limit_and_granted_rate_is_reported(team, imu):
    band = FakeBand()
    bridge = started(band, motion_hz=833)
    assert ("configure", {"motion": 416, "sensors": "both", "battery": True}) in band.calls
    bridge.stop()
    band = FakeBand(grant=100)              # the band grants something else than asked
    bridge = started(band, motion_hz=208)
    assert bridge.status()["configured_hz"] == 100
    bridge.stop()


def test_capabilities_decide_what_is_asked(team, imu):
    band = FakeBand(caps=["accel", "led"])
    bridge = started(band)
    assert ("configure", {"motion": 50, "sensors": "accel"}) in band.calls   # no gyro, no battery
    assert bridge.status()["sensors"] == "accel"
    bridge.stop()
    with pytest.raises(BridgeError) as err:
        started(FakeBand(caps=["led", "battery"]))
    assert err.value.code == "not-available"


def test_wrong_band_is_refused(team, imu):
    band = FakeBand(number=OTHER)
    with pytest.raises(BridgeError) as err:
        started(band)
    assert err.value.code == "wrong-band"
    assert ("disconnect",) in band.calls and not any(c[0] == "configure" for c in band.calls)


def test_connect_failure_keeps_the_sdk_code_and_bad_arguments(team):
    class NotFound(Exception):
        code = "not-found"

    def connect(*a, **k):
        raise NotFound("band not found")

    bridge = VexoBridge(connect=connect)
    with pytest.raises(BridgeError) as err:
        bridge.start()
    assert err.value.code == "not-found" and bridge.status()["last_error"] == "band not found"
    with pytest.raises(ValueError):
        bridge.start(transport="wifi")
    with pytest.raises(ValueError):
        bridge.start(sensors="all")


def test_second_start_is_refused(team, imu):
    bridge = started(FakeBand())
    with pytest.raises(BridgeError) as err:
        bridge.start()
    assert err.value.code == "already-running"
    bridge.stop()


# ---------------------------------------------------------------- motion
def test_handlers_only_enqueue_and_flush_publishes_band_batches(team, imu):
    bus, batches, _ = imu
    band = FakeBand()
    bridge = started(band)
    now = time.time()
    feed(band, 5, now - 0.1)
    bus.drain()
    assert batches == [] and len(bridge._inbox) == 5       # the callback did nothing but append
    bridge._drain()
    bus.drain()
    assert len(batches) == 1 and batches[0].source == "band" and batches[0].configured_hz == 50
    s = batches[0].samples[0]
    assert (s.n, s.az, s.gx) == (0, 1.0, 0.0) and s.t == pytest.approx(s.time * 1000.0)
    bridge.stop()


def test_missing_samples_rate_and_saturation_reach_status(team, imu):
    bus, batches, events = imu
    band = FakeBand()
    bridge = started(band)
    start = time.time() - 1.0
    for i in range(52):                    # 52 a second measured, 50 configured
        if i not in (20, 21, 22):          # three readings lost on the way
            band.motion(i, start + i / 52, ax=8.0 if i == 40 else 0.0)
    bridge._drain()
    status = bridge.status()
    assert status["missing_samples"] == 3 and status["completeness"] == round(49 / 52, 4)
    assert status["effective_hz"] == pytest.approx(52 * 48 / 51, abs=0.5)   # what really arrived
    assert status["saturated"] is True and status["stale"] is False
    bridge._publish_quality()
    bus.drain()
    quality = [e for e in events if e.type == "sensor_quality"][-1]
    assert quality.source == "band"
    assert {"configured_hz", "effective_hz", "missing_samples", "completeness", "saturated",
            "stale"} <= set(quality.payload)
    assert quality.payload["missing_samples"] == 3
    bridge.stop()


def test_switched_off_sensor_becomes_nan_not_zero(team, imu):
    bus, batches, _ = imu
    band = FakeBand()
    bridge = started(band, sensors="accel")
    band.emit("motion", n=0, t=1.0, time=time.time(), ax=0.1, ay=0.0, az=1.0, gx=None, gy=None, gz=None)
    bridge._drain()
    bus.drain()
    s = batches[0].samples[0]
    assert s.ax == 0.1 and math.isnan(s.gx) and math.isnan(s.gz)
    assert bridge.status()["saturated"] is False
    bridge.stop()


# ---------------------------------------------------------------- link loss
def test_reconnect_is_a_gap_and_n_restart_is_not_a_loss(team, imu):
    bus, batches, events = imu
    band = FakeBand()
    bridge = started(band)
    start = time.time() - 5.0
    last = feed(band, 50, start)
    band.emit("reconnecting", reason="lost", seconds=120)
    bridge._drain()
    during = bridge.status()
    assert during["connected"] is False and during["reconnecting"] is True
    assert during["stale"] is True and during["effective_hz"] == 0.0
    assert during["gaps"][-1]["start"] == pytest.approx(last) and during["gaps"][-1]["end"] is None
    band.emit("reconnected", attempts=2, seconds=3.2, failed=[])
    feed(band, 50, time.time() - 0.99)     # the counter starts again at 0 on the new link
    bridge._drain()
    bus.drain()
    after = bridge.status()
    assert after["connected"] is True and after["reconnecting"] is False
    assert after["missing_samples"] == 0 and after["received_samples"] == 100
    gap = after["gaps"][-1]
    assert gap["reason"] == "link-lost" and gap["end"] is not None and gap["end"] - gap["start"] > 3
    assert after["effective_hz"] == pytest.approx(50, abs=0.5)   # the gap is not averaged in
    kinds = [(e.type, e.payload["reason"]) for e in events if e.type.startswith("device_")]
    assert kinds == [("device_connected", "connected"), ("device_disconnected", "lost"),
                     ("device_connected", "reconnected")]
    back = [e for e in events if e.type == "device_connected"][-1]
    assert back.payload["gap"]["start"] == pytest.approx(last) and back.payload["gap"]["end"] is not None
    # nothing was invented for the gap: two batches, only the samples that really arrived
    assert sum(len(b.samples) for b in batches) == 100
    times = [s.time for b in batches for s in b.samples]
    assert max(b - a for a, b in zip(times, times[1:])) > 3
    bridge.stop()


def test_disconnect_gives_a_gap_and_stale_status_not_continuous_data(team, imu):
    bus, batches, events = imu
    band = FakeBand()
    bridge = started(band)
    last = feed(band, 25, time.time() - 0.5)
    band.emit("reconnecting", reason="lost", seconds=120)
    band.emit("disconnect", reason="lost", reconnect="gave-up")
    bridge._drain()
    bus.drain()
    status = bridge.status()
    assert status["connected"] is False and status["reconnecting"] is False and status["stale"] is True
    assert status["effective_hz"] == 0.0
    assert status["gaps"][-1] == {"start": pytest.approx(last), "end": None, "reason": "link-lost"}
    down = [e for e in events if e.type == "device_disconnected"]
    assert [e.payload["reason"] for e in down] == ["lost", "lost (reconnect gave-up)"]
    assert down[0].payload["reconnecting"] is True and down[1].payload["reconnecting"] is False
    count = sum(len(b.samples) for b in batches)
    bridge._drain()
    bridge._publish_quality()
    bus.drain()
    assert sum(len(b.samples) for b in batches) == count == 25      # silence stays silence
    assert [e for e in events if e.type == "sensor_quality"][-1].payload["stale"] is True
    bridge.stop()
    bus.drain()
    assert len([e for e in events if e.type == "device_disconnected"]) == 2   # not announced again


def test_stop_publishes_one_disconnect_and_is_safe_twice(team, imu):
    bus, _, events = imu
    bridge = started(FakeBand())
    bridge.stop()
    bridge.stop()
    bus.drain()
    down = [e for e in events if e.type == "device_disconnected"]
    assert [e.payload["reason"] for e in down] == ["closed"]
    status = bridge.status()
    assert status["connected"] is False and status["source"] is None and status["stale"] is True
    assert bridge.band is None


def test_battery_and_notice_are_tracked(team, imu):
    band = FakeBand()
    bridge = started(band)
    band.emit("battery", percent=81, millivolts=4000, state="battery", charging=False)
    band.emit("notice", kind="stream-dropped", arg=1)
    bridge._drain()
    status = bridge.status()
    assert status["battery"] == 81 and status["last_notice"]["kind"] == "stream-dropped"
    bridge.stop()


def test_flush_thread_publishes_batches_and_quality_by_itself(team, imu):
    bus, batches, events = imu
    band = FakeBand()
    bridge = started(band, hold=False)
    end = time.time() + 1.6
    n = 0
    while time.time() < end:
        band.motion(n, time.time())
        n += 1
        time.sleep(0.02)
    bus.drain()
    assert len(batches) >= 5 and all(b.source == "band" for b in batches)   # about ten a second
    assert max(len(b.samples) for b in batches) < n
    assert any(e.type == "sensor_quality" and not e.payload["stale"] for e in events)
    bridge.stop()


# ---------------------------------------------------------------- LED and microphone
def test_led_is_queued_while_the_microphone_is_on(team, imu):
    band = FakeBand()
    bridge = started(band)
    assert bridge.set_led("#00ff00") == {"applied": True, "queued": False}
    with bridge.microphone() as mic_band:
        assert mic_band is band
        band.streams["audio"] = True                       # what speech.listen switches on
        assert bridge.set_led("#ff0000", pattern="blink", period=500) == {"applied": False, "queued": True}
        assert bridge.set_led("#ff8800") == {"applied": False, "queued": True}   # last one wins
        assert band.led == ("#00ff00", 1.0, "solid", 1000) and bridge.status()["led_queued"] is True
        band.streams["audio"] = False
    assert band.led == ("#ff8800", 1.0, "solid", 1000) and bridge.status()["led_queued"] is False
    bridge.stop()


def test_led_denied_by_the_band_is_queued_and_no_band_is_an_error(team, imu):
    band = FakeBand()
    bridge = started(band)
    band.streams["mic_level"] = True                       # microphone on without the context manager
    assert bridge.led_off()["queued"] is True
    band.streams["mic_level"] = False
    bridge._apply_pending_led()
    assert band.led == "off"
    bridge.stop()
    with pytest.raises(BridgeError):
        bridge.set_led("#ffffff")
    with pytest.raises(BridgeError):
        with bridge.microphone():
            pass


def test_no_band_number_literal_in_band_code():
    """The contract: the number comes only from config.band_number()."""
    import re
    from pathlib import Path
    backend = Path(vexo_bridge.__file__).parent
    files = [backend / n for n in ("vexo_bridge.py", "recorder.py", "replay.py", "routers/band.py")]
    for path in files:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"Vexo-HACK-\d|VEXO_BAND\s*=\s*\d|Band\.connect\(\s*\d|band_number\s*=\s*\d", text), path
        assert "os.environ" not in text or "VEXO_BAND" not in text, path


def test_after_a_final_disconnect_the_bridge_lets_go_and_can_connect_again(team, imu):
    bus, _, events = imu
    band = FakeBand()
    bridge = started(band)
    band.emit("disconnect", reason="lost", reconnect="gave-up")
    bridge._drain()
    status = bridge.status()
    assert bridge.band is None and status["source"] is None and status["connected"] is False
    again = FakeBand()
    bridge._connect = lambda number, transport, auto_reconnect=True: again
    assert bridge.start()["connected"] is True       # not "already-running"
    bridge.stop()
