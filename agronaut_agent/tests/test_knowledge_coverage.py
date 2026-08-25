"""Knowledge-base coverage: the Tier-3 economics / feasibility / food-safety / regulations
docs exist, are substantive, and carry a sources line — so the RAG layer can cite them.
"""

import pathlib

KB = pathlib.Path(__file__).resolve().parents[2] / "knowledge"

TIER3 = [
    "economics_and_costs.md",
    "feasibility_and_business_case.md",
    "food_safety_and_hygiene.md",
    "regulations_and_permits.md",
]


def test_tier3_docs_exist_and_are_substantive():
    for name in TIER3:
        doc = KB / name
        assert doc.exists(), f"missing Tier-3 KB doc: {name}"
        text = doc.read_text()
        assert len(text) > 800, f"{name} is too thin to be useful"
        assert text.lstrip().startswith("# "), f"{name} needs a title heading"


def test_tier3_docs_cite_sources():
    for name in TIER3:
        text = (KB / name).read_text().lower()
        assert "source" in text or "fao" in text, f"{name} has no citation"
