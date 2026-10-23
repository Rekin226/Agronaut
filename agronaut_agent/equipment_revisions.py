"""Explicit, previewed equipment revisions with immutable parent and target evidence."""

import hashlib
import json

from agro_model.validation import choice, text

from .domains import run_capability
from .equipment_targets import source_issues
from .field_io import _mapping
from .field_workflow import _calculate, _json, _read, _retry, _scope


class EquipmentRevisions:
    def __init__(self, db):
        self.db = db

    def _preview(self, conn, owner, unit_id, calculation_id, patch, reason, target_policy):
        timezone, _ = _scope(conn, owner, unit_id)
        text("reason", reason)
        choice("target_policy", target_policy, ("preserve", "detach"))
        parent = _read(conn, "field_calculations", unit_id, calculation_id)
        choice("parent task", parent["task"], ("drip_design", "sprinkler_design"))
        inputs = json.loads(_json(parent["inputs"]))
        sections = [name for name, value in inputs.items() if isinstance(value, dict)]
        patch = json.loads(_json(_mapping(patch, sections, "equipment patch")))
        changes = []
        for section, values in patch.items():
            values = _mapping(values, tuple(inputs[section]), f"{section} patch")
            changed = [key for key, value in values.items()
                       if type(value) is not type(inputs[section][key]) or value != inputs[section][key]]
            if changed:
                text(f"{section} source", values.get("source"))
            for key in changed:
                changes.append(dict(section=section, field=key, before=inputs[section][key], after=values[key]))
            inputs[section].update(values)
        target = parent.get("water_target")
        if target_policy == "detach":
            if target is None:
                raise ValueError("there is no linked water target to detach")
            text("application source for detached target", patch.get("application", {}).get("source"))
        elif not changes:
            raise ValueError("change at least one equipment input")
        result = run_capability("soil", parent["task"], inputs)
        if target and target_policy == "preserve":
            issues = source_issues(conn, owner, unit_id, target["calculation_snapshot"], parent["cycle_id"],
                                   result["result"]["area_m2"], target["timezone"])
            if timezone != target["timezone"]:
                issues.append("farm timezone differs from the linked water target")
            expected = ({"gross_depth_mm": target["gross_depth_mm"]} if parent["task"] == "drip_design" else
                        {"net_depth_mm": target["net_depth_mm"], "efficiency": target["efficiency"]})
            if any(inputs["application"][key] != value for key, value in expected.items()):
                issues.append("preserving the water target requires its original application amount and efficiency")
            if issues:
                raise ValueError("; ".join(sorted(set(issues))))
        payload = dict(calculation_id=calculation_id, patch=patch, reason=reason, target_policy=target_policy)
        token = hashlib.sha256(_json(["equipment-revision-v1", owner, unit_id, parent, payload, result]).encode()).hexdigest()
        return parent, dict(status="preview", **payload, preview_token=token, task=parent["task"],
            changes=changes, inputs=inputs, result=result,
            water_target=target if target_policy == "preserve" else None,
            comparison=dict(before_feasible=parent["result"]["feasible"], after_feasible=result["feasible"],
                before_constraints=parent["result"]["binding_constraints"], after_constraints=result["binding_constraints"]),
            note="Equipment scenario only. Saving creates a child calculation; no irrigation, purchase or resource booking is recorded.")

    def preview(self, owner, unit_id, calculation_id, patch, reason, target_policy):
        with self.db.transaction() as conn:
            return self._preview(conn, owner, unit_id, calculation_id, patch, reason, target_policy)[1]

    def save(self, owner, unit_id, calculation_id, patch, reason, target_policy, preview_token, *, request_id):
        text("preview_token", preview_token)
        patch = json.loads(_json(patch))
        operation = dict(kind="revise_equipment", calculation_id=calculation_id, patch=patch,
                         reason=reason, target_policy=target_policy, preview_token=preview_token)
        with self.db.transaction() as conn:
            _scope(conn, owner, unit_id)
            previous = _retry(conn, "field_calculations", unit_id, request_id, operation)
            if previous:
                return previous
            parent, preview = self._preview(conn, owner, unit_id, calculation_id, patch, reason, target_policy)
            if preview_token != preview["preview_token"]:
                raise ValueError("revision differs from its preview; preview the current changes again")
            saved = _calculate(conn, owner, unit_id, parent["cycle_id"], request_id, parent["task"],
                               preview["inputs"], operation, parent_id=calculation_id)
            if preview["water_target"]:
                saved["water_target"] = preview["water_target"]
            saved["equipment_revision"] = dict(reason=reason, changes=preview["changes"], target_policy=target_policy,
                previous_water_target_id=(parent.get("water_target") or {}).get("calculation_id"),
                comparison=preview["comparison"])
            conn.execute("UPDATE field_calculations SET payload=? WHERE id=?", (_json(saved), saved["id"]))
            return saved
