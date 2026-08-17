"""Turn a visual observation's prose into the categorical features `aqua_model.triage` accepts.

This is the bridge between the untrusted half of the vision path and the trusted half. The
vision model writes prose; the trust zone only accepts a typed, vocabulary-checked
`ObservationFeatures`. This module does that conversion deterministically — no LLM, no
network — so the mapping is auditable and testable like everything else in the pipeline.

Why it is safe to do this with keywords:

* Every feature is CATEGORICAL. The worst a misread does is select a different branch of a
  differential; it cannot put a number into the sizing engine, because there is no numeric
  field to put one in.
* Features are SCOPED BY SUBJECT. "white spots" means one thing on a fish and something else
  on a leaf, so fish features are only read when the text is about fish and leaf features only
  when it is about plants. Without that, a leaf-spot observation would silently become a fish
  disease differential.
* Missing a feature degrades to a shorter differential or none at all — never to a wrong
  confident answer. `triage` returns empty for an empty observation, and the caller asks for
  a better photo.

Lives in `agent/` because it parses prose; the vocabulary and the table it feeds live in
`aqua_model/` (the trust zone's dependency rule: agent may import aqua_model, never the
reverse).
"""

from __future__ import annotations

import re

from aqua_model.triage import ObservationFeatures, validate_observation_features


def _rx(*alternatives: str) -> re.Pattern:
    return re.compile("|".join(alternatives), re.IGNORECASE)


# --- subject detection ------------------------------------------------------------------
_PLANT_CUE = _rx(r"\bleaf\b", r"\bleaves\b", r"\bplant", r"\bfoliage\b", r"\bseedling",
                 r"\bcrop\b", r"\blettuce\b", r"\bbasil\b", r"\bkale\b", r"\bspinach\b",
                 r"\btomato", r"\bchard\b", r"\bherb", r"\bstem", r"\bcanopy\b")
_FISH_CUE = _rx(r"\bfish\b", r"\btilapia\b", r"\bcatfish\b", r"\bclarias\b", r"\btrout\b",
                r"\bcarp\b", r"\bkoi\b", r"\bperch\b", r"\bgill", r"\bfins?\b", r"\bscale")
_WATER_CUE = _rx(r"\bwater\b", r"\btank\b", r"\bsump\b", r"\bpond\b")
_ROOT_CUE = _rx(r"\broots?\b", r"\broot zone\b", r"\broot-zone\b")
_EQUIP_CUE = _rx(r"\bpump\b", r"\bpipe", r"\braft\b", r"\bnet\b", r"\bstandpipe\b",
                 r"\bfilter\b", r"\bchannel\b", r"\bbed\b", r"\bair ?stone\b", r"\bvalve\b")

# --- leaf features ----------------------------------------------------------------------
_OLD_LEAF = _rx(r"\bolder?\b", r"\blower\b", r"\bouter\b", r"\bbottom\b", r"\bmature\b")
_NEW_LEAF = _rx(r"\bnew(?:est)?\b", r"\byoung(?:est)?\b", r"\bupper\b", r"\binner\b",
                r"\btop\b", r"\bnew growth\b", r"\bgrowing tip")

_INTERVEINAL = _rx(r"\binterveinal\b", r"\bbetween the veins\b", r"\bveins? (?:are |remain )?"
                   r"(?:still )?green\b", r"\bgreen veins?\b")
_MARGIN = _rx(r"\bmargins?\b", r"\bedges?\b", r"\brims?\b", r"\btips? and edges\b")
_SCORCH = _rx(r"\bscorch", r"\bburn", r"\bbrown", r"\bcrisp", r"\bdry\b", r"\bnecro")
_WHOLE_PALE = _rx(r"\buniform(?:ly)? pale\b", r"\bpale (?:green|yellow)\b",
                  r"\bwhole (?:leaf|plant)\b", r"\boverall (?:pale|yellow)",
                  r"\bgenerally pale\b", r"\ball over\b")
_TIP_BURN = _rx(r"\btip ?burn\b", r"\btips? (?:are |look )?(?:brown|burnt|burned|dead)\b",
                r"\bdistorted\b", r"\bdeformed\b", r"\bcurled and brown\b",
                r"\bblossom.end\b")
_STIPPLED = _rx(r"\bstippl", r"\bspeckl", r"\bfine specks?\b", r"\bmottl")
_SPOTS = _rx(r"\bspots?\b", r"\bspotting\b", r"\bblotch", r"\blesions? on the leaf\b")
_HOLES = _rx(r"\bholes?\b", r"\bchew", r"\bbitten\b", r"\beaten\b", r"\bragged edges\b")
_POWDER = _rx(r"\bpowder", r"\bwhite dust\b", r"\bwhite (?:film|bloom) on the (?:leaf|leaves)\b",
              r"\bmildew\b")
_WEBBING = _rx(r"\bwebbing\b", r"\bfine webs?\b", r"\bcobweb")

# --- colours ----------------------------------------------------------------------------
_COLOUR_CUES = (
    ("yellow", _rx(r"\byellow", r"\bchloro", r"\bpale green\b")),
    ("brown", _rx(r"\bbrown", r"\bnecro", r"\bscorch")),
    ("white", _rx(r"\bwhite\b", r"\bwhitish\b")),
    ("purple", _rx(r"\bpurpl", r"\bviolet\b")),
    ("dark_green", _rx(r"\bdark green\b", r"\bdeep green\b")),
    ("red", _rx(r"\bred\b", r"\breddish\b")),
)

# --- roots ------------------------------------------------------------------------------
_ROOT_BAD = _rx(r"\bbrown\b", r"\bslim", r"\bfoul\b", r"\bsmell", r"\brot", r"\bmush",
                r"\bdiscolo")
_ROOT_GOOD = _rx(r"\bwhite\b", r"\bfirm\b", r"\bhealthy\b", r"\bcream")

# --- water ------------------------------------------------------------------------------
_WATER_GREEN = _rx(r"\bgreen water\b", r"\bpea.?soup\b", r"\bwater (?:is |looks |appears )?"
                   r"(?:bright |dark )?green\b", r"\bgreen(?:ish)? and (?:cloudy|murky)\b")
_WATER_CLOUDY = _rx(r"\bcloudy\b", r"\bmurky\b", r"\bturbid\b", r"\bhazy\b", r"\bmilky\b")
_WATER_BROWN = _rx(r"\bbrown water\b", r"\btea.?colou?red\b",
                   r"\bwater (?:is |looks |appears )?brown\b")
_WATER_CLEAR = _rx(r"\bwater is clear\b", r"\bclear water\b", r"\bwater (?:looks|appears) clear\b")

# --- fish -------------------------------------------------------------------------------
# "at the surface" alone is too broad — algae, debris and light all sit on the surface. The
# phrase only signals gasping when a fish is DOING something there, so require the verb.
_GASPING = _rx(r"\bgasp", r"\bpiping\b", r"\bmouths? at the surface\b",
               r"\bmouthing the surface\b", r"\bgulping\b",
               r"\b(?:holding|hanging|sitting|staying|stay|gathered|gathering|crowding|"
               r"crowded|hovering|congregat\w+|swimming|swim)\s+(?:up\s+)?"
               r"(?:at|near|by|on)\s+the surface\b")
_LETHARGIC = _rx(r"\blethargic\b", r"\bslow(?:ly)?\b", r"\bsluggish\b", r"\blistless\b",
                 r"\bmotionless\b", r"\bhardly moving\b", r"\binactive\b")
_NOT_EATING = _rx(r"\bnot eating\b", r"\boff (?:their )?feed\b", r"\brefusing (?:food|feed)\b",
                  r"\bnot feeding\b", r"\bleft the feed\b", r"\beating less\b")
_FLASHING = _rx(r"\bflashing\b", r"\brubbing\b", r"\bscratching\b", r"\bdarting\b",
                r"\bscraping\b")
_CLAMPED = _rx(r"\bclamped\b", r"\bfins? (?:held )?(?:close|clamped|tight)")
_FISH_WHITE_SPOTS = _rx(r"\bwhite spots?\b", r"\bsalt grains?\b", r"\bwhite specks?\b",
                        r"\bwhite dots?\b")
_LESION = _rx(r"\blesion", r"\bsores?\b", r"\bulcer", r"\bwound", r"\bred patch",
              r"\bopen (?:sore|wound)")
_FRAYED = _rx(r"\bfray", r"\bragged fins?\b", r"\btorn fins?\b", r"\bfin rot\b",
              r"\beroded fins?\b")
_SWOLLEN = _rx(r"\bswollen\b", r"\bbloat", r"\bdistended\b", r"\bpop.?eye\b", r"\bdropsy\b")
_COTTON = _rx(r"\bcotton", r"\btufts?\b", r"\bfluffy white\b", r"\bsaprolegnia\b")

# --- pests ------------------------------------------------------------------------------
_PEST_CUES = (
    ("aphids", _rx(r"\baphid")),
    ("whiteflies", _rx(r"\bwhitefl", r"\bwhite fl(?:y|ies)\b")),
    ("mites", _rx(r"\bmites?\b", r"\bspider mite")),
    ("gnats", _rx(r"\bgnats?\b", r"\bfungus gnat")),
)


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?;])\s+", text) if s.strip()]


def _near(text: str, cue_a: re.Pattern, cue_b: re.Pattern) -> bool:
    """True when both cues appear in the SAME sentence. Co-occurrence within one sentence is
    a much better proxy for 'these describe the same thing' than presence anywhere in the
    paragraph — "the roots are white and firm. The older leaves are brown." must not read as
    brown slimy roots."""
    return any(cue_a.search(s) and cue_b.search(s) for s in _sentences(text))


def _scope(text: str, cue: re.Pattern) -> str:
    """Just the sentences that mention this subject.

    Domain cues are then read against that slice instead of the whole observation, so
    "The fish look healthy but a mat of algae floats on the surface" does not register fish
    gasping, and "the water moves slowly" does not register a lethargic fish. This narrows
    the window; it does not eliminate the problem — within a single sentence that mentions
    two subjects, cues can still cross ("the pump is slow and the fish are fine"). Getting
    that last case right needs parsing, and the cost of being wrong here is bounded: a
    spurious feature adds one more candidate to a differential that never claims certainty,
    and the environment-first ordering means the extra candidate is a cheap, safe check."""
    return " ".join(s for s in _sentences(text) if cue.search(s))


def extract_observation_features(text: str) -> ObservationFeatures:
    """Prose → categorical features. Pure and total: any string in, valid features out.

    Never raises: an unrecognisable observation yields empty features, and `triage` turns
    that into "ask for a better photo" rather than a guess."""
    if not text or not text.strip():
        return ObservationFeatures()

    subject: list[str] = []
    if _PLANT_CUE.search(text):
        subject.append("plant")
    if _FISH_CUE.search(text):
        subject.append("fish")
    if _WATER_CUE.search(text):
        subject.append("water")
    if _ROOT_CUE.search(text):
        subject.append("roots")
    if _EQUIP_CUE.search(text):
        subject.append("equipment")

    leaf_age = "unknown"
    leaf_pattern: list[str] = []
    colour: list[str] = []
    if "plant" in subject:
        pt = _scope(text, _PLANT_CUE)          # only the sentences about plants
        old, new = bool(_OLD_LEAF.search(pt)), bool(_NEW_LEAF.search(pt))
        leaf_age = "both" if old and new else "old" if old else "new" if new else "unknown"

        if _INTERVEINAL.search(pt):
            leaf_pattern.append("interveinal")
        if _near(pt, _MARGIN, _SCORCH):
            leaf_pattern.append("margin_scorch")
        if _WHOLE_PALE.search(pt):
            leaf_pattern.append("whole_pale")
        if _TIP_BURN.search(pt):
            leaf_pattern.append("tip_burn")
        if _STIPPLED.search(pt):
            leaf_pattern.append("stippled")
        if _HOLES.search(pt):
            leaf_pattern.append("holes")
        if _POWDER.search(pt):
            leaf_pattern.append("powder")
        if _WEBBING.search(pt):
            leaf_pattern.append("webbing")
        # Leaf "spots" only when this is NOT a fish observation — otherwise a fish's white
        # spots would be double-counted as a leaf pattern too.
        if _SPOTS.search(pt) and "fish" not in subject:
            leaf_pattern.append("spots")

        for name, cue in _COLOUR_CUES:
            if cue.search(pt):
                colour.append(name)

    root_state = "unknown"
    if "roots" in subject:
        if _near(text, _ROOT_CUE, _ROOT_BAD):
            root_state = "brown_slimy"
        elif _near(text, _ROOT_CUE, _ROOT_GOOD):
            root_state = "white_healthy"

    water_state = "unknown"
    if "water" in subject:
        wt = _scope(text, _WATER_CUE)          # only the sentences about water
        if _WATER_GREEN.search(wt):
            water_state = "green"
        elif _WATER_BROWN.search(wt):
            water_state = "brown"
        elif _WATER_CLOUDY.search(wt):
            water_state = "cloudy"
        elif _WATER_CLEAR.search(wt):
            water_state = "clear"

    fish_behaviour: list[str] = []
    fish_body: list[str] = []
    if "fish" in subject:
        ft = _scope(text, _FISH_CUE)           # only the sentences about fish
        if _GASPING.search(ft):
            fish_behaviour.append("gasping_surface")
        if _LETHARGIC.search(ft):
            fish_behaviour.append("lethargic")
        if _NOT_EATING.search(ft):
            fish_behaviour.append("not_eating")
        if _FLASHING.search(ft):
            fish_behaviour.append("flashing")
        if _CLAMPED.search(ft):
            fish_behaviour.append("clamped_fins")
        if _FISH_WHITE_SPOTS.search(ft):
            fish_body.append("white_spots")
        if _LESION.search(ft):
            fish_body.append("lesion")
        if _FRAYED.search(ft):
            fish_body.append("frayed_fins")
        if _SWOLLEN.search(ft):
            fish_body.append("swollen")
        if _COTTON.search(ft):
            fish_body.append("cotton_tufts")

    pests: list[str] = [name for name, cue in _PEST_CUES if cue.search(text)]

    # Route through the trust gate rather than constructing directly, so a vocabulary drift
    # between this module and the table fails loudly in tests instead of silently here.
    return validate_observation_features(
        subject=subject, leaf_age=leaf_age, leaf_pattern=leaf_pattern, colour=colour,
        root_state=root_state, water_state=water_state, fish_behaviour=fish_behaviour,
        fish_body=fish_body, pests_visible=pests,
    )
