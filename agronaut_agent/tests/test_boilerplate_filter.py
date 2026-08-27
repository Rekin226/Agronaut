"""The knowledge-index boilerplate filter.

`_is_boilerplate_text` decides what gets dropped before a page reaches the knowledge index, so
a false positive silently removes real agronomy from retrieval and nobody notices. It had a
duplicate inline copy in `build_vector_store` for a while (#19, fixed by @jordansilly77-stack
in #31) — the two had already drifted apart by the time anyone looked. These tests exist so
they cannot drift again, and so the retrieval-quality bug that dedupe fixed stays fixed.
"""

from srcs.chatbot import _is_boilerplate_text


def _long(text: str, pad: str = " agronomy detail.") -> str:
    """Pad past the 200-character floor without changing what's being tested."""
    while len(text) < 260:
        text += pad
    return text


# --- the length floor -------------------------------------------------------------------

def test_empty_and_short_text_is_boilerplate():
    assert _is_boilerplate_text("") is True
    assert _is_boilerplate_text(None) is True
    assert _is_boilerplate_text("Tilapia like warm water.") is True   # true but too short


def test_a_long_substantive_passage_survives():
    assert _is_boilerplate_text(_long(
        "Iron deficiency shows as interveinal chlorosis on new leaves, and in a cycled system "
        "it is usually pH lockout above about 7.0 rather than an actual absence of iron.")) is False


# --- keyword matches --------------------------------------------------------------------

def test_navigation_and_legal_keywords_are_boilerplate():
    for keyword in ("privacy policy", "cookies", "terms of use", "terms and conditions",
                    "accessibility", "legal notice", "all rights reserved",
                    "help and support", "contact us", "subscribe", "sign in", "log in"):
        assert _is_boilerplate_text(_long(f"Please see our {keyword} for details.")) is True, keyword


def test_keyword_matching_is_case_insensitive():
    assert _is_boilerplate_text(_long("PRIVACY POLICY and Cookies apply.")) is True


# --- the regression this file exists for ------------------------------------------------
# The inline duplicate matched a bare "terms". That is a substring of the ordinary English
# phrase "in terms of", so any passage using it was silently dropped from the knowledge index.
# Narrowing to "terms of use"/"terms and conditions" was the real win in #31.
#
# Verified against the pre-#31 implementation: both passages below were dropped by it, and
# both are kept now. (Note "long-term" was NEVER affected — no trailing "s" — so the phrase
# under test is specifically "in terms of".)

def test_the_phrase_in_terms_of_is_not_boilerplate():
    for passage in (
        "In terms of feed conversion, African catfish is markedly more efficient than tilapia, "
        "which changes the feed budget for the same standing biomass.",
        "Growers often describe results in terms of kilograms per square metre per year, which "
        "makes comparing a raft system with vertical towers harder than it looks.",
        "Iron and calcium behave differently in terms of mobility, which is why one shows on "
        "new growth and the other does not.",
    ):
        assert _is_boilerplate_text(_long(passage)) is False, passage


def test_ordinary_prose_is_kept_while_a_real_legal_footer_is_dropped():
    keeps = _long("Considered in terms of water use, vertical towers win on floor area.")
    drops = _long("By using this site you accept our terms of use and privacy policy.")
    assert _is_boilerplate_text(keeps) is False
    assert _is_boilerplate_text(drops) is True


# --- the short-lines heuristic ----------------------------------------------------------
# The other half of #31's behaviour change: nav-menu soup is many short lines and little prose.

def test_a_stack_of_short_lines_is_boilerplate():
    menu = "\n".join(["Home", "About", "Products", "Species", "Crops", "Blog", "News",
                      "Careers", "Support", "Downloads", "Partners", "Events", "Press",
                      "Locations", "Sitemap", "Français", "Español"] * 2)
    assert len(menu) > 200                      # long enough to clear the floor
    assert _is_boilerplate_text(menu) is True   # ...but still boilerplate


def test_wrapped_prose_is_not_mistaken_for_a_menu():
    """Prose wrapped at a normal width has long lines, so it must not trip the ratio check."""
    prose = "\n".join([
        "Potassium deficiency shows as scorch at the margins of older leaves, and fish feed",
        "is low in potassium, so it is an expected shortfall rather than a surprise. The usual",
        "fix is potassium bicarbonate, which doubles as a pH buffer and so addresses two",
        "problems with one input rather than fighting them separately.",
    ])
    assert _is_boilerplate_text(prose) is False


def test_a_long_document_of_short_lines_is_kept():
    """The ratio check only fires under 2,500 chars — a genuinely long document of short lines
    (a table, a species list) is real content, not navigation."""
    rows = "\n".join(f"tilapia,{i},1.7,26" for i in range(400))
    assert len(rows) > 2500
    assert _is_boilerplate_text(rows) is False


# --- the property that made #19 a bug in the first place --------------------------------

def test_the_index_builder_and_the_retriever_share_one_implementation():
    """#19 was two copies of this logic that had already drifted. If a second copy reappears,
    this catches it: the filter must be defined once at module level and used everywhere."""
    import inspect

    import srcs.chatbot as chatbot

    source = inspect.getsource(chatbot)
    assert source.count("def _is_boilerplate_text") == 1
    assert "def looks_like_boilerplate" not in source, (
        "a second boilerplate filter has reappeared — see #19; there must be exactly one")
