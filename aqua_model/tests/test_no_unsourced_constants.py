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

The scanner covers seven places a module-level number can live: bare assignments,
numeric values in string-keyed dicts AND in name-keyed dicts, non-zero defaults on
`@dataclass` fields (0/0.0 state initialisers are exempt, including the
`field(default=...)` form), bare-numeric keyword arguments AND bare-numeric
positional arguments in module-level calls. A `Coefficient(...)` call never trips
it: the call itself carries value, range and source, wherever in the package it is
defined.

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
    ("cropgrowth", "MAX_OVER_CITED"):
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
    ("scenario", "THRESHOLDS_MG_L[tan_mg_l]"):
        "alert threshold backed by the nitrogen-cycle knowledge base (THRESHOLD_SOURCE); the value "
        "lives in a dict so scenarios can key on the channel name",
    ("scenario", "THRESHOLDS_MG_L[no2_mg_l]"):
        "alert threshold backed by the nitrogen-cycle knowledge base (THRESHOLD_SOURCE); the value "
        "lives in a dict so scenarios can key on the channel name",
    ("scenario", "THRESHOLDS_MG_L[no3_mg_l]"):
        "alert threshold backed by the nitrogen-cycle knowledge base (THRESHOLD_SOURCE); the value "
        "lives in a dict so scenarios can key on the channel name",
    # mirror.py — operator-nudge trust weights, a policy the comments above them reason through
    ("mirror", "NUDGE_WEIGHTS[water_temp_c]"):
        "how far an operator reading overrides the model; data-trust policy reasoned in the comment above",
    ("mirror", "NUDGE_WEIGHTS[tan_mg_l]"):
        "how far an operator reading overrides the model; data-trust policy reasoned in the comment above",
    ("mirror", "NUDGE_WEIGHTS[no2_mg_l]"):
        "how far an operator reading overrides the model; data-trust policy reasoned in the comment above",
    ("mirror", "NUDGE_WEIGHTS[no3_mg_l]"):
        "how far an operator reading overrides the model; data-trust policy reasoned in the comment above",
    ("mirror", "NUDGE_WEIGHTS[fish_avg_weight_g]"):
        "how far an operator reading overrides the model; data-trust policy reasoned in the comment above",
    ("mirror", "NUDGE_WEIGHTS[fish_count]"):
        "a count is a count; mortality is not negotiable (comment above the dict)",
    # validate.py — the trust boundary's own hard sanity bounds: refuse-outside ranges, not model inputs
    ("validate", "_BOUNDS[grow_area_m2]"):
        "hard refuse-outside sanity bound at the validation gate; order-of-magnitude guard, not a coefficient",
    ("validate", "_BOUNDS[temperature_c]"):
        "hard refuse-outside sanity bound at the validation gate; order-of-magnitude guard, not a coefficient",
    ("validate", "_BOUNDS[water_budget_lpd]"):
        "hard refuse-outside sanity bound at the validation gate; order-of-magnitude guard, not a coefficient",
    # climate.py — the single-poly-tunnel envelope defaults moved to the registry
    # (GREENHOUSE_* in coefficients.py, sourced/derived per #213); sourced is the
    # default state, so no entries remain here. Add one back ONLY with a source
    # or a stated derivation.
    # advisory.py — evidence-class confidence ceilings. A confidence ceiling will
    # never get a measured source: the ordering (measured > direction > level) is
    # the claim, and it is policy, like NUDGE_WEIGHTS — reasoned in the comment
    # above the dict, not physics to be cited.
    ("advisory", "EVIDENCE_CONFIDENCE[MEASURED]"):
        "confidence ceiling encoding the MIXED twin-validation verdict (comment above the dict); the ordering is the claim, policy not physics",
    ("advisory", "EVIDENCE_CONFIDENCE[MODELLED_DIRECTION]"):
        "confidence ceiling encoding the MIXED twin-validation verdict (comment above the dict); the ordering is the claim, policy not physics",
    ("advisory", "EVIDENCE_CONFIDENCE[MODELLED_LEVEL]"):
        "confidence ceiling encoding the MIXED twin-validation verdict (comment above the dict); the ordering is the claim, policy not physics",
    # system_types.py — per-system geometry defaults; each instance passes explicit, source-cited values
    ("system_types", "SystemType.footprint_ratio"):
        "class-level default; every SystemType instance sets its own value with a source",
    ("system_types", "SystemType.lift_height_m"):
        "class-level default; every SystemType instance sets its own lift with a source",
    ("system_types", "SystemType.lift_low"):
        "class-level default; every SystemType instance sets its own lift with a source",
    ("system_types", "SystemType.lift_high"):
        "class-level default; every SystemType instance sets its own lift with a source",
    # layout.py — drafting defaults for the 3D proposal, which states positions are not a site plan
    ("layout", "Placed.plant_spacing_m"):
        "drafting default for the 3D proposal; a real plan overrides it, nothing computes physics from it",
    ("layout", "PipeRun.diameter_m"):
        "drafting default for the 3D proposal; layout re-pipes runs at the computed diameter",
    ("layout", "PipeRun.flow_lpm"):
        "drafting default for the 3D proposal; layout writes the routed flow before hydraulics runs",
    # flowsheet.py / pilot.py — presentation and grant-template defaults
    ("flowsheet", "Component.count"):
        "default of one unit per flowsheet box; no construction site passes a count today, "
        "so the default IS the value in every shipped flowsheet",
    ("pilot", "PilotInfo.duration_months"):
        "grant-template default duration; presentation default, nothing computes from it",
    # twin.py — TwinState volume default; the flowsheet passes the real tank volume at run time
    ("twin", "TwinState.volume_l"):
        "standalone-twin convenience default (1 m3); the design path always passes the real volume",
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
    # twin.py — transient-twin kinetics. These module-level seeds duplicate the
    # TwinParams values the scenario spread varies; they carry the same KNOWN DEBT
    # #211 label so both copies of each number point at the same tracking issue.
    ("twin", "_AOB_DOUBLING_DAYS"):
        "KNOWN DEBT #211: nitrifier population kinetics for the transient twin (cycling/ammonia-peak timing); not a sizing input",
    ("twin", "_NOB_DOUBLING_DAYS"):
        "KNOWN DEBT #211: nitrifier population kinetics for the transient twin (nitrite-peak timing); not a sizing input",
    ("twin", "_SEED_CAPACITY_G_DAY"):
        "trace nitrifier population at cycling start; transient-twin initialisation",
    ("twin", "_N_REMOVAL_PER_DAY"):
        "KNOWN DEBT #211: first-order nitrate removal rate; ratio fixed by the steady-state split, magnitude sets equilibration speed",
    # twin.py — TwinParams defaults and the scenario spread. The docstring already states these
    # are "literature-typical rather than fitted"; they are real model numbers with no source, so
    # they are listed as known debt with the issue that tracks their migration (#211), not as
    # conventions.
    ("twin", "TwinParams.aob_doubling_days"):
        "KNOWN DEBT #211: transient-twin kinetics, literature-typical, no citation yet",
    ("twin", "TwinParams.nob_doubling_days"):
        "KNOWN DEBT #211: transient-twin kinetics, literature-typical, no citation yet",
    ("twin", "TwinParams.n_removal_per_day"):
        "KNOWN DEBT #211: first-order nitrate removal; ratio fixed by the steady-state split, magnitude not sourced",
    ("twin", "PARAMS_FAST(aob_doubling_days=...)"):
        "KNOWN DEBT #211: fast end of the AOB doubling-time spread; not sourced yet",
    ("twin", "PARAMS_FAST(nob_doubling_days=...)"):
        "KNOWN DEBT #211: fast end of the NOB doubling-time spread; not sourced yet",
    ("twin", "PARAMS_FAST(n_removal_per_day=...)"):
        "KNOWN DEBT #211: fast end of the removal-rate spread; ratio fixed by the steady-state split",
    ("twin", "PARAMS_SLOW(aob_doubling_days=...)"):
        "KNOWN DEBT #211: slow end of the AOB doubling-time spread; not sourced yet",
    ("twin", "PARAMS_SLOW(nob_doubling_days=...)"):
        "KNOWN DEBT #211: slow end of the NOB doubling-time spread; not sourced yet",
    ("twin", "PARAMS_SLOW(n_removal_per_day=...)"):
        "KNOWN DEBT #211: slow end of the removal-rate spread; ratio fixed by the steady-state split",
    # types.py — output-plumbing defaults; the sizing engine always writes explicit values
    ("types", "HydroponicOutput.footprint_ratio"):
        "default for the output container; every design path writes the engine-computed value",
    ("types", "DesignOutput.footprint_ratio"):
        "default for the output container; every design path writes the engine-computed value",
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
    """{(display_name, line_number)} for every module-level bare-numeric constant.

    `display_name` is `"NAME"` for plain assignments, `"Name.field"` for a
    non-zero `@dataclass` field default, `"NAME[key]"` for a numeric value in a
    module-level dict (string- OR name-keyed), `"NAME(kw=...)"` for bare-numeric
    keyword arguments and `"NAME(#i)"` for bare-numeric positional arguments in
    a module-level call. The line number is the line of
    the specific literal, so an allowlist lookup points at the number itself.
    """
    found = set()
    tree = ast.parse(source)
    dataclass_names = set()
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and any(
            _is_dataclass_decorator(d) for d in node.decorator_list
        ):
            dataclass_names.add(node.name)

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            found |= _dataclass_defaults(node, dataclass_names)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets, value = _assignment_parts(node)
            if value is None:
                continue
            # The registry shape: Coefficient(...) carries value, range and source in the call.
            if isinstance(value, ast.Call) and _call_name(value.func) == "Coefficient":
                continue
            found |= _bare_value_names(value, targets, node.lineno)
            found |= _dict_numeric_values(targets, value)
            if isinstance(value, ast.Call):
                for target in targets:
                    prefix = target.id if isinstance(target, ast.Name) else None
                    found |= _call_numeric_values(value, prefix=prefix, lineno=node.lineno)
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            found |= _call_numeric_values(node.value, prefix=None, lineno=node.lineno)
    return found


def _is_dataclass_decorator(node):
    """True for @dataclass or @dataclass(...) — the qualified form too."""
    target = node.func if isinstance(node, ast.Call) else node
    if isinstance(target, ast.Name):
        return target.id == "dataclass"
    if isinstance(target, ast.Attribute):
        return target.attr == "dataclass"
    return False


def _assignment_parts(node):
    """(targets, value) for an Assign or a valued AnnAssign, else ([], None)."""
    if isinstance(node, ast.Assign):
        return node.targets, node.value
    if isinstance(node, ast.AnnAssign) and node.value is not None:
        return [node.target], node.value
    return [], None


def _call_name(func):
    """Dotted name of a call target ('Coefficient', 'dataclasses.replace'), else None."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        base = _call_name(func.value)
        return f"{base}.{func.attr}" if base else None
    return None


def _bare_value_names(value, targets, lineno):
    """{(name, lineno)} for each target the value is a bare numeric for."""
    if _bare_numbers(value) is None:
        return set()
    found = set()
    for target in targets:
        if isinstance(target, ast.Name) and target.id == target.id.upper():
            found.add((target.id, lineno))
    return found


def _dict_numeric_values(targets, value):
    """{(NAME[key], line)} for numeric literal values in a module-level dict.

    Both string-keyed and name-keyed dicts are scanned: a name key
    (EVIDENCE_CONFIDENCE[MEASURED]) is just a string key spelled through a
    module constant, so it hides from a literal-only scan. An enum-style
    numeric-keyed map is a lookup table (the numbers are the vocabulary, not
    quantities), and naming a hit NAME[0] in the allowlist would be noise
    either way.
    """
    if not isinstance(value, ast.Dict):
        return set()
    found = set()
    for key_node, val_node in zip(value.keys, value.values):
        if isinstance(key_node, ast.Constant) and isinstance(key_node.value, str):
            key = key_node.value
        elif isinstance(key_node, ast.Name):
            key = key_node.id
        else:
            continue
        nums = _bare_numbers(val_node)
        if nums is None:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                found.add((f"{target.id}[{key}]", val_node.lineno))
    return found


def _dataclass_defaults(class_node, dataclass_names):
    """{(Name.field, line)} for non-zero bare-numeric defaults on @dataclass fields.

    Zero-initialised state (TwinState.tan_mg_l = 0.0) is exempt: 0 is "no
    reading yet", not a quantity. The `field(default=...)` form is scanned
    through to the inner default so a number cannot hide inside the call. A
    field defaulting to a name (the shared _SEED_CAPACITY_G_DAY pattern) is a
    reference, not a literal, so it stays out of scope; the name itself is
    already scanned where it is defined. A `field(default_factory=...)` is a
    constructor reference, likewise out of scope.
    """
    if class_node.name not in dataclass_names:
        return set()
    found = set()
    for stmt in class_node.body:
        if not (isinstance(stmt, ast.AnnAssign) and stmt.value is not None):
            continue
        value = stmt.value
        if isinstance(value, ast.Call) and _call_name(value.func) == "field":
            kwargs = [kw for kw in value.keywords if kw.arg == "default"]
            if not kwargs:
                continue
            value = kwargs[0].value
        nums = _bare_numbers(value)
        if nums is None or (len(nums) == 1 and nums[0] == 0):
            continue
        if isinstance(stmt.target, ast.Name):
            found.add((f"{class_node.name}.{stmt.target.id}", value.lineno))
    return found


def _call_numeric_values(call, prefix, lineno):
    """{(NAME(...), line)} for bare-numeric arguments in a module-level call.

    Covers both keyword (`NAME(kw=...)`) and positional (`NAME(#i)`) arguments;
    a scanner that reads only `call.keywords` lets `TwinParams(0.7, 1.0, 0.15)`
    through silently. `prefix` is the assignment's display prefix
    ('PARAMS_FAST'); the call's own name is used when there is none. Two calls
    are skipped, keyed on the CALL's class rather than the display name: a
    Coefficient(...) (its arguments ARE the value/range/source triple) and a
    sourced entity row — Crop(...), FishSpecies(...), SystemType(...) — because
    a call that carries a non-empty `source=` string is the entity-table form
    of rule 2, and flagging every row of the crop and species tables would
    turn the guard into an allowlist mill. Keying the exemption on the class
    name plus a non-empty string keeps an unsourced call on some other class
    from borrowing it by adding `source=''`.
    """
    cls = _call_name(call.func)
    if cls == "Coefficient":
        return set()
    if cls in _SOURCED_ENTITY_CLASSES and _carries_sourced_string(call):
        return set()
    name = prefix if prefix is not None else cls
    found = set()
    for i, arg in enumerate(call.args):
        nums = _bare_numbers(arg)
        if nums is None:
            continue
        found.add((f"{name}(#{i})", arg.lineno))
    for kw in call.keywords:
        nums = _bare_numbers(kw.value)
        if nums is None:
            continue
        label = f"{name}({kw.arg}=...)" if kw.arg else f"{name}(**{{...}})"
        found.add((label, kw.value.lineno))
    return found


_SOURCED_ENTITY_CLASSES = {"Crop", "FishSpecies", "SystemType", "Component"}


def _carries_sourced_string(call):
    """True when the call passes a non-empty string (or name) as `source=`.

    The entity tables pass their citation as a literal; a constant holding it
    (THRESHOLD_SOURCE) is the same fact by reference. An empty string — a
    placeholder someone added to slip past the old shape-only check — does not
    count, and neither does any other type.
    """
    for kw in call.keywords:
        if kw.arg != "source":
            continue
        v = kw.value
        if isinstance(v, ast.Constant) and isinstance(v.value, str) and v.value.strip():
            return True
        if isinstance(v, ast.Name):
            return True
    return False


def _scan_package():
    """{(stem, display_name): line} for every module-level bare-numeric constant
    outside the coefficients registry."""
    findings = {}
    for py in sorted(AQUA_MODEL_DIR.glob("*.py")):
        if py.stem == REGISTRY_MODULE:
            continue
        for name, lineno in _module_level_constants(py.read_text(encoding="utf-8")):
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
    assert {name for name, _line in found} == {"NEW_UNLISTED_LIMIT", "ALSO_BAD"}, found


def test_scanner_ignores_registered_coefficients_and_derived_values():
    registered = (
        "from .coefficients import Coefficient\n"
        "FACTOR = Coefficient(name='f', value=1.3, low=1.2, high=1.4,\n"
        "                     unit='x', source='LIT')\n"
        "DERIVED = 1.0 - OTHER_FRACTION\n"
        "FLAG = True\n"
    )
    assert _module_level_constants(registered) == set()


def test_scanner_flags_numeric_dict_values():
    src = (
        "BANDS = {'low': 1.0, 'high': 9.0}\n"
        "OK_MIXED = {'label': 'text', 'ratio': 0.5}\n"
        "COMPUTED = {'x': 1.0 + 2.0}\n"
        "LOOKUP = {0: 1.0, 1: 2.0}\n"
        "MEASURED = 'measured'\n"
        "CONF = {MEASURED: 0.9, 'open': 0.6}\n"
    )
    found = {name for name, _line in _module_level_constants(src)}
    assert found == {
        "BANDS[low]", "BANDS[high]", "OK_MIXED[ratio]",
        "CONF[MEASURED]", "CONF[open]",
    }, found


def test_scanner_flags_nonzero_dataclass_defaults():
    src = (
        "from dataclasses import dataclass, field\n"
        "@dataclass\n"
        "class Params:\n"
        "    rate: float = 1.5\n"
        "    state: float = 0.0\n"
        "    shared: float = _SEED\n"
        "    wired: float = field(default=0.7)\n"
        "    zeroed: float = field(default=0.0)\n"
        "    made: 'Params' = field(default_factory=lambda: Params(1.0))\n"
        "\n"
        "class Plain:\n"
        "    not_scanned: float = 3.0\n"
    )
    found = {name for name, _line in _module_level_constants(src)}
    assert found == {"Params.rate", "Params.wired"}, found


def test_scanner_flags_numeric_args_in_module_level_calls():
    src = (
        "PARAMS_FAST = TwinParams(aob_doubling_days=0.7, label='fast')\n"
        "PARAMS_POS = TwinParams(0.7, 1.0, 0.15)\n"
        "NAMED = TwinParams()\n"
        "REG = Coefficient(name='r', value=0.4, low=0.2, high=0.6, unit='x', source='LIT')\n"
        "SOURCED = Crop(name='lettuce', frr=60.0, source='FAO589/UVI')\n"
        "BARE_SOURCE = Crop(name='lettuce', frr=60.0, source='')\n"
        "NOT_ENTITY = Band(top=9.0, source='LIT')\n"
        "get_system('raft')\n"
    )
    found = {name for name, _line in _module_level_constants(src)}
    assert found == {
        "PARAMS_FAST(aob_doubling_days=...)",
        "PARAMS_POS(#0)", "PARAMS_POS(#1)", "PARAMS_POS(#2)",
        "BARE_SOURCE(frr=...)",
        "NOT_ENTITY(top=...)",
    }, found
