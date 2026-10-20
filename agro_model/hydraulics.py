"""Full-pipe Darcy-Weisbach losses in SI units, following EPANET 2.2.

Friction-factor equations: EPA EPANET 2.2 manual, section 12.1, Pipe Resistance
Coefficient. These are Darcy factors (four times the Fanning factor).
No material roughness, liquid viscosity or fitting losses are inferred.
"""

from dataclasses import dataclass
from math import log10, pi

from .validation import number, text

EPA_ALGORITHMS = "https://usepa.github.io/EPANET2.2/12_analysis_algorithms.html"
EPA_NETWORK = "https://usepa.github.io/EPANET2.2/3_network_model.html"
GRAVITY_M_S2 = 9.80665  # conventional standard gravity, not a local measurement


@dataclass(frozen=True)
class Pipe:
    internal_diameter_mm: float
    roughness_mm: float
    max_head_m: float
    source: str

    def __post_init__(self):
        # Software envelope, not recommended equipment sizes or design limits.
        number("internal_diameter_mm", self.internal_diameter_mm, 1, 2000)
        number("roughness_mm", self.roughness_mm, 0, self.internal_diameter_mm * 0.05)
        number("max_head_m", self.max_head_m, high=10000, positive=True)
        text("pipe source", self.source)


def darcy_factor(reynolds: float, relative_roughness: float) -> float:
    """EPANET laminar, Dunlop transition and Swamee-Jain turbulent equations."""
    re = number("reynolds", reynolds, high=1e12, positive=True)
    rr = number("relative_roughness", relative_roughness, 0, 0.05)
    if re <= 2000:
        return 64 / re
    if re >= 4000:
        return 0.25 / log10(rr / 3.7 + 5.74 / re**0.9)**2
    r = re / 2000
    aa, ab = -1.5634601348517065795, 0.00328895476345399058690
    y2 = rr / 3.7 + ab
    y3 = -2 * log10(y2)
    fa = y3**-2
    fb = fa * (2 - aa * ab / (y2 * y3))
    x1 = 7 * fa - fb
    x2 = 0.128 - 17 * fa + 2.5 * fb
    x3 = -0.128 + 13 * fa - 2 * fb
    x4 = 0.032 - 3 * fa + 0.5 * fb
    return x1 + r * (x2 + r * (x3 + r * x4))


def pipe_loss(pipe: Pipe, *, flow_lph: float, length_m: float,
              viscosity_m2_s: float, minor_k: float = 0) -> dict:
    """Return friction and local head losses; zero flow has zero loss and no factor."""
    q = number("flow_lph", flow_lph, 0, 1e9) / 3_600_000
    length = number("length_m", length_m, 0, 1e6)
    viscosity = number("viscosity_m2_s", viscosity_m2_s, 1e-8, 1e-3)
    k = number("minor_k", minor_k, 0, 1e6)
    d = pipe.internal_diameter_mm / 1000
    velocity = q / (pi * d*d / 4)
    re = velocity * d / viscosity
    factor = darcy_factor(re, pipe.roughness_mm / pipe.internal_diameter_mm) if q else None
    # The linear laminar expression avoids 0 * infinity for tiny positive flows.
    friction = (128 * length * viscosity * q / (pi * GRAVITY_M_S2 * d**4)
                if re <= 2000 else factor * length / d * velocity**2 / (2 * GRAVITY_M_S2))
    minor = k * velocity**2 / (2 * GRAVITY_M_S2)
    result = dict(friction_head_m=friction, minor_head_m=minor,
                  total_head_m=friction + minor, velocity_m_s=velocity,
                  reynolds=re, darcy_factor=factor)
    for key, value in result.items():
        if value is not None:
            number(key, value, 0)
    return result


def distributed_outlet_losses(pipe, count, spacing, outlet_lph, viscosity, insertion_k):
    """Loss to first/last outlet and peak velocity; half-spacing entry, closed tail.

    Internal network primitive: callers validate integer counts and geometry.
    Each through-fitting uses the upstream segment's velocity.
    """
    first = total = peak_velocity = 0.0
    for i in range(count):
        loss = pipe_loss(pipe, flow_lph=(count - i) * outlet_lph,
                         length_m=spacing * (0.5 if i == 0 else 1),
                         viscosity_m2_s=viscosity, minor_k=insertion_k)
        total += loss["total_head_m"]
        if i == 0:
            first = total
            peak_velocity = loss["velocity_m_s"]
    return first, total, peak_velocity
