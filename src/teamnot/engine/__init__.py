"""TeamNoT engine — pipeline orchestration.

The engine ties Brief + Workspace + workers + DoD together. It owns the
"run_until_done" loop and the multi-phase pipeline (plan → implement → test →
review → document).

Public entry points:

    from teamnot.engine import Worker
    result = Worker(brief).run_until_done()
"""
from teamnot.engine.worker import Worker, WorkerResult

__all__ = ["Worker", "WorkerResult"]
