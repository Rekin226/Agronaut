"""Display saved-calculation chronology without inferring prediction independence."""

from datetime import datetime, timezone

STATUSES = ('saved_before_event', 'saved_during_event', 'saved_at_or_after_event_end', 'unknown')
LIMITATION = ('Save-time chronology uses the application timestamp and supplied event window. '
    'It does not establish when inputs became available, when an imported prediction was first made, '
    'clock accuracy, calibration independence or prospective validation. Timing does not change diagnostic metric eligibility.')
SOIL_TIMING_LIMITATION = ('Save-time chronology compares the saved calculation snapshot with the original observation time '
    'and the adjusted reference boundary separately. Saving before closing midnight can still occur after the measurement. '
    'Timestamps do not establish input availability, clock accuracy, when an imported prediction was first made, '
    'calibration independence or prospective validation. Timing does not change diagnostic metric eligibility.')


def _utc(value):
    try:
        stamp = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            return None
        return stamp.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def prediction_event_timing(saved_at, started_at, ended_at):
    """Equality at event start is during; equality at event end is after/end."""
    saved, start, end = map(_utc, (saved_at, started_at, ended_at))
    reason = None
    if start is None or end is None or end <= start:
        status, reason = 'unknown', 'missing_or_invalid_event_window'
    elif saved is None:
        status, reason = 'unknown', 'missing_or_invalid_calculation_timestamp'
    elif saved < start:
        status = 'saved_before_event'
    elif saved < end:
        status = 'saved_during_event'
    else:
        status = 'saved_at_or_after_event_end'
    return dict(status=status, reason=reason, calculation_saved_at_utc=saved.isoformat() if saved else None,
        event_start_utc=start.isoformat() if start else None, event_end_utc=end.isoformat() if end else None,
        prospective_validation_status='not_established')


def timing_counts(rows):
    """Count all selected events, including those excluded from numerical metrics."""
    counts = dict.fromkeys(STATUSES, 0)
    for row in rows:
        counts[row['prediction_timing']['status']] += 1
    return counts


def render_timing(report):
    counts = report.get('timing_counts')
    if counts is None:
        return 'Calculation save timing: not recorded in this older report.'
    return ('Calculation save timing across all selected events: '
        f"before start {counts['saved_before_event']}; during {counts['saved_during_event']}; "
        f"at/after end {counts['saved_at_or_after_event_end']}; unknown {counts['unknown']}. "
        'Earlier saving alone does not establish prospective validation.')


def prediction_observation_timing(saved_at, observed_at, reference_at):
    """Retain measurement time separately from its adjusted end-of-day reference."""
    saved, observed, reference = map(_utc, (saved_at, observed_at, reference_at))
    def order(target):
        if saved is None or target is None:
            return 'unknown'
        return 'before' if saved < target else 'at_or_after'
    return dict(calculation_saved_at_utc=saved.isoformat() if saved else None,
        observed_at_utc=observed.isoformat() if observed else None,
        reference_at_utc=reference.isoformat() if reference else None,
        relative_to_observation=order(observed), relative_to_reference_boundary=order(reference),
        prospective_validation_status='not_established')


def observation_timing_counts(rows):
    counts = {key: dict(before=0, at_or_after=0, unknown=0)
              for key in ('relative_to_observation', 'relative_to_reference_boundary')}
    for row in rows:
        for key in counts:
            counts[key][row['observation_timing'][key]] += 1
    return counts


def render_observation_timing(report):
    counts = report.get('observation_timing_counts')
    if counts is None:
        return 'Calculation save timing: not recorded in this older report.'
    parts = []
    for key, label in (('relative_to_observation', 'measurement'), ('relative_to_reference_boundary', 'reference boundary')):
        values = counts[key]
        parts.append(f"{label}: before {values['before']}, at/after {values['at_or_after']}, unknown {values['unknown']}")
    return ('Calculation save timing across all selected readings — '+'; '.join(parts)+'. '
        'Earlier saving alone does not establish prospective validation.')
