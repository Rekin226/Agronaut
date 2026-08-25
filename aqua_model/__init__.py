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

from .sizing import size_system
from .hydroponics import size_hydroponic_system
from .pilot import PilotInfo, to_pilot_proposal, projected_outcomes
from .optimizer import optimize, OptimizeInput, OptimizeResult, Candidate, OBJECTIVES
from .validate import (
    validate_design_input,
    validate_hydroponic_input,
    validate_optimize_input,
    ValidationError,
)
from .triage import (
    ObservationFeatures, TriageCandidate, TriageResult, format_triage, triage_symptoms,
    validate_observation_features,
)
from .types import (
    DesignInput, DesignOutput, CoefficientUse, HydroponicInput, HydroponicOutput,
)
from .twin import TwinParams, TwinState, mature_biofilter, simulate
from .scenario import Intervention, compare, format_comparison, run_scenario
from .climate import DailyClimate, GreenhouseParams, from_records
from .fishgrowth import Cohort, days_to_weight
from .production import (
    ProductionParams, ProductionState, format_summary, simulate_production, start_state,
)
from .layout import Layout, plan_layout
from .scene3d import to_scene

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
    # the time dimension: nitrogen twin, scenarios, and the production twin
    "TwinParams",
    "TwinState",
    "simulate",
    "mature_biofilter",
    "Intervention",
    "run_scenario",
    "compare",
    "format_comparison",
    "DailyClimate",
    "GreenhouseParams",
    "from_records",
    "Cohort",
    "days_to_weight",
    "ProductionParams",
    "ProductionState",
    "simulate_production",
    "start_state",
    "format_summary",
    # space: layout and the 3D scene
    "Layout",
    "plan_layout",
    "to_scene",
]

__version__ = "0.1.0"
