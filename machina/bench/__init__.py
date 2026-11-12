"""Benchmark infrastructure: canonical task manifests and run evidence.

This package is deliberately provider- and benchmark-agnostic. Adapters belong
to the suites; the ledger only records immutable task identity, model/harness
configuration, outcomes, and cost/latency evidence.
"""

from machina.bench.ledger import (
    Ledger,
    RunRecord,
    TaskLockbox,
    TaskSpec,
    canonical_hash,
)

__all__ = [
    "Ledger",
    "RunRecord",
    "TaskSpec",
    "TaskLockbox",
    "canonical_hash",
]
