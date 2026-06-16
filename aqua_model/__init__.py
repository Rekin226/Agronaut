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
from .validate import validate_design_input, ValidationError
from .types import DesignInput, DesignOutput, CoefficientUse

__all__ = [
    "size_system",
    "validate_design_input",
    "ValidationError",
    "DesignInput",
    "DesignOutput",
    "CoefficientUse",
]

__version__ = "0.1.0"
