"""Declared mineral-fertilizer nutrient accounting, not crop requirement estimation."""

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal

from .validation import choice, number, text

NUTRIENTS = ("n", "p2o5", "k2o")
SOURCES = (
    "https://extension.umd.edu/resource/garden-fertilizer-basics",
    "https://extension.umn.edu/agriculture/specialty-crops/interpreting-soil-tests-for-fruit-and-vegetable-crops",
)


def _optional_number(name, value, high):
    if value is not None:
        number(name, value, 0, high)


@dataclass(frozen=True)
class NutrientTarget:
    n_kg_ha: float | None
    p2o5_kg_ha: float | None
    k2o_kg_ha: float | None
    basis: str
    source: str
    scope_note: str

    def __post_init__(self):
        choice("target basis", self.basis, ("remaining_application",))
        text("target source", self.source)
        text("target scope_note", self.scope_note)
        for nutrient in NUTRIENTS:
            _optional_number(nutrient+" target kg/ha", getattr(self, nutrient+"_kg_ha"), 1e6)


@dataclass(frozen=True)
class ProductAnalysis:
    n_pct: float | None
    p2o5_pct: float | None
    k2o_pct: float | None
    basis: str
    source: str

    def __post_init__(self):
        choice("analysis basis", self.basis, ("mass_percent",))
        text("analysis source", self.source)
        for nutrient in NUTRIENTS:
            _optional_number(nutrient+" mass percent", getattr(self, nutrient+"_pct"), 100)


@dataclass(frozen=True)
class FertilizerProduct:
    id: str
    name: str
    kind: str
    quantity: float
    unit: str
    analysis: ProductAnalysis
    density_kg_l: float | None = None
    density_source: str | None = None

    def __post_init__(self):
        text("product id", self.id)
        text("product name", self.name)
        choice("product kind", self.kind, ("mineral",))
        number("product quantity", self.quantity, high=1e9, positive=True)
        choice("product unit", self.unit, ("kg", "L"))
        if not isinstance(self.analysis, ProductAnalysis):
            raise ValueError("product analysis is required")
        if self.unit == "kg":
            if self.density_kg_l is not None or self.density_source is not None:
                raise ValueError("kg quantities must not supply density fields")
        elif self.density_kg_l is not None:
            number("density kg/L", self.density_kg_l, high=1000, positive=True)
            text("density source", self.density_source)
        elif self.density_source is not None:
            text("density source", self.density_source)


@dataclass(frozen=True)
class NutritionBudget:
    area_m2: float
    crop: str
    stage: str
    period_start: str
    period_end: str
    target: NutrientTarget
    products: tuple[FertilizerProduct, ...]

    def __post_init__(self):
        number("area m2", self.area_m2, high=1e10, positive=True)
        text("crop", self.crop)
        text("stage", self.stage)
        for name in ("period_start", "period_end"):
            value = getattr(self, name)
            text(name, value)
            if date.fromisoformat(value).isoformat() != value:
                raise ValueError(f"{name} must use YYYY-MM-DD")
        if self.period_end < self.period_start:
            raise ValueError("period_end must be on or after period_start")
        if not isinstance(self.target, NutrientTarget):
            raise ValueError("nutrient target is required")
        _validate_products(self.area_m2, self.products)


def _validate_products(area_m2, products):
    number("area m2", area_m2, high=1e10, positive=True)
    if not isinstance(products, tuple) or len(products) > 100:
        raise ValueError("products must contain at most 100 product records")
    if any(not isinstance(p, FertilizerProduct) for p in products):
        raise ValueError("products must be fertilizer product records")
    ids = [p.id.strip() for p in products]
    if len(ids) != len(set(ids)):
        raise ValueError("product IDs must be unique; consolidate repeated product quantities")


def _d(value):
    return Decimal(str(value))


def _float(value):
    converted = float(value)
    number("calculated nutrient amount", converted)
    return converted


def _product_accounting(area_m2, supplied_products):
    _validate_products(area_m2, supplied_products)
    area_ha = _d(area_m2)/10000
    known = {n: Decimal(0) for n in NUTRIENTS}
    unknown = {n: [] for n in NUTRIENTS}
    missing, products = [], []
    sources = set(SOURCES)
    for product in supplied_products:
        sources.add(product.analysis.source)
        if product.density_source:
            sources.add(product.density_source)
        mass = _d(product.quantity) if product.unit == "kg" else (
            _d(product.quantity)*_d(product.density_kg_l) if product.density_kg_l is not None else None)
        if mass is None:
            missing.append(f"products.{product.id}.density_kg_l")
        nutrients = {}
        for nutrient in NUTRIENTS:
            percent = getattr(product.analysis, nutrient+"_pct")
            if percent is None:
                missing.append(f"products.{product.id}.analysis.{nutrient}_pct")
            # An explicit zero percentage contributes zero even when liquid density is unknown.
            amount = Decimal(0) if percent == 0 else mass*_d(percent)/100 if mass is not None and percent is not None else None
            if amount is None:
                unknown[nutrient].append(product.id)
            else:
                known[nutrient] += amount
            nutrients[nutrient+"_kg"] = _float(amount) if amount is not None else None
        products.append(dict(id=product.id, name=product.name, mass_kg=_float(mass) if mass is not None else None,
                             nutrients=nutrients, analysis_source=product.analysis.source,
                             density_source=product.density_source))
    return area_ha, known, unknown, missing, products, sources


def account_products(area_m2, products):
    """Label nutrient amounts for stated quantities; no target or uptake assumptions."""
    area, known, unknown, missing, rows, sources = _product_accounting(area_m2, products)
    totals = {}
    for nutrient in NUTRIENTS:
        total = None if unknown[nutrient] else known[nutrient]
        totals[nutrient] = dict(known_product_kg=_float(known[nutrient]), unknown_product_ids=unknown[nutrient],
            declared_total_kg=_float(total) if total is not None else None,
            declared_kg_ha=_float(total/area) if total is not None else None)
    return dict(model_version="mineral-product-accounting-1", area_ha=_float(area), products=rows,
                nutrients=totals, missing_inputs=missing, sources=sorted(sources),
                basis="N, P2O5 and K2O label mass percentages; not plant-available nutrients or measured uptake.")


def evaluate_nutrition(spec: NutritionBudget):
    area_ha, known, unknown, missing, products, sources = _product_accounting(spec.area_m2, spec.products)
    sources.add(spec.target.source)
    totals = {}
    for nutrient in NUTRIENTS:
        target = getattr(spec.target, nutrient+"_kg_ha")
        if target is None:
            missing.append(f"target.{nutrient}_kg_ha")
        total = None if unknown[nutrient] else known[nutrient]
        target_mass = None if target is None else _d(target)*area_ha
        difference = None if total is None or target_mass is None else total-target_mass
        totals[nutrient] = dict(known_product_kg=_float(known[nutrient]), unknown_product_ids=unknown[nutrient],
            declared_total_kg=_float(total) if total is not None else None,
            declared_kg_ha=_float(total/area_ha) if total is not None else None,
            target_kg_ha=target, target_kg=_float(target_mass) if target_mass is not None else None,
            supplied_minus_target_kg=_float(difference) if difference is not None else None,
            shortfall_kg=_float(max(Decimal(0), -difference)) if difference is not None else None,
            excess_kg=_float(max(Decimal(0), difference)) if difference is not None else None)
    return dict(model_version="mineral-nutrient-accounting-1", evidence_status="experimental", inputs=asdict(spec),
        area_ha=_float(area_ha), products=products, nutrients=totals, sources=sorted(sources),
        missing_inputs=missing, feasible=None, binding_constraints=[],
        assumptions=["N, P2O5 and K2O accounting uses label mass percentages; P/K are not elemental amounts.",
            "Targets are caller-supplied remaining mineral-fertilizer application amounts after independently evaluated credits.",
            "Product quantities are totals for the entire stated area and planning period; no actual application is recorded."],
        limitations=["No soil-test interpretation, crop requirement estimation, availability/recovery, mineralization or nutrient-loss model.",
            "No automatic nutrient credits, organic amendments, elemental P/K conversion, blending compatibility or application schedule.",
            "Matching a declared target is not an agronomic recommendation or proof of safe/feasible application; targets/sources are not verified.",
            "No field validation, salinity, environmental/legal limits, economics or yield response prediction."])
