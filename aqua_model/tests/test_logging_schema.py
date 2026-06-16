"""The logging standard validates rows loudly and stays versioned/consistent."""

from aqua_model import logging_schema as ls


def _good_row(**over):
    row = {
        "system_id": "tw-001",
        "timestamp": "2026-06-16T08:00:00Z",
        "water_temp_c": 26.0,
        "ph": 6.8,
        "ammonia_mg_l": 0.1,
        "nitrite_mg_l": 0.0,
        "nitrate_mg_l": 40.0,
        "feed_g": 360.0,
        "makeup_water_l": 30.0,
        "fish_mortality_count": 0,
    }
    row.update(over)
    return row


def test_schema_is_versioned():
    assert ls.SCHEMA_VERSION == "1.0.0"


def test_header_matches_field_order_and_is_stable():
    header = ls.csv_header()
    assert header[0] == "system_id"
    assert header[1] == "timestamp"
    assert len(header) == len(ls.FIELDS)


def test_good_row_validates_clean():
    assert ls.validate_row(_good_row()) == []


def test_missing_required_field_is_flagged():
    row = _good_row()
    del row["feed_g"]
    problems = ls.validate_row(row)
    assert any("feed_g" in p for p in problems)


def test_out_of_range_value_is_flagged():
    problems = ls.validate_row(_good_row(ph=99.0))
    assert any("ph" in p for p in problems)


def test_unknown_field_is_flagged_against_version():
    problems = ls.validate_row(_good_row(secret_sauce=1))
    assert any("unknown field" in p for p in problems)


def test_non_numeric_where_numeric_expected_is_flagged():
    problems = ls.validate_row(_good_row(water_temp_c="warm"))
    assert any("water_temp_c" in p for p in problems)


def test_schema_doc_renders():
    doc = ls.schema_doc()
    assert "install-logging standard v1.0.0" in doc
    assert "nitrate_mg_l" in doc
