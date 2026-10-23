"""Edit supplied specifications, inspect a preview, then save a new design version."""

import json

import streamlit as st

from agronaut_agent.equipment_consultation import questions
from agronaut_agent.irrigation_io import render_drip_report, render_sprinkler_report

PREFIX = "field_equipment_revision_"


def render_equipment_revision(call, saved, save_revision):
    if saved["task"] not in ("drip_design", "sprinkler_design"):
        return
    if st.session_state.get(PREFIX + "parent") != saved["id"]:
        for key in list(st.session_state):
            if key.startswith(PREFIX):
                del st.session_state[key]
        st.session_state[PREFIX + "parent"] = saved["id"]
    prompts = questions(saved["task"])
    with st.expander("Revise this equipment design"):
        st.caption("Choose the specifications to revise and check their sources. Preview recalculates the whole design. "
                   "Saving keeps the original and creates a new version; it records no performed irrigation or purchase.")
        selected = st.multiselect("Sections to revise", [k for k, v in saved["inputs"].items() if isinstance(v, dict)],
                                  format_func=lambda k: k.replace("_", " ").title(), key=PREFIX + "sections")
        patch = {}
        for section in selected:
            st.markdown("**" + section.replace("_", " ").title() + "**")
            changes = {}
            source = saved["inputs"][section]["source"]
            for field, old in saved["inputs"][section].items():
                question = prompts[f"{section}.{field}"]
                key = PREFIX + section + "_" + field
                if isinstance(old, bool):
                    value = st.checkbox(question.prompt, value=old, key=key)
                elif question.choices:
                    value = st.selectbox(question.prompt, list(question.choices), index=list(question.choices).index(old), key=key)
                elif isinstance(old, (int, float)):
                    default = old * 100 if question.percent else old
                    default = int(default) if question.integer else float(default)
                    entered = st.number_input(question.prompt, value=default, step=1 if question.integer else None, key=key)
                    value = old if entered == default else entered / 100 if question.percent else entered
                else:
                    value = st.text_area(question.prompt, value=old, key=key)
                if field == "source":
                    source = value
                if value != old or type(value) is not type(old):
                    changes[field] = value
            if changes:
                patch[section] = dict(changes, source=source)
        policy = "preserve"
        if saved.get("water_target"):
            policy = st.selectbox("Linked water-budget target", ["preserve", "detach"], key=PREFIX + "policy",
                format_func=lambda p: "Preserve the saved target and evidence" if p == "preserve" else "Detach and use an independent application target")
            if policy == "detach":
                st.caption("The previous budget link remains in the original version. Supply the basis for this version's independent target.")
                source = st.text_area("Source for the independent application target", key=PREFIX + "detached_source")
                patch.setdefault("application", {})["source"] = source
        reason = st.text_area("Reason for this design revision", key=PREFIX + "reason")
        data = dict(calculation_id=saved["id"], patch=patch, reason=reason, target_policy=policy)
        signature = json.dumps(data, sort_keys=True)
        if st.session_state.get(PREFIX + "signature") != signature:
            st.session_state.pop(PREFIX + "preview", None)
            st.session_state[PREFIX + "signature"] = signature
        if st.button("Preview equipment revision", key=PREFIX + "preview_button"):
            st.session_state.pop(PREFIX + "preview", None)
            try:
                st.session_state[PREFIX + "preview"] = call("preview_equipment_revision", data)
            except (ValueError, TypeError) as exc:
                st.error(str(exc))
        preview = st.session_state.get(PREFIX + "preview")
        if preview:
            st.dataframe([dict(section=row["section"], input=row["field"].replace("_", " "),
                               before=str(row["before"]), after=str(row["after"])) for row in preview["changes"]], hide_index=True)
            comparison = preview["comparison"]
            st.caption("Supplied constraints — original: " + ("PASS" if comparison["before_feasible"] else "FAIL")
                       + "; preview: " + ("PASS" if comparison["after_feasible"] else "FAIL"))
            render = render_drip_report if saved["task"] == "drip_design" else render_sprinkler_report
            st.text(render(preview["result"]["result"]))
            st.button("Save equipment revision", key=PREFIX + "save", on_click=save_revision,
                      args=("revise_equipment", dict(data, preview_token=preview["preview_token"])))
