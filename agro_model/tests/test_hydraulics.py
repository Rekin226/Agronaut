"""Independent analytic/implicit references for full-pipe hydraulics."""

from math import log10, pi, sqrt

import pytest

from agro_model.hydraulics import GRAVITY_M_S2, Pipe, darcy_factor, pipe_loss


def test_poiseuille_laminar_loss_and_zero_flow():
    pipe = Pipe(10, 0, 100, "analytic test")
    # Hagen-Poiseuille pressure gradient converted to head; no use of darcy_factor.
    q, length, nu, d = 0.5 / 3_600_000, 12, 1e-6, 0.01
    expected = 128 * nu * length * q / (pi * GRAVITY_M_S2 * d**4)
    loss = pipe_loss(pipe, flow_lph=0.5, length_m=length, viscosity_m2_s=nu)
    assert loss["reynolds"] < 2000
    assert loss["total_head_m"] == pytest.approx(expected, rel=1e-12)
    zero = pipe_loss(pipe, flow_lph=0, length_m=12, viscosity_m2_s=nu, minor_k=10)
    assert zero["total_head_m"] == 0
    assert zero["darcy_factor"] is None


@pytest.mark.parametrize("re", [5000, 10000, 100000, 1e6])
@pytest.mark.parametrize("roughness", [0, 0.0001, 0.001, 0.01])
def test_swamee_jain_against_implicit_colebrook(re, roughness):
    # Independently solve Colebrook rather than duplicate the explicit approximation.
    f = 0.02
    for _ in range(100):
        f = 1 / (-2 * log10(roughness / 3.7 + 2.51 / (re * sqrt(f))))**2
    assert darcy_factor(re, roughness) == pytest.approx(f, rel=0.03)


@pytest.mark.parametrize("roughness", [0, 0.001, 0.05])
def test_transition_continuity_and_monotonic_loss(roughness):
    for boundary in (2000, 4000):
        assert darcy_factor(boundary-1e-4, roughness) == pytest.approx(
            darcy_factor(boundary+1e-4, roughness), rel=1e-6)
    # Loss is proportional to f*Re² at fixed diameter/viscosity; needed for flow bounds.
    losses = [darcy_factor(re, roughness)*re**2 for re in range(1500, 4501, 10)]
    assert losses == sorted(losses)


def test_minor_loss_uses_actual_pipe_velocity_and_darcy_not_fanning():
    p = Pipe(25, 0.001, 40, "test")
    q, k, length = 500, 3, 20
    out = pipe_loss(p, flow_lph=q, length_m=length, viscosity_m2_s=1e-6, minor_k=k)
    velocity = q / 3_600_000 / (pi*0.025**2/4)
    assert out["minor_head_m"] == pytest.approx(k*velocity**2/(2*GRAVITY_M_S2))
    assert out["friction_head_m"] == pytest.approx(
        out["darcy_factor"]*length/0.025*velocity**2/(2*GRAVITY_M_S2))


@pytest.mark.parametrize("value", [True, -1, float("nan"), float("inf"), 10**1000])
def test_invalid_flow_is_rejected(value):
    with pytest.raises(ValueError):
        pipe_loss(Pipe(16, 0, 30, "test"), flow_lph=value, length_m=10, viscosity_m2_s=1e-6)
