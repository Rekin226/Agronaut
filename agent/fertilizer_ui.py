"""Completed mineral fertilizer applications in the model-free crop journal."""

from datetime import date

import streamlit as st


def _save(call, cycle_id, prior_id, prefix, product_ids):
    from .crop_ui import _request_id

    data = {key: st.session_state.get(prefix+key) for key in
            ("entry", "day", "phase", "area_m2", "location", "placement", "source", "provenance", "calculation_id")}
    data["day"] = data["day"].isoformat() if data["day"] else None
    data["calculation_id"] = data["calculation_id"] or None
    data["products"] = []
    for index, product_id in enumerate(product_ids):
        p = prefix+f"product_{index}_"
        product = dict(id=product_id, kind="mineral", **{key: st.session_state.get(p+key) for key in
                       ("name", "quantity", "unit", "density_kg_l", "density_source")})
        product["density_source"] = product["density_source"] or None
        product["analysis"] = dict(basis="mass_percent", source=st.session_state.get(p+"source"),
                                   **{n+"_pct": st.session_state.get(p+n) for n in ("n", "p2o5", "k2o")})
        data["products"].append(product)
    request = dict(cycle_id=cycle_id, data=data, supersedes_id=prior_id)
    try:
        call("record_fertilizer", dict(request, request_id=_request_id("record_fertilizer", request)))
        st.session_state.crop_notice = "Fertilizer application saved; correction history preserved."
    except (ValueError, TypeError) as exc:
        st.session_state.crop_error = str(exc)


def _void(call, selector):
    from .crop_ui import _request_id

    request = dict(record_id=st.session_state.get(selector), reason=st.session_state.get(selector+"_reason"))
    try:
        call("void_fertilizer", dict(request, request_id=_request_id("void_fertilizer", request)))
        st.session_state.crop_notice = "Fertilizer application voided; history preserved."
    except (ValueError, TypeError) as exc:
        st.session_state.crop_error = str(exc)


def render_fertilizer_totals(summary):
    st.caption(summary["note"])
    if not summary["groups"]:
        st.info("No current fertilizer applications recorded.")
        return
    rows = []
    for group in summary["groups"]:
        for nutrient, amounts in group["nutrients"].items():
            rows.append(dict(phase=group["phase"], provenance=group["provenance"], nutrient=nutrient.upper(),
                applications=group["applications"], known_label_kg=amounts["known_product_kg"],
                total_label_kg=amounts["declared_total_kg"] if amounts["declared_total_kg"] is not None else "unknown"))
    st.dataframe(rows)


def render_fertilizer_records(call, journal, cycle_id, last_day):
    from .crop_ui import _journal_date

    rows = [r for r in journal["records"] if r["kind"] == "fertilizer"]
    current = {r["id"]: r for r in rows if not r["superseded"]}
    selector = f"crop_fertilizer_correction_{cycle_id}"
    if st.session_state.get(selector) not in (None, *current):
        st.session_state[selector] = None
    with st.expander("Record or correct a fertilizer application"):
        st.caption("Enter mineral products actually applied, including preplant applications. "
                   "A nutrient plan does not create an application record. Keep unknown analyses and densities blank.")
        prior_id = st.selectbox("Fertilizer application entry", [None, *current], key=selector,
            format_func=lambda key: "New application" if key is None else
            f"Correct {current[key]['data']['entry']}" + (" (voided)" if current[key]["voided"] else ""))
        old = current[prior_id]["data"] if prior_id else {}
        prefix = f"crop_fertilizer_{cycle_id}_{prior_id or 'new'}_"
        count = st.number_input("Number of applied mineral products", min_value=1, max_value=100,
                               value=len(old.get("products", [])) or 1, step=1, key=prefix+"count")
        product_ids = []
        reserved_ids = {p["id"].strip() for p in old.get("products", [])}
        with st.form(prefix+"form"):
            st.text_input("Application entry ID", value=old.get("entry", ""), disabled=prior_id is not None, key=prefix+"entry")
            # Streamlit serializes years below 1000 without the padding its parser requires.
            _journal_date("Application date", old.get("day"), date(1000, 1, 1), last_day, prefix+"day")
            for key, label, choices in (("phase", "Application phase", ["preplant", "in_crop"]),
                                       ("provenance", "Application quantity provenance", ["measured", "reported", "estimated"])):
                st.selectbox(label, choices, index=choices.index(old[key]) if old else None, key=prefix+key)
            st.number_input("Treated area (m²)", min_value=0.0, max_value=1e10,
                            value=float(old["area_m2"]) if old else None, key=prefix+"area_m2")
            st.text_input("Treated location or extent", value=old.get("location", ""), key=prefix+"location")
            st.text_input("Actual placement or application method", value=old.get("placement", ""), key=prefix+"placement")
            st.text_input("Application record source", key=prefix+"source")
            st.text_input("Related saved nutrient plan ID (optional)", value=old.get("calculation_id") or "", key=prefix+"calculation_id")
            st.caption("Product quantities cover this application and treated area. Enter N, P₂O₅ and K₂O label mass percentages. "
                       "Enter zero only when the label states zero. Organic amendments are not supported here.")
            for index in range(count):
                product = old.get("products", [])[index] if index < len(old.get("products", [])) else {}
                product_id = product.get("id", f"product-{index+1}")
                if not product:
                    while product_id.strip() in reserved_ids:
                        product_id += "-new"
                reserved_ids.add(product_id.strip())
                product_ids.append(product_id)
                p = prefix+f"product_{index}_"
                st.markdown(f"**Applied mineral product {index+1}**")
                st.text_input("Applied product name", value=product.get("name", ""), key=p+"name")
                for key, label, high in (("quantity", "Quantity actually applied", 1e9),
                                         ("density_kg_l", "Liquid density (kg/L), only for quantities in L", 1000.0)):
                    st.number_input(label, value=float(product[key]) if product.get(key) is not None else None,
                                    min_value=0.0, max_value=high, key=p+key)
                st.selectbox("Applied quantity unit", ["kg", "L"],
                             index=["kg", "L"].index(product["unit"]) if product else None, key=p+"unit")
                st.text_input("Applied product density source", value=product.get("density_source") or "", key=p+"density_source")
                analysis = product.get("analysis", {})
                for n, label in (("n", "N"), ("p2o5", "P₂O₅"), ("k2o", "K₂O")):
                    st.number_input(f"Applied label {label} (% by mass)", min_value=0.0, max_value=100.0,
                        value=float(analysis[n+"_pct"]) if analysis.get(n+"_pct") is not None else None, key=p+n)
                st.text_input("Applied product analysis source", value=analysis.get("source", ""), key=p+"source")
            st.form_submit_button("Save fertilizer application", on_click=_save,
                                  args=(call, cycle_id, prior_id, prefix, product_ids))
    eligible = {key: row for key, row in current.items() if not row["voided"]}
    if eligible:
        selector = f"crop_fertilizer_void_{cycle_id}"
        if st.session_state.get(selector) not in eligible:
            st.session_state[selector] = next(iter(eligible))
        with st.expander("Void an incorrect fertilizer application"):
            st.selectbox("Fertilizer application to void", list(eligible), key=selector,
                         format_func=lambda key: eligible[key]["data"]["entry"])
            st.text_input("Reason for voiding fertilizer application", key=selector+"_reason")
            st.button("Void fertilizer application", key=selector+"_submit", on_click=_void, args=(call, selector))
    if rows:
        products = []
        for row in eligible.values():
            data = row["data"]
            for product, amount in zip(data["products"], row["accounting"]["products"]):
                products.append(dict(entry=data["entry"], day=data["day"], product=product["name"],
                    quantity=product["quantity"], unit=product["unit"], treated_area_m2=data["area_m2"],
                    provenance=data["provenance"], analysis_source=product["analysis"]["source"],
                    **{n: value if value is not None else "unknown" for n, value in amount["nutrients"].items()}))
        if products:
            st.subheader("Current applied products")
            st.dataframe(products)
        st.subheader("Fertilizer application history")
        st.dataframe([dict(r["data"], products=len(r["data"]["products"]), record_id=r["id"],
                          superseded=r["superseded"], voided=r["voided"], reason=r["reason"]) for r in rows])
        render_fertilizer_totals(journal["fertilizer"])
