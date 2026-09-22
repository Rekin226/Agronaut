"""The viewer's crop-form table must only describe crops the database actually has.

The bug this guards against is silent: `cropForm()` falls back to a generic rosette for
any key it does not know, so a typo'd or renamed key renders the same dull green shape
as before and nothing fails — the drawing just stops matching the crop it names. That
happened for real: the table shipped a `chard` row while `crops.py` defines
`swiss_chard`, so chard designs fell through to the fallback and the row was dead code.

These tests read `web/viewer_template.html` directly rather than parsing JavaScript,
because the table is data, not logic, and the two files drift apart precisely when
nobody is looking at both.
"""

import re
from pathlib import Path

from aqua_model.crops import CROPS

_VIEWER = Path(__file__).resolve().parent.parent.parent / "web" / "viewer_template.html"


def _crop_forms():
    """Parse the CROP_FORMS table into {key: {field: value}} — the fields are plain
    numbers except `tint`, which the viewer writes as a 0xRRGGBB literal."""
    text = _VIEWER.read_text(encoding="utf-8")
    block = re.search(r"const CROP_FORMS = \{(.*?)\n\};", text, re.DOTALL)
    assert block, "CROP_FORMS table not found in viewer_template.html"
    forms = {}
    for key, body in re.findall(r"^  (\w+):\s*\{([^}]*)\}", block.group(1), re.MULTILINE):
        fields = dict(re.findall(r"(\w+):\s*([0-9x.]+)", body))
        forms[key] = fields
    return forms


def test_every_form_key_is_a_real_crop_key():
    unknown = set(_crop_forms()) - set(CROPS)
    assert not unknown, (
        "CROP_FORMS has keys that are not crop names in aqua_model/crops.py; the "
        f"viewer silently falls back to a generic rosette for them: {sorted(unknown)}")


def test_amaranth_and_swiss_chard_have_their_own_form():
    forms = _crop_forms()
    for key in ("amaranth", "swiss_chard"):
        assert key in forms, (
            f"{key} is in the crop database but has no CROP_FORMS row; it renders as "
            "the generic fallback rosette, indistinguishable from every other "
            "unmodelled crop")


def test_amaranth_does_not_read_as_lettuce():
    """Issue #114's acceptance: the new row must visibly differ when you walk in."""
    forms = _crop_forms()
    amaranth, lettuce = forms["amaranth"], forms["lettuce"]
    differing = [f for f in ("h", "leaves", "tilt", "tint", "stem")
                 if float(int(amaranth[f], 16) if f == "tint" else amaranth[f])
                 != float(int(lettuce[f], 16) if f == "tint" else lettuce[f])]
    assert len(differing) >= 3, (
        f"amaranth differs from lettuce only in {differing}; a viewer walking in would "
        "not be able to tell the two crops apart")
