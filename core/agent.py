"""
Central agent orchestrator for AgentT.
Coordinates all modules via the event bus and manages lifecycle.
"""

import logging

from core.events import EventBus, Event, FILE_ARRIVED, ERROR_OCCURRED
from core.audit import log_action

logger = logging.getLogger(__name__)


class AgentT:
    """
    Main agent that ties all modules together.
    Modules register with the agent and subscribe to events.
    """

    def __init__(self):
        self.event_bus = EventBus()
        self._modules = {}
        self._running = False

        # Log all errors
        self.event_bus.subscribe(ERROR_OCCURRED, self._handle_error)

    def register_module(self, name: str, module):
        """Register a module with the agent. Module must have a setup(event_bus) method."""
        self._modules[name] = module
        if hasattr(module, "setup"):
            module.setup(self.event_bus)
        logger.info(f"Module '{name}' registered")

    def start(self):
        """Start all modules that have a start() method."""
        self._running = True
        log_action("agent", "agent_started", detail={"modules": list(self._modules.keys())})
        logger.info(f"AgentT starting with modules: {list(self._modules.keys())}")

        for name, module in self._modules.items():
            if hasattr(module, "start"):
                try:
                    module.start()
                    logger.info(f"Module '{name}' started")
                except Exception as e:
                    logger.error(f"Failed to start module '{name}': {e}")

    def stop(self):
        """Stop all modules that have a stop() method."""
        self._running = False
        for name, module in self._modules.items():
            if hasattr(module, "stop"):
                try:
                    module.stop()
                    logger.info(f"Module '{name}' stopped")
                except Exception as e:
                    logger.error(f"Failed to stop module '{name}': {e}")

        log_action("agent", "agent_stopped")
        logger.info("AgentT stopped")

    def _handle_error(self, event: Event):
        """Log errors from any module."""
        log_action(
            module="agent",
            action="module_error",
            detail=event.data,
            severity="error",
        )
