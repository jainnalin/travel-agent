# travel_agent/orchestrator/bus/base.py
"""
Message Bus Base Interface for Travel Agent Orchestrator.

This module defines the abstract interface for message bus implementations
used for inter-component communication within the travel agent system.
"""

from __future__ import annotations
from typing import Callable

# Type alias for message bus event handlers
BusHandler = Callable[[object], None]


class BusBase:
    """
    Abstract base class for message bus implementations.
    
    Provides publish/subscribe pattern for loose coupling between
    orchestrator components. Implementations should handle
    thread-safe message delivery and handler management.
    """
    
    def subscribe(self, topic: str, handler: BusHandler) -> None:
        """Subscribe a handler to receive messages on a specific topic."""
        raise NotImplementedError

    def publish(self, topic: str, message: object) -> None:
        """Publish a message to all subscribers of a specific topic."""
        raise NotImplementedError
