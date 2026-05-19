"""Workers — concrete adapters that turn prompts into outputs.

Every worker:
  * Is registered with a billing model in ``teamnot.safety`` (metered, subscription, local)
  * Goes through ``CostGuard.gate(...)`` for every call
  * Returns plain strings or JSON to the engine — no agent state of its own

The registry lets the engine pick a worker by name at runtime instead of
hard-coding which model does what.
"""
from teamnot.workers.claude_cli import ClaudeCliWorker
from teamnot.workers.minimax import MinimaxWorker

__all__ = ["ClaudeCliWorker", "MinimaxWorker"]
