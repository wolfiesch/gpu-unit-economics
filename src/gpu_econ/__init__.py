"""GPU unit-economics model.

Public API re-exports the input contract and the five calculation entrypoints.
Calculation modules are filled in by their own files; imports are guarded so the
package is importable as modules land.
"""

from __future__ import annotations

from .inputs import (
    B200,
    DEFAULT_GPUS,
    H100,
    H200,
    DataCenterAssumptions,
    GPUSpec,
    Scenario,
    WorkloadAssumptions,
)

__all__ = [
    "GPUSpec",
    "DataCenterAssumptions",
    "WorkloadAssumptions",
    "Scenario",
    "H100",
    "H200",
    "B200",
    "DEFAULT_GPUS",
]
