"""Versioned JSON for exact coupled-water inputs and continuation states.

No file or database is read or written here. Fractions are encoded as integer
pairs, and loading reconstructs only an explicit set of validated model records.
"""

import json
from dataclasses import fields
from datetime import date, datetime
from fractions import Fraction

from . import (
    soil_drainage,
    soil_evaporation,
    soil_infiltration,
    soil_replay,
    soil_roots,
    soil_storage,
    soil_water,
)
from .water import ReferenceETDay

SCHEMA_VERSION = "soil-snapshot-1"
MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
MODEL_VERSIONS = {module.__name__.rsplit(".", 1)[-1]: module.MODEL_VERSION for module in (
    soil_drainage, soil_evaporation, soil_infiltration, soil_replay, soil_roots, soil_storage)}
MODEL_VERSIONS["soil_water"] = soil_water.COUPLED_MODEL_VERSION
RECORDS = {cls.__name__: cls for cls in (
    soil_storage.SoilCompartment, soil_storage.SoilPatch, soil_storage.SoilStorage,
    soil_drainage.DrainageParameters, soil_infiltration.SurfaceWaterState, soil_infiltration.SurfaceWaterSupply,
    soil_evaporation.SoilEvaporationParameters, soil_evaporation.SoilEvaporationMemory,
    soil_evaporation.PatchEvaporationCoefficients, soil_roots.RootUptakeParameters,
    soil_roots.RootLayerActivity, soil_roots.CropTranspirationCoefficients,
    ReferenceETDay, soil_replay.SoilWaterState, soil_replay.SoilWaterDay)}
KINDS = {"state": soil_replay.SoilWaterState, "day": soil_replay.SoilWaterDay}


def _encode(value):
    if type(value) in (str, int, float, bool, type(None)):
        return value
    if type(value) is Fraction:
        return {"fraction": [value.numerator, value.denominator]}
    if type(value) is datetime:
        return {"datetime": value.isoformat()}
    if type(value) is date:
        return {"date": value.isoformat()}
    if type(value) is tuple:
        return {"tuple": [_encode(item) for item in value]}
    if RECORDS.get(type(value).__name__) is type(value):
        return {"record": type(value).__name__, "fields": {f.name: _encode(getattr(value, f.name)) for f in fields(value)}}
    raise ValueError("snapshot contains an unsupported value type")


def _decode(value, depth=0):
    if depth > 64:
        raise ValueError("snapshot nesting is too deep")
    if type(value) in (str, int, float, bool, type(None)):
        return value
    if not isinstance(value, dict):
        raise ValueError("snapshot values require explicit type tags")
    if set(value) == {"fraction"}:
        pair = value["fraction"]
        if not isinstance(pair, list) or len(pair) != 2 or any(type(v) is not int for v in pair) or pair[1] <= 0:
            raise ValueError("fraction requires integer numerator and positive denominator")
        return Fraction(*pair)
    if set(value) in ({"date"}, {"datetime"}):
        key = next(iter(value))
        if not isinstance(value[key], str):
            raise ValueError("snapshot date must be ISO text")
        return (date if key == "date" else datetime).fromisoformat(value[key])
    if set(value) == {"tuple"}:
        if not isinstance(value["tuple"], list):
            raise ValueError("tuple tag requires an array")
        return tuple(_decode(item, depth+1) for item in value["tuple"])
    if set(value) == {"record", "fields"}:
        name, supplied = value["record"], value["fields"]
        if not isinstance(name, str) or name not in RECORDS:
            raise ValueError("unknown snapshot record")
        cls = RECORDS[name]
        if not isinstance(supplied, dict) or set(supplied) != {f.name for f in fields(cls)}:
            raise ValueError("snapshot record fields must exactly match the versioned schema")
        return cls(**{key: _decode(item, depth+1) for key, item in supplied.items()})
    raise ValueError("unknown snapshot type tag")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError(f"non-finite JSON number is not supported: {value}")


def dumps_soil_snapshot(value):
    """Produce an exact, deterministic snapshot for one state or daily input."""
    kind = next((name for name, cls in KINDS.items() if type(value) is cls), None)
    if kind is None:
        raise ValueError("only validated SoilWaterState or SoilWaterDay can be snapshotted")
    result = json.dumps(dict(schema_version=SCHEMA_VERSION, model_versions=MODEL_VERSIONS,
                             kind=kind, value=_encode(value)), ensure_ascii=False, allow_nan=False, sort_keys=True)
    if len(result.encode("utf-8")) > MAX_SNAPSHOT_BYTES:
        raise ValueError("snapshot exceeds the 8 MiB limit")
    return result


def loads_soil_snapshot(payload, *, expected_kind):
    """Validate schema, model compatibility and physical records before loading.

    A syntactically valid file is not evidence that its observations or parameters
    are accurate. Ownership, provenance review and adoption belong to the service.
    """
    if not isinstance(expected_kind, str) or expected_kind not in KINDS:
        raise ValueError("expected snapshot kind must be state or day")
    if not isinstance(payload, str) or len(payload.encode("utf-8")) > MAX_SNAPSHOT_BYTES:
        raise ValueError("snapshot must be JSON text within the 8 MiB limit")
    try:
        envelope = json.loads(payload, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
        if not isinstance(envelope, dict) or set(envelope) != {"schema_version", "model_versions", "kind", "value"}:
            raise ValueError("snapshot envelope does not match the schema")
        if envelope["schema_version"] != SCHEMA_VERSION or envelope["model_versions"] != MODEL_VERSIONS:
            raise ValueError("snapshot schema or model version is incompatible; explicit migration is required")
        if envelope["kind"] != expected_kind:
            raise ValueError("snapshot kind does not match the requested input")
        result = _decode(envelope["value"])
        if type(result) is not KINDS[expected_kind]:
            raise ValueError("snapshot root record does not match its kind")
        return result
    except (TypeError, OverflowError, RecursionError) as exc:
        raise ValueError("malformed soil snapshot") from exc
