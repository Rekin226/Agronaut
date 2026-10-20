"""Manual mineral-product accounting intake for the selected soil unit."""

import streamlit as st


def render_nutrition_intake(active, farm, cycle):
    scope = (active["id"], (cycle["id"], cycle["crop"], cycle["planted_on"]) if cycle else None)
    if st.session_state.get("field_nutrient_scope") != scope:
        for key in list(st.session_state):
            if key.startswith("field_nutrient_"):
                del st.session_state[key]
        st.session_state.field_nutrient_scope = scope
    prefix = f"field_nutrient_{active['id']}_{cycle['id'] if cycle else 'none'}_"
    with st.expander("Account for mineral fertilizer nutrients"):
        st.caption("Compare proposed product quantities with independently sourced remaining application targets. "
                   "Use N, P₂O₅ and K₂O mass percentages from the label. This does not choose a fertilizer rate or record an application.")
        count = st.number_input("Number of mineral products", min_value=0, max_value=10, value=1, step=1, key=prefix+"count")
        with st.form(prefix+"form"):
            crop = st.text_input("Crop for nutrient accounting", value=cycle["crop"] if cycle else "",
                                 disabled=cycle is not None, key=prefix+"crop")
            stage = st.text_input("Crop stage or planning phase", key=prefix+"stage")
            area = st.number_input("Area for this nutrient budget (m²)", min_value=0.0, value=None, key=prefix+"area")
            start = st.date_input("Nutrient planning period starts", value=None, key=prefix+"start")
            end = st.date_input("Nutrient planning period ends", value=None, key=prefix+"end")
            remaining = st.checkbox("Targets are remaining fertilizer application amounts after independently assessed credits",
                                    key=prefix+"remaining")
            st.caption("Leave unknown nutrient targets blank. Enter zero only when the sourced target is zero. "
                       "Do not enter soil-test ppm or crop uptake requirements here.")
            target = {}
            for nutrient, label in (("n", "N"), ("p2o5", "P₂O₅"), ("k2o", "K₂O")):
                target[nutrient+"_kg_ha"] = st.number_input(f"Remaining {label} target (kg/ha)", value=None,
                    min_value=0.0, max_value=1e6, key=prefix+nutrient+"_target")
            target["source"] = st.text_input("Target source", key=prefix+"target_source")
            target["scope_note"] = st.text_input("Target region, method and credits already considered", key=prefix+"target_scope")
            target["basis"] = "remaining_application" if remaining else None
            products = []
            for index in range(count):
                p = prefix+f"product_{index}_"
                st.markdown(f"**Mineral product {index+1}**")
                name = st.text_input("Product name", key=p+"name")
                quantity = st.number_input("Quantity for the whole area and period", value=None, min_value=0.0, max_value=1e9, key=p+"quantity")
                unit = st.selectbox("Product quantity unit", ["kg", "L"], index=None, key=p+"unit")
                density = st.number_input("Density (kg/L), only for quantities in L", value=None, min_value=0.0, max_value=1000.0, key=p+"density")
                density_source = st.text_input("Density source, if supplied", key=p+"density_source")
                analysis = dict(basis="mass_percent")
                for nutrient, label in (("n", "N"), ("p2o5", "P₂O₅"), ("k2o", "K₂O")):
                    analysis[nutrient+"_pct"] = st.number_input(f"Label {label} (% by mass)", value=None, min_value=0.0, max_value=100.0,
                                                              key=p+nutrient)
                analysis["source"] = st.text_input("Product analysis source", key=p+"source")
                products.append(dict(id=f"product-{index+1}", name=name, kind="mineral", quantity=quantity, unit=unit,
                                     density_kg_l=density, density_source=density_source or None, analysis=analysis))
            if st.form_submit_button("Calculate and save nutrient budget"):
                return dict(task="nutrient_budget", inputs=dict(schema_version=1, site=dict(name=farm["name"], timezone=farm["timezone"]),
                    area_m2=area, crop=dict(name=crop, stage=stage),
                    period=dict(start=start.isoformat() if start else None, end=end.isoformat() if end else None),
                    target=target, products=products))
    return None
