"""A transport moves whole frames between the computer and one band. It knows nothing
about what the frames mean (that is codec.py). To add a new way to reach a band,
subclass Transport and pass an instance to Band.connect(number, transport=...).
"""
from typing import Callable


class VexoError(Exception):
    """Something went wrong talking to a band. .code is a short reason such as "timeout";
    the message says what to do about it."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# The first thing to check when a band cannot be found over Bluetooth. A band that is
# connected to anything is invisible to everything else, so a double-tap alone cannot help
# a second program.
ELSEWHERE_HELP = ("If something else is connected to it (the dashboard tab, another program, a teammate's "
                  "laptop), close that first: the band takes one Bluetooth connection at a time and cannot "
                  "be found while it is connected.")


def not_found_help(name: str) -> str:
    """What to tell someone whose band cannot be found over Bluetooth, most likely reason first."""
    return ("Could not see %s. %s Then double-tap the band (its light blinks blue for a minute), keep it "
            "close, and try again." % (name, ELSEWHERE_HELP))


class Transport:
    kind = "custom"  # "ble", "serial" or "sim" for the built-in ones

    def __init__(self) -> None:
        # Band assigns these. Call on_frame once per frame received, and on_close when
        # the link drops. Both must be called on the asyncio event loop thread.
        self.on_frame: Callable[[bytes], None] = lambda frame: None
        self.on_close: Callable[[str], None] = lambda reason: None
    # Optional, for a link on which the band can end the session by itself while the link
    # stays open (the USB cable): set session_watch = True, and give the transport an
    # on_session_hint attribute (Band assigns it; call it on the event loop thread when an
    # empty packet arrives in the middle of a session). Band then watches for streams that
    # went silent and ends the link with reason "session-ended"
    # (transports/session_watch.py). Without it nothing is watched and nothing extra is sent.

    async def open(self) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        pass

    async def send(self, frame: bytes) -> None:
        raise NotImplementedError
