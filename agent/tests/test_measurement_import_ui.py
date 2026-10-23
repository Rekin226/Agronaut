"""Real crop page import actions with uploaded bytes supplied to the upload widget."""

import pytest

pytest.importorskip("streamlit.testing.v1")
import streamlit as st  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

from agent.tests.test_crop_ui import SCRIPT  # noqa: E402
from agent.tests.test_field_ui import setup  # noqa: E402
from agronaut_agent.tests.test_measurement_import import csv_text, rows  # noqa: E402


class Upload:
    def __init__(self, content):
        self.content = content.encode("utf-8")
        self.size = len(self.content)

    def getvalue(self):
        return self.content


def uploaded_page(tmp_path, monkeypatch):
    brain, unit, _ = setup(tmp_path)
    data = rows()
    data[0]["cycle_id"] = None
    upload = [Upload(csv_text(data))]
    monkeypatch.setattr(st, "file_uploader", lambda *args, **kwargs: upload[0])
    at = AppTest.from_string(SCRIPT).run(timeout=15)
    assert not at.exception
    return at, brain, unit, data, upload


def click(at, label):
    next(b for b in at.button if b.label == label).click().run(timeout=15)
    assert not at.exception


def test_web_preview_save_and_retry_without_model(tmp_path, monkeypatch):
    at, brain, _, _, _ = uploaded_page(tmp_path, monkeypatch)
    assert not any(b.label == "Save reviewed measurement import" for b in at.button)
    click(at, "Preview measurement import")
    assert not at.error
    assert brain.crop_records("web", "operator", "list_measurements", {})["measurements"] == []
    click(at, "Save reviewed measurement import")
    assert not at.error
    saved = brain.crop_records("web", "operator", "list_measurements", {})["measurements"]
    assert len(saved) == 2 and all(m["effective_quality"] == "unverified" for m in saved)
    click(at, "Preview measurement import")
    click(at, "Save reviewed measurement import")
    assert brain.crop_records("web", "operator", "list_measurements", {})["measurements"] == saved


def test_changed_upload_requires_new_preview_and_invalid_row_saves_nothing(tmp_path, monkeypatch):
    at, brain, _, data, upload = uploaded_page(tmp_path, monkeypatch)
    click(at, "Preview measurement import")
    data[1]["data"]["unit"] = "L"
    upload[0] = Upload(csv_text(data))
    at.run(timeout=15)
    assert not any(b.label == "Save reviewed measurement import" for b in at.button)
    click(at, "Preview measurement import")
    assert at.error and "row 2" in at.error[0].value
    assert not any(b.label == "Save reviewed measurement import" for b in at.button)
    assert brain.crop_records("web", "operator", "list_measurements", {})["measurements"] == []


def test_switching_units_clears_import_preview(tmp_path, monkeypatch):
    at, brain, _, _, _ = uploaded_page(tmp_path, monkeypatch)
    click(at, "Preview measurement import")
    brain.create_unit("web", "operator", {"farm": {"name": "Other", "timezone": "Asia/Taipei"},
        "unit": {"name": "Hydro", "production_system": "hydroponics", "medium": "water",
                 "environment": "greenhouse", "water_strategy": "fully_irrigated"}})
    at.run(timeout=15)
    assert not at.exception
    assert not any(b.label == "Save reviewed measurement import" for b in at.button)
    assert brain.crop_records("web", "operator", "list_measurements", {})["measurements"] == []
