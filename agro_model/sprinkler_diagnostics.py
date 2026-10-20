"""Hydraulic residual statistics; no fitting or acceptance thresholds."""

from math import fsum, sqrt

from .hydraulics import GRAVITY_M_S2
from .validation import number


def predicted_pressure(head_m, density_kg_m3):
    number('predicted pressure head', head_m, 0, 10000)
    if density_kg_m3 is None:
        return None
    number('water_density_kg_m3', density_kg_m3, 500, 2000)
    return head_m*density_kg_m3*GRAVITY_M_S2/1000


def summarize(errors):
    values=list(errors)
    if len(values)>20000:
        raise ValueError('at most 20000 paired readings per report')
    for value in values:
        number('hydraulic error',value,-1e9,1e9)
    n=len(values)
    return dict(count=n,bias=fsum(values)/n if n else None,mae=fsum(abs(v) for v in values)/n if n else None,
                rmse=sqrt(fsum(v*v for v in values)/n) if n else None,
                maximum_absolute_error=max(map(abs,values)) if n else None)


def summarize_events(events):
    groups=[summarize(values) for values in events]
    known=[group for group in groups if group['count']]
    n=len(known)
    return dict(event_count=n,paired_reading_count=sum(g['count'] for g in known),events_without_pairs=len(groups)-n,
                bias=fsum(g['bias'] for g in known)/n if n else None,mae=fsum(g['mae'] for g in known)/n if n else None,
                rmse=sqrt(fsum(g['rmse']**2 for g in known)/n) if n else None,
                maximum_absolute_error=max(g['maximum_absolute_error'] for g in known) if n else None)
