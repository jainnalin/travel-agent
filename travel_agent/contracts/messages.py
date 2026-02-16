# travel-agent/contracts/messages.py

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass(frozen=True)
class Message:
    topic: str
    payload: Dict[str, Any]
