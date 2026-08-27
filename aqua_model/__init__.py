"""aqua_model — deterministic aquaponics design & sizing core.

This package is the TRUST ZONE. It is pure Python: no LLM, no network, no Ollama,
no imports from `srcs/`. Every number it produces is traceable to a cited
coefficient (see `coefficients.py`) and every design states what it does NOT model.

Public API:
    size_system(DesignInput) -> DesignOutput   # the calculator
    validate_design_input(...) -> DesignInput  # the trust gate

Design rules (from the approved design doc + eng/CEO reviews):
  - FRR (feeding-rate ratio) is the single SIZING rule. The nitrogen balance is an
    independent CONSISTENCY CHECK, never a second sizing path.
  - Coefficients carry value + range + unit + source. They are seed defaults meant to
    be CALIBRATED against a real system, not universal constants.
  - Every output lists what is NOT modeled. Calibration != validation.
"""

from .hydroponics import size_hydroponic_system
from .optimizer import OBJECTIVES, Candidate, OptimizeInput, OptimizeResult, optimize
from .pilot import PilotInfo, projected_outcomes, to_pilot_proposal
from .sizing import size_system
from .triage import (
    ObservationFeatures,
    TriageCandidate,
    TriageResult,
    format_triage,
    triage_symptoms,
    validate_observation_features,
)
from .types import (
    CoefficientUse,
    DesignInput,
    DesignOutput,
    HydroponicInput,
    HydroponicOutput,
)
from .validate import (
    ValidationError,
    validate_design_input,
    validate_hydroponic_input,
    validate_optimize_input,
)

__all__ = [
    "size_system",
    "size_hydroponic_system",
    "PilotInfo",
    "to_pilot_proposal",
    "projected_outcomes",
    "optimize",
    "OptimizeInput",
    "OptimizeResult",
    "Candidate",
    "OBJECTIVES",
    "validate_design_input",
    "validate_hydroponic_input",
    "validate_optimize_input",
    "validate_observation_features",
    "triage_symptoms",
    "format_triage",
    "ObservationFeatures",
    "TriageCandidate",
    "TriageResult",
    "ValidationError",
    "DesignInput",
    "DesignOutput",
    "CoefficientUse",
    "HydroponicInput",
    "HydroponicOutput",
]

__version__ = "0.1.0"
