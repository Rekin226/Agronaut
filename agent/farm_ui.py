"""Farm and production-unit setup, without a model in the path."""

import hashlib
import json

import streamlit as st

_SYSTEMS = {"soil": "Soil cultivation", "hydroponics": "Hydroponics", "aquaponics": "Aquaponics"}
_WATER = {"rainfed": "Rainfed", "supplemental": "Rainfall with supplemental irrigation",
          "fully_irrigated": "Fully irrigated"}
_ENVIRONMENTS = {"open_field": "Open field", "greenhouse": "Greenhouse", "indoor": "Indoor"}


def _changed_zone_parent():
    for key in list(st.session_state):
        if key.startswith("farm_zone_") and key != "farm_zone_parent":
            del st.session_state[key]


def _changed_unit():
    # Display state is scoped too: never carry a pond's form values or transcript into
    # the next field. Durable records remain in the selected unit's database context.
    st.session_state.messages = []
    if "farm_unit_choice" in st.session_state:
        del st.session_state["farm_unit_choice"]
    for key in list(st.session_state):
        if key.startswith(("twin_", "log_", "field_", "crop_")):
            del st.session_state[key]
    if hasattr(st, "rerun"):
        st.rerun()
    else:
        st.experimental_rerun()


def render_unit_controls(brain, user: str, channel: str = "web"):
    units = brain.list_units(channel, user)
    farms = brain.list_farms(channel, user)
    st.sidebar.header("Farm and production units")
    if units:
        by_id = {u["id"]: u for u in units}
        choices = list(by_id)
        active = next((u["id"] for u in units if u["active"]), None)
        choice = st.sidebar.selectbox(
            "Production unit", choices, index=choices.index(active) if active else 0,
            format_func=lambda key: f"{by_id[key]['name']} · " + (f"zone of {by_id[key]['parent_name']}" if by_id[key].get("parent_unit_id") else by_id[key]['production_system'] or 'unresolved'),
            key="farm_unit_choice")
        if not active:
            st.sidebar.caption("Choose which unit to discuss before starting a consultation.")
        else:
            st.sidebar.caption(f"Active: {by_id[active]['name']}")
            if by_id[active].get("parent_unit_id"):
                st.sidebar.caption(f"Irrigation zone of {by_id[active]['parent_name']}. Crop cycles and records belong to this zone.")
                with st.sidebar.expander("Selected zone definition"):
                    details = brain.field_workflow(channel, user, "list_zones", {}, expected_unit_id=active)
                    st.write(details["zone"]["data"] if details["zone"] else "No zone definition is available.")
                    st.caption(details["note"])
        domain = None
        if by_id[choice]["production_system"] is None:
            domain = st.sidebar.selectbox("How is this unit grown?", list(_SYSTEMS),
                                           format_func=_SYSTEMS.get, key="farm_resolve_domain")
        if st.sidebar.button("Use this unit", key="farm_use_unit"):
            brain.select_unit(channel, user, choice, domain)
            _changed_unit()
    else:
        st.sidebar.caption("Add a field, greenhouse or aquatic system to keep its records separate.")

    parents = {u["id"]: u for u in units if u["production_system"] == "soil" and not u.get("parent_unit_id") and u.get("environment")}
    if parents:
        with st.sidebar.expander("Add an irrigation zone"):
            st.caption("Give each zone its own records within a field. Only farm and growing environment are shared; crop cycles, soil inputs and measurements start separately.")
            parent_key = "farm_zone_parent"
            if st.session_state.get(parent_key) not in (None, *parents):
                st.session_state[parent_key] = None
            parent = st.selectbox("Parent field", [None, *parents], key=parent_key,
                on_change=_changed_zone_parent,
                format_func=lambda key: "Select a parent field" if key is None else parents[key]["name"])
            if parent:
                st.caption(f"Farm: {parents[parent]['farm_id']} · Environment: {parents[parent]['environment']}")
                with st.form("farm_create_zone"):
                    key = st.text_input("Stable zone key", key="farm_zone_key", help="Unique within this field; for example north-drip.")
                    name = st.text_input("Zone name", key="farm_zone_name")
                    strategy = st.selectbox("Zone water strategy", ["supplemental", "fully_irrigated"], format_func=_WATER.get, key="farm_zone_strategy")
                    method = st.selectbox("Zone irrigation method", ["drip", "sprinkler", "surface"], key="farm_zone_method")
                    area = st.text_input("Zone ground area (m²); blank if unknown", key="farm_zone_area")
                    boundary = st.text_area("Zone location and boundaries", key="farm_zone_boundary")
                    source = st.text_input("Zone definition source", key="farm_zone_source")
                    st.caption("Boundaries and area are declared, not checked for overlap. Create a new identity if the definition changes; original zone records stay separate.")
                    if st.form_submit_button("Create and use zone"):
                        data = dict(zone_key=key, name=name, water_strategy=strategy, irrigation_method=method,
                                    ground_area_m2=area.strip() or None, boundary_note=boundary, source=source)
                        request_id = "web-zone-"+hashlib.sha256(json.dumps([parent, data], sort_keys=True).encode()).hexdigest()
                        try:
                            brain.create_zone(channel, user, parent, data, request_id)
                        except ValueError as exc:
                            st.error(str(exc))
                        else:
                            _changed_unit()

    with st.sidebar.expander("Add a production unit"):
        system = st.selectbox("Production system", list(_SYSTEMS), format_func=_SYSTEMS.get,
                              key="farm_new_system")
        farm_names = {f["id"]: f["name"] for f in farms}
        farm_choice = st.selectbox("Farm", ["new"] + list(farm_names),
                                   format_func=lambda key: "New farm" if key == "new" else farm_names[key],
                                   key="farm_new_parent")
        strategy = st.selectbox("Water strategy", list(_WATER), format_func=_WATER.get,
                                 key="farm_new_water") if system == "soil" else "fully_irrigated"
        with st.form("farm_create_unit"):
            name = st.text_input("Unit name", placeholder="Tomato field", key="farm_new_name")
            if farm_choice == "new":
                farm_name = st.text_input("Farm name", key="farm_new_farm_name")
                timezone = st.text_input("Farm timezone", placeholder="Africa/Accra",
                                          help="Use a timezone name so observations stay on the correct local day.",
                                          key="farm_new_timezone")
                farm = {"name": farm_name, "timezone": timezone}
            else:
                farm = {"id": farm_choice}
            environment = st.selectbox("Growing environment", list(_ENVIRONMENTS),
                                        index=0 if system == "soil" else 1,
                                        format_func=_ENVIRONMENTS.get, key="farm_new_environment")
            medium = "soil" if system == "soil" else st.selectbox("Growing medium", ["water", "substrate"],
                                                                   key="farm_new_medium")
            method = None
            if system == "soil" and strategy != "rainfed":
                method = st.selectbox("Irrigation method", ["drip", "sprinkler", "surface"],
                                       key="farm_new_irrigation")
            if st.form_submit_button("Create and use unit"):
                try:
                    brain.create_unit(channel, user, {"farm": farm, "unit": {
                        "name": name, "production_system": system, "medium": medium,
                        "environment": environment, "water_strategy": strategy,
                        "irrigation_method": method}})
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    _changed_unit()
