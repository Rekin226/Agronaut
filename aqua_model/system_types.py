"""Growing methods — the design is flexible, not one fixed framework.

An operator can build the same species/crop as raft/DWC, NFT, or a media bed, and each is a
genuinely different system: different grow-bed water volume, a different bill of materials,
and different considerations. This module holds the CITED per-method geometry so the
deterministic sizing adapts to the chosen method while every number stays traceable.

The fish/feed sizing is method-independent (it is driven by plant area via the feeding-rate
ratio), so only the water geometry and the component set change between methods.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SystemType:
    key: str
    name: str
    water_depth_m: float          # effective standing water per m² of grow area
    water_depth_low: float
    water_depth_high: float
    provides_biofiltration: bool  # media beds host nitrifiers on the media surface
    grow_bed_label: str           # for the bill of materials and the schematic
    grow_bed_item: str
    considerations: list          # honest, method-specific notes surfaced to the operator
    source: str


RAFT = SystemType(
    key="raft", name="raft / deep-water culture (DWC)",
    water_depth_m=0.30, water_depth_low=0.20, water_depth_high=0.40,
    provides_biofiltration=False,
    grow_bed_label="raft / DWC grow beds", grow_bed_item="raft / DWC grow bed",
    considerations=[
        "Deep water buffers temperature and pH swings — forgiving and beginner-friendly.",
        "Highest water volume of the three methods (more makeup water to warm/cool).",
    ],
    source="FAO589",
)

NFT = SystemType(
    key="nft", name="nutrient film technique (NFT)",
    water_depth_m=0.03, water_depth_low=0.01, water_depth_high=0.05,
    provides_biofiltration=False,
    grow_bed_label="NFT channels", grow_bed_item="NFT channels",
    considerations=[
        "Channels hold only a thin film, so the system carries little water — light and cheap.",
        "Little thermal/chemical buffering; a pump failure dries roots within hours — needs "
        "reliable power and backup.",
        "Best for lightweight leafy greens and herbs; heavy fruiting crops need support.",
    ],
    source="LIT",
)

MEDIA_BED = SystemType(
    key="media_bed", name="media bed (flood & drain)",
    water_depth_m=0.12, water_depth_low=0.08, water_depth_high=0.16,
    provides_biofiltration=True,
    grow_bed_label="media beds (flood & drain)", grow_bed_item="media bed (gravel/clay)",
    considerations=[
        "The media itself hosts nitrifiers and captures solids — it provides biofiltration, "
        "so a separate biofilter can often be reduced (size conservatively before removing it).",
        "Heavier and needs more media, but robust and good for fruiting crops.",
        "Effective water volume is the void space in the media (~40%), between raft and NFT.",
    ],
    source="LIT",
)

SYSTEM_TYPES = {st.key: st for st in (RAFT, NFT, MEDIA_BED)}
DEFAULT_SYSTEM_TYPE = "raft"


def get_system_type(key: str | None) -> SystemType:
    k = (key or DEFAULT_SYSTEM_TYPE).strip().lower()
    if k not in SYSTEM_TYPES:
        raise KeyError(f"unknown system_type {key!r}; known: {sorted(SYSTEM_TYPES)}")
    return SYSTEM_TYPES[k]
