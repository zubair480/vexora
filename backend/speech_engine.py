"""Push-to-talk capture. Owned by the be-voice session (see CONTRACT.md).

Two ways to get a transcript:

- `capture(band, seconds)` wraps the kit's `speech.listen(band, seconds, on_text=...)` exactly
  as shipped (Deepgram, key `DEEPGRAM_API_KEY`). The band is the already-connected
  `vexo_open.sync.Band` owned by the be-band bridge: this module never connects, never reads the
  band number and never disconnects.
- `transcribe_typed(text)` for typed text: no band, no Deepgram key, no network.

LED rule (kit AGENTS.md rule 4): while the microphone streams the light is locked solid blue and
`set_led` / `led_off` / `identify` are refused with code `denied`. So nothing here touches the LED
during a capture; other modules hand their LED updates to `queue_led_update(...)`, which holds
them until the microphone is off and then runs them in order.

Nothing is published while listening. `speech.listen` blocks for `seconds`: call `capture` from a
worker thread (a sync FastAPI endpoint), never from a `band.on` handler.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

from . import config

log = logging.getLogger("beltguard.speech")

MAX_SECONDS = 30.0

_capture_lock = threading.Lock()      # one capture at a time: there is one microphone
_state_lock = threading.Lock()
_listening = False
_pending_led: list[Callable[[], None]] = []


class SpeechCaptureError(RuntimeError):
    """Capture could not produce a transcript. `code` is short and stable; the message is honest."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def is_listening() -> bool:
    return _listening


def queue_led_update(action: Callable[[], None]) -> bool:
    """Run an LED update now, or after the capture if the microphone is on.

    Returns True if it was queued (LED locked blue), False if it ran straight away.
    """
    with _state_lock:
        if _listening:
            _pending_led.append(action)
            return True
    action()
    return False


def _flush_led() -> None:
    with _state_lock:
        actions = list(_pending_led)
        _pending_led.clear()
    for action in actions:
        try:
            action()
        except Exception:
            log.exception("queued LED update failed")


def _kit_speech():
    config.add_kit_to_path()
    import speech   # the kit's helper, unmodified
    return speech


def transcribe_typed(text: Optional[str]) -> str:
    """The no-band path: a typed report becomes the transcript, whitespace tidied."""
    return " ".join((text or "").split())


def capture(band, seconds: float, on_text: Optional[Callable[[str, bool], None]] = None) -> str:
    """Listen through the band's microphone for `seconds` and return the final transcript.

    `on_text(text, final)` is called on the kit's WebSocket thread with interim and final text;
    it must only enqueue. Raises `SpeechCaptureError` (no band, no key, Deepgram refused, band
    error, already listening). An empty string means nothing was heard: it is never replaced
    with a guess.
    """
    global _listening
    if band is None:
        raise SpeechCaptureError("no-band", "No band is connected, so there is no microphone to "
                                 "listen with. Connect the band first, or type the report.")
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        raise SpeechCaptureError("bad-seconds", "seconds must be a number.") from None
    if not 0 < seconds <= MAX_SECONDS:
        raise SpeechCaptureError("bad-seconds", "seconds must be above 0 and at most %g." % MAX_SECONDS)
    if not _capture_lock.acquire(blocking=False):
        raise SpeechCaptureError("busy", "The microphone is already listening. Wait for it to finish.")
    try:
        config.load_env()                 # DEEPGRAM_API_KEY from the repo's .env, if not in the env
        speech = _kit_speech()
        with _state_lock:
            _listening = True             # LED is locked blue from here: LED updates are queued
        try:
            text = speech.listen(band, seconds, on_text=on_text)
        except Exception as error:        # SpeechError / VexoError / OSError from the kit
            code = getattr(error, "code", None) or "speech"
            message = getattr(error, "message", None) or str(error) or error.__class__.__name__
            raise SpeechCaptureError(str(code), str(message)) from error
        return " ".join((text or "").split())
    finally:
        with _state_lock:
            _listening = False
        _capture_lock.release()
        _flush_led()
