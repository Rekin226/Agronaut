"""The full app exposes cross-unit crop work without a model or an implicit switch."""

import pytest

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402

from agent.tests.test_field_ui import setup  # noqa: E402
from agronaut_agent.tests.test_crop_calendar import task  # noqa: E402

SCRIPT = """
import streamlit as st
import app
from agent.tests.test_field_ui import _SHARED
st.session_state.agent = _SHARED['brain']
st.session_state.web_user = 'operator'
st.session_state.app_mode = 'Farm overview'
app.main()
"""


def test_full_app_shows_both_production_systems_without_switching(tmp_path):
    brain, unit, _ = setup(tmp_path)
    created = brain.crop_records("web", "operator", "create", dict(crop="tomato", planted_on="2026-07-01",
                                source="Synthetic", request_id="plant"))
    brain.crop_records("web", "operator", "plan_task", dict(cycle_id=created["cycle"]["id"], data=task(), request_id="task"))
    aquatic = brain.create_unit("web", "operator", {"farm": {"id": unit["farm_id"]},
        "unit": {"name": "Pond", "production_system": "aquaponics", "medium": "water",
                 "environment": "greenhouse", "water_strategy": "fully_irrigated"}})
    brain.crop_records("web", "operator", "create", dict(crop="lettuce", planted_on="2026-07-01",
                                source="Synthetic", request_id="plant"))
    before = brain.farm_overview("web", "operator")
    at = AppTest.from_string(SCRIPT).run(timeout=15)
    assert not at.exception and not at.error
    labels = [node.label for node in at.expander]
    assert any("Field · soil" in label for label in labels)
    assert any("Pond · aquaponics" in label for label in labels)
    assert any("title" in frame.value.columns and "Synthetic harvest inspection" in frame.value["title"].tolist()
               for frame in at.dataframe)
    assert next(u["id"] for u in brain.list_units("web", "operator") if u["active"]) == aquatic["id"]
    assert brain.farm_overview("web", "operator")["farms"] == before["farms"]


def test_unknown_timezone_is_visible_without_a_false_all_clear(tmp_path):
    brain, unit, _ = setup(tmp_path)
    brain._farms.db.execute("UPDATE farms SET timezone=NULL WHERE id=?", (unit["farm_id"],))
    at = AppTest.from_string(SCRIPT).run(timeout=15)
    assert not at.exception
    assert any("timezone is missing" in item.value for item in at.warning)
    assert not any("No recorded tasks currently due" in item.value for item in at.info)


def test_other_users_farms_are_not_rendered(tmp_path):
    brain, _, _ = setup(tmp_path)
    brain.create_unit("web", "someone_else", {"farm": {"name": "Private orchard", "timezone": "Asia/Taipei"},
        "unit": {"name": "Private field", "production_system": "soil", "medium": "soil",
                 "environment": "open_field", "water_strategy": "rainfed"}})
    at = AppTest.from_string(SCRIPT).run(timeout=15)
    assert not at.exception
    assert "Private orchard" not in [s.value for s in at.subheader]
    assert not any("Private field" in e.label for e in at.expander)


def test_equipment_reservations_and_review_flags_are_visible_without_mutation(tmp_path):
    from agronaut_agent.equipment_calendar import EquipmentCalendar
    from agronaut_agent.tests.test_equipment_calendar import asset

    brain, unit, _ = setup(tmp_path)
    owner = brain._conv.get_or_create_user('web', 'operator')
    calendar = EquipmentCalendar(brain._farms.db)
    pump = calendar.save_equipment(owner, unit['farm_id'], asset(), request_id='pump')
    saved = calendar.reserve_equipment(owner, unit['farm_id'], dict(equipment_ids=[pump['id']],
        start='2099-10-20T08:00:00Z', end='2099-10-20T09:00:00Z', title='Shared irrigation equipment',
        source='Synthetic operator diary'), request_id='booking')
    calendar.save_equipment(owner, unit['farm_id'], asset(status='unavailable'), request_id='repair',
        supersedes_id=pump['id'], reason='Repair needed')
    before = brain._farms.export(owner)
    selected = next(u['id'] for u in brain.list_units('web', 'operator') if u['active'])
    at = AppTest.from_string(SCRIPT).run(timeout=15)
    assert not at.exception and not at.error
    rows = next(frame.value for frame in at.dataframe if 'booking_id' in frame.value.columns)
    assert rows['booking_id'].tolist() == [saved['id']]
    assert rows['needs_review'].tolist() == [True]
    assert rows['timing'].tolist() == ['upcoming']
    assert any('Elapsed reservations do not prove completed work' in item.value for item in at.caption)
    assert brain._farms.export(owner) == before
    assert next(u['id'] for u in brain.list_units('web', 'operator') if u['active']) == selected
