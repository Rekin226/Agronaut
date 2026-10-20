"""Independent catch-grid arithmetic and rejection of incompatible evidence."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from agro_model.catch_can import evaluate_catch_test


def example():
    return json.loads(
        (Path(__file__).resolve().parents[2] / "examples/sprinkler_catches.json").read_text()
    )


def test_independent_grid_mean_low_quarter_and_rate_preserve_sources():
    data = example()
    original = deepcopy(data)
    result = evaluate_catch_test(data)
    stats = result["statistics"]
    assert stats == dict(
        mean_depth_mm=148 / 16,
        low_quarter_mean_depth_mm=28 / 4,
        low_quarter_du_pct=pytest.approx(100 * 28 / 37),
        mean_catch_rate_mm_h=18.5,
        minimum_depth_mm=6,
        maximum_depth_mm=12,
    )
    assert result["low_quarter_positions"] == [dict(row=1, column=i) for i in range(1, 5)]
    assert result["inputs"] == original and data == original
    assert result["feasible"] is None and result["sources"]
    assert result["event_ended_at"] == "2026-07-07T09:30:00+00:00"


def test_uniform_zero_and_missing_are_distinct_and_permutation_does_not_change_statistics():
    data = example()
    for row in data["catches"]:
        row["depth_mm"] = 3
    assert evaluate_catch_test(data)["statistics"]["low_quarter_du_pct"] == 100
    data["catches"].reverse()
    assert evaluate_catch_test(data)["low_quarter_positions"] == [
        dict(row=1, column=i) for i in range(1, 5)
    ]
    for row in data["catches"]:
        row["depth_mm"] = 0
    result = evaluate_catch_test(data)
    assert result["statistics"]["mean_catch_rate_mm_h"] == 0
    assert result["statistics"]["low_quarter_du_pct"] is None
    assert not result["missing_inputs"]
    data["catches"][0]["depth_mm"] = None
    result = evaluate_catch_test(data)
    assert all(value is None for value in result["statistics"].values())
    assert result["missing_inputs"] == ["depth_mm at row 4, column 4"]


@pytest.mark.parametrize(
    "kind",
    [
        "bool_version",
        "extra",
        "missing",
        "duplicate",
        "outside",
        "bool_position",
        "negative",
        "nan",
        "infinite",
        "depth_string",
        "no_source",
        "rain",
        "exposure",
        "unequal",
        "fractional_quarter",
        "too_many",
        "spacing",
        "zero_duration",
        "tiny_duration",
        "future",
        "naive",
        "bad_zone",
        "unknown_kind",
        "bad_wind",
    ],
)
def test_invalid_or_inapplicable_inputs_fail(kind):
    data = example()
    if kind == "bool_version":
        data["schema_version"] = True
    elif kind == "extra":
        data["efficiency"] = 0.8
    elif kind == "missing":
        data["catches"].pop()
    elif kind == "duplicate":
        data["catches"][-1] = data["catches"][0]
    elif kind == "outside":
        data["catches"][0]["row"] = 0
    elif kind == "bool_position":
        data["catches"][0]["row"] = True
    elif kind in ("negative", "nan", "infinite", "depth_string"):
        data["catches"][0]["depth_mm"] = dict(
            negative=-1, nan=float("nan"), infinite=float("inf"), depth_string="3"
        )[kind]
    elif kind == "no_source":
        data["catches"][0]["source"] = ""
    elif kind == "rain":
        data["event"]["rain_free"] = False
    elif kind == "exposure":
        data["event"]["same_exposure"] = "true"
    elif kind == "unequal":
        data["grid"]["equal_area"] = False
    elif kind == "fractional_quarter":
        data["grid"].update(rows=3, columns=3)
    elif kind == "too_many":
        data["grid"].update(rows=100, columns=100)
    elif kind == "spacing":
        data["grid"]["spacing_x_m"] = 0
    elif kind == "zero_duration":
        data["event"]["duration_seconds"] = 0
    elif kind == "tiny_duration":
        data["event"]["duration_seconds"] = 1e-300
    elif kind == "future":
        data["event"].update(kind="observed", started_at="9998-01-01T00:00:00Z")
    elif kind == "naive":
        data["event"]["started_at"] = "2026-07-07T12:00:00"
    elif kind == "bad_zone":
        data["site"]["timezone"] = "not-a-zone"
    elif kind == "unknown_kind":
        data["event"]["kind"] = "forecast"
    elif kind == "bad_wind":
        data["event"]["wind_m_s"] = -1
    with pytest.raises(ValueError):
        evaluate_catch_test(data)


def test_elapsed_exposure_crosses_dst_and_unknown_wind_is_visible():
    data = example()
    data["site"]["timezone"] = "America/New_York"
    data["event"].update(
        started_at="2026-11-01T01:30:00-04:00", duration_seconds=3600, wind_m_s=None
    )
    result = evaluate_catch_test(data)
    assert result["event_ended_at"] == "2026-11-01T01:30:00-05:00"
    assert result["statistics"]["mean_catch_rate_mm_h"] == 9.25
    assert any("Wind speed is unknown" in note for note in result["notes"])
