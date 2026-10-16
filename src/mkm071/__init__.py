"""Library for the LTC MKM071 mouse (Rongyuan family, model id 1833), wired config channel.

See docs/protocol.md for the protocol and docs/roadmap.md for what is planned.
"""
from .device import Mkm071, WriteNotApplied
from .events import Event, decode_event
from .frame import MKM071_ID, UnsafeCommand
from .model import (ButtonAction, DeviceState, DpiBlock, LightRecord, Macro, Matrix, Opt0,
                    Profile)

__version__ = "0.1.0.dev0"

__all__ = ["Mkm071", "WriteNotApplied", "UnsafeCommand", "MKM071_ID", "Event", "decode_event",
           "ButtonAction", "DeviceState", "DpiBlock", "LightRecord", "Macro", "Matrix", "Opt0",
           "Profile", "__version__"]
