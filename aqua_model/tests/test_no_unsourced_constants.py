"""Trust-zone rule 2, machine-checked: every number carries a source.

`coefficients.py` is the registry where cited numbers live. This test walks every
other module in `aqua_model/` with `ast` and asserts that no module-level numeric
constant sits outside the registry without an explicit, reasoned exemption in
ALLOWLIST below.

The allowlist is the point, not a chore: it forces anyone keeping a number outside
the registry to say in one line why it is not a coefficient. Geometry conventions,
unit conversions, sensor-QC policy and drawing constants are exempt with a reason;
known debt is listed with the issue that tracks its migration. A new constant with
no entry fails CI, so the rule no longer depends on a reviewer noticing.

This guards the form, not the truth: a sourced number can still be the wrong
number. The test makes the rule visible; a reviewer still reads the source.
"""

import ast
from pathlib import Path

AQUA_MODEL_DIR = Path(__file__).resolve().parent.parent

# The registry itself: numbers live here as Coefficient(...) with a source.
REGISTRY_MODULE = "coefficients"

# {(module_stem, constant_name): one-line reason it is not a registry coefficient}
ALLOWLIST = {
    # advisory.py — alert bands and operator actions, deliberately coarse
    ("advisory", "MEASUREMENT_FRESH_DAYS"):
        "advice-freshness window for logged readings; an operations policy, not a physical constant",
    ("advisory", "TAN_ACT_MG_L"):
        "advisory alert band backed by the nitrogen-cycle knowledge base; coarse by design (see comment)",
    ("advisory", "TAN_URGENT_MG_L"):
        "one nitrifier doubling above the act tier; the knowledge base gives no second number",
    ("advisory", "NO2_ACT_MG_L"):
        "advisory alert band backed by the nitrogen-cycle knowledge base; coarse by design (see comment)",
    ("advisory", "NO2_URGENT_MG_L"):
        "one nitrifier doubling above the act tier; the knowledge base gives no second number",
    ("advisory", "NO3_HIGH_MG_L"):
        "advisory alert band backed by the nitrogen-cycle knowledge base; coarse by design (see comment)",
    ("advisory", "NO3_LOW_MG_L"):
        "advisory alert band backed by the nitrogen-cycle knowledge base; coarse by design (see comment)",
    ("advisory", "RATION_REDUCED"):
        "feeding action an operator can perform with a scoop; coarser than the model could resolve",
    ("advisory", "RATION_HELD"):
        "feeding action an operator can perform with a scoop; coarser than the model could resolve",
    ("advisory", "FEED_PAUSE_MAX_HOURS"):
        "cited safe feeding-pause window before escalation to a human (triage rule)",
    # business.py — arithmetic hygiene
    ("business", "_MATERIAL_MARGIN"):
        "float-comparison epsilon for material totals; arithmetic hygiene, not a quantity",
    # climate.py — cited unit conversion
    ("climate", "PAR_MOL_PER_MJ_GLOBAL"):
        "unit conversion MJ to mol PAR with in-line citation (McCree 1972; Thimijan & Heins 1983)",
    # costing.py — named in #131
    ("costing", "_PIPE_FITTING_FACTOR"):
        "fitting allowance on straight pipe runs; known debt, registry migration tracked in #131",
    # cropgrowth.py — guardrail
    ("cropgrowth", "_MAX_OVER_CITED"):
        "cap on how far better-than-reference conditions may beat the cited yield; guardrail, not an input",
    # datasets.py — sensor-QC policy
    ("datasets", "_SATURATION_THRESHOLD"):
        "sensor-QC policy: share of rail-pinned readings that marks a channel saturated (cheap-sensor signature)",
    ("datasets", "_SENTINEL_THRESHOLD"):
        "sensor-QC policy: share of dead-sensor sentinel readings that disqualifies a channel",
    ("datasets", "_IMPLAUSIBLE_THRESHOLD"):
        "sensor-QC policy: share of implausible readings that disqualifies a channel",
    # fishgrowth.py — guardrail
    ("fishgrowth", "_MAX_PCT_BW_DAY"):
        "guardrail cap on feeding rate; feed charts rarely exceed it and beyond it feed is wasted",
    # flowsheet.py — architecture routing thresholds, each reasoned in place
    ("flowsheet", "MEDIA_BED_SELF_FILTER_MAX_KG_M3"):
        "FAO 589 ceiling guidance for media-bed self-filtration (cited in the comment above the constant)",
    ("flowsheet", "INTENSIVE_KG_M3"):
        "stocking threshold above which the flowsheet adds degassing (UVI commercial practice)",
    ("flowsheet", "COMMERCIAL_AREA_M2"):
        "scale threshold where FAO 589 ch. 8 decoupled arguments start to outweigh coupled simplicity",
    ("flowsheet", "MIN_BAND_COVERAGE"):
        "fish/crop band-overlap threshold for the coupled-vs-decoupled architecture decision",
    # hydraulics.py — plumbing rules of thumb and physical constants
    ("hydraulics", "MIN_FALL_M"):
        "minimum gravity-leg drop; plumbing rule of thumb (level tolerance eats smaller grades)",
    ("hydraulics", "SLOPE"):
        "1% fall per metre, the ordinary gravity drain rule; shallower silts up with solids",
    ("hydraulics", "CLEARANCE_M"):
        "router clearance to vessel walls; a routing convention, not a physical quantity",
    ("hydraulics", "GRID_M"):
        "router resolution; a speed/precision trade for the path planner, not a coefficient",
    ("hydraulics", "PIPE_RUN_H_M"):
        "default height of the horizontal run; a routing convention",
    ("hydraulics", "TARGET_VELOCITY_MS"):
        "design velocity inside the 0.6-1.5 m/s settle/erosion band stated in its docstring",
    ("hydraulics", "STANDARD_OD_MM"):
        "metric PVC pressure-pipe catalogue, the sizes actually stocked; a lookup table, not a parameter",
    ("hydraulics", "DARCY_F"):
        "fixed Darcy friction factor for smooth PVC; stated simplification over a Colebrook solve",
    ("hydraulics", "FITTING_EQUIV_LENGTHS_M"):
        "equivalent length per direction change; standard minor-loss estimation practice",
    ("hydraulics", "G"):
        "standard gravity, a defined physical constant",
    # layout.py — greenhouse workspace geometry (engineering conventions)
    ("layout", "AISLE_M"):
        "workspace geometry: aisle width for access; ergonomic convention, no physics computes from it",
    ("layout", "MARGIN_M"):
        "workspace geometry: component-to-wall clearance; ergonomic convention",
    ("layout", "WALL_H_M"):
        "workspace geometry: greenhouse wall height; structural convention",
    ("layout", "RIDGE_MIN_H_M"):
        "workspace geometry: minimum ridge height; structural convention",
    ("layout", "TANK_DEPTH_M"):
        "workspace geometry: reachable rearing-tank depth (arm + net); ergonomic convention",
    ("layout", "TANK_MAX_M3"):
        "workspace geometry: tank size above which splitting is preferred; handling/redundancy convention",
    ("layout", "TANK_MAX_COUNT"):
        "workspace geometry: tank count ceiling; handling/redundancy convention",
    ("layout", "BED_WIDTH_M"):
        "workspace geometry: standard raft/media bed width (reach from one side); industry standard",
    ("layout", "BED_MAX_LENGTH_M"):
        "workspace geometry: bed length ceiling; ergonomic convention",
    ("layout", "NFT_BENCH_H_M"):
        "workspace geometry: bench working height; ergonomic convention",
    ("layout", "MEDIA_BED_STAND_H_M"):
        "workspace geometry: media-bed stand height; ergonomic convention",
    ("layout", "TOWER_H_M"):
        "workspace geometry: tower height; convention",
    ("layout", "TOWER_ROW_SPACING_M"):
        "workspace geometry: tower row spacing; convention",
    # massbalance.py — the nitrogen split, named in #131
    ("massbalance", "SOLIDS_REMOVAL_FRACTION"):
        "nitrogen sink split; known debt, registry migration tracked in #131",
    ("massbalance", "WATER_EXCHANGE_FRACTION"):
        "nitrogen sink split; known debt, registry migration tracked in #131",
    ("massbalance", "DENITRIFICATION_FRACTION"):
        "nitrogen sink split; known debt, registry migration tracked in #131",
    # optimizer.py — algorithm parameter
    ("optimizer", "_ALLOC_STEPS"):
        "bounded-enumeration granularity for the area-split search; algorithmic parameter, not physics",
    # reference_systems.py — calibration gate
    ("reference_systems", "FEED_TOLERANCE"):
        "tolerance band of the original calibration gate, kept so results stay comparable",
    # scenario.py — reporting precision policy
    ("scenario", "_MATERIAL_MG_L"):
        "reporting floor below which relative change is meaningless; precision policy, not physics",
    # scene3d.py — drawing conventions, nothing computes from them
    ("scene3d", "FISH_DRAWN_PER_TANK"):
        "3D rendering: fish drawn per tank (population scaled to the real count); nothing computes from it",
    ("scene3d", "PLANT_SCALE_FLOOR"):
        "3D rendering: minimum drawn plant size under limitation; nothing computes from it",
    ("scene3d", "FULTON_K"):
        "3D rendering: condition factor for drawn body length; a drawing convention per its comment",
    ("scene3d", "DEFAULT_MAX_FRAMES"):
        "3D rendering: default animation frame cap; a presentation default",
    # triage.py — priority ordering, only relative order is meaningful
    ("triage", "_P_ENVIRONMENT"):
        "triage priority band; only the ordering of the four bands is meaningful",
    ("triage", "_P_AVAILABILITY"):
        "triage priority band; only the ordering of the four bands is meaningful",
    ("triage", "_P_SUPPLY"):
        "triage priority band; only the ordering of the four bands is meaningful",
    ("triage", "_P_PATHOGEN"):
        "triage priority band; only the ordering of the four bands is meaningful",
    # twin.py — transient-twin kinetics
    ("twin", "_AOB_DOUBLING_DAYS"):
        "nitrifier population kinetics for the transient twin (cycling/ammonia-peak timing); not a sizing input",
    ("twin", "_NOB_DOUBLING_DAYS"):
        "nitrifier population kinetics for the transient twin (nitrite-peak timing); not a sizing input",
    ("twin", "_SEED_CAPACITY_G_DAY"):
        "trace nitrifier population at cycling start; transient-twin initialisation",
    ("twin", "_N_REMOVAL_PER_DAY"):
        "first-order nitrate removal rate; ratio fixed by the steady-state split, magnitude sets equilibration speed",
}


def _bare_numbers(node):
    """The numeric value(s) of a bare numeric literal, else None.

    Accepts int/float constants (not bool), their negation, and tuples/lists
    whose elements are all numeric. Anything computed (a Call, a BinOp, a name)
    is not a declared constant and is out of scope here.
    """
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            return None
        return [node.value]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _bare_numbers(node.operand)
        return [-v for v in inner] if inner is not None else None
    if isinstance(node, (ast.Tuple, ast.List)):
        vals = []
        for elt in node.elts:
            got = _bare_numbers(elt)
            if got is None:
                return None
            vals.extend(got)
        return vals or None
    return None


def _module_level_constants(source):
    """{CONST_NAME: line_number} for module-level bare-numeric assignments."""
    found = {}
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if _bare_numbers(value) is None:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and target.id == target.id.upper():
                found[target.id] = node.lineno
    return found


def _scan_package():
    """{(stem, name): line} for every module-level bare-numeric constant
    outside the coefficients registry."""
    findings = {}
    for py in sorted(AQUA_MODEL_DIR.glob("*.py")):
        if py.stem == REGISTRY_MODULE:
            continue
        for name, lineno in _module_level_constants(py.read_text(encoding="utf-8")).items():
            findings[(py.stem, name)] = lineno
    return findings


def test_no_unlisted_module_level_numeric_constants():
    findings = _scan_package()
    assert findings, "scanner found no constants anywhere; the walk itself is broken"
    unlisted = sorted(set(findings) - set(ALLOWLIST))
    assert not unlisted, (
        "module-level numeric constants outside coefficients.py without an allowlist "
        f"reason: {unlisted}. Add a Coefficient to the registry, or an ALLOWLIST entry "
        "stating in one line why this number is not a coefficient."
    )


def test_allowlist_entries_point_at_real_constants():
    findings = _scan_package()
    stale = sorted(set(ALLOWLIST) - set(findings))
    assert not stale, (
        f"ALLOWLIST entries that no longer match any constant: {stale}. "
        "Remove them, or move the number into the registry and drop the entry."
    )


def test_scanner_flags_a_new_unlisted_constant():
    src = "NEW_UNLISTED_LIMIT = 42\nALSO_BAD = (1.0, -2.0)\nok = 5\nALIAS = 'text'\n"
    found = _module_level_constants(src)
    assert set(found) == {"NEW_UNLISTED_LIMIT", "ALSO_BAD"}, found


def test_scanner_ignores_registered_coefficients_and_derived_values():
    registered = (
        "from .coefficients import Coefficient\n"
        "FACTOR = Coefficient(name='f', value=1.3, low=1.2, high=1.4,\n"
        "                     unit='x', source='LIT')\n"
        "DERIVED = 1.0 - OTHER_FRACTION\n"
        "FLAG = True\n"
    )
    assert _module_level_constants(registered) == {}
