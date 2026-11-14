"""Talk to a Vexo Open band from Python (Bluetooth, USB cable, or a simulator).

    from vexo_open.sync import Band      # plain calls, no async
    from vexo_open import Band           # async version
"""
from . import codec
from .band import Band, Message
from .codec import CONTRACT_REVISION, EVENT_KINDS, LED_PATTERNS, NAME_PREFIX, SERVICE_UUID, band_id, band_name
from .transport import Transport, VexoError
from .transports.sim import SimTransport

__all__ = ["Band", "Message", "Transport", "VexoError", "SimTransport", "band_name", "band_id", "NAME_PREFIX",
           "SERVICE_UUID", "CONTRACT_REVISION", "EVENT_KINDS", "LED_PATTERNS", "codec"]
__version__ = "0.2.0"
