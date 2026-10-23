"""Guided, explicitly supplied daily meteorology for the field-planning draft."""

from datetime import timedelta

from agro_model.validation import number
from agro_model.water import reference_et

from .field_io import _date, parse_weather_records

WEATHER_CHOICE = (
    "Choose manual for supplied daily ET0/rainfall, meteorology for daily station readings, "
    "or open_meteo to fetch model estimates. Open-Meteo supports windows of 1–16 days, "
    "within the last 90 days through 14 days ahead.")
SETUP = (
    ('radiation_basis', 'Choose net for supplied daily net radiation, or solar to estimate net radiation from daily incoming solar energy using FAO-56.', 'meteorology'),
    ('elevation_m', 'What is the site elevation above sea level, in metres (−500 to 9000)?', 'meteorology'),
    ('wind_height_m', 'At what height above ground was wind speed measured, in metres (0.5 to 100)?', 'meteorology'),
)
SOURCE_QUESTION = ('What is the source of the daily temperature, humidity, wind and radiation values, '
    'site information, and any rainfall? Supply daily extremes; interval averages do not establish them.')
DAILY = (
    ('t_min_c', '{day}: what is the minimum air temperature over the full day, in °C?', -80, 60),
    ('t_max_c', '{day}: what is the maximum air temperature over the full day, in °C?', -80, 60),
    ('rh_min_pct', '{day}: what is the minimum relative humidity over the full day, in percent?', 0, 100),
    ('rh_max_pct', '{day}: what is the maximum relative humidity over the full day, in percent?', 0, 100),
    ('wind_m_s', '{day}: what is the mean wind speed over the full day, in m/s, at the stated measurement height?', 0, 100),
)
RADIATION = {
    'net': ('net_radiation_mj_m2', '{day}: what is total daily net radiation in MJ/m²? This is not incoming solar energy.', -50, 60),
    'solar': ('solar_radiation_mj_m2', '{day}: what is total daily incoming solar energy in MJ/m²? This is a full-day energy total, not an instantaneous W/m² reading.', 0, 60),
}


def active(draft):
    return draft['data'].get('weather_input') == 'meteorology'


def setup_steps(draft):
    steps = list(SETUP)
    if draft['data'].get('radiation_basis') == 'solar':
        steps.append(('latitude', "What is the field's WGS84 latitude in decimal degrees (−90 to 90)?", 'latitude'))
    return steps + [('weather_source', SOURCE_QUESTION, 'text')]


def daily_steps(draft, index):
    day = (_date(draft['data']['start'])+timedelta(days=index)).isoformat()
    radiation = RADIATION[draft['data'].get('radiation_basis', 'net')]
    return [(f'{key}_{index}', question.format(day=day), 'meteorology')
            for key, question, _, _ in (*DAILY, radiation)]


def weather_record(draft, index, data=None):
    data = draft['data'] if data is None else data
    row = dict(day=(_date(data['start'])+timedelta(days=index)).isoformat(),
        kind=data['weather_kind'], source=data['weather_source'], elevation_m=data['elevation_m'],
        wind_height_m=data['wind_height_m'], rainfall_mm=data.get(f'rain_{index}'),
        **{key: data[f'{key}_{index}'] for key, _, _, _ in DAILY})
    key = RADIATION[data['radiation_basis']][0]
    row[key] = data[f'{key}_{index}']
    if data['radiation_basis'] == 'solar':
        row.update(input_type='solar_radiation_fao56', latitude_deg=data['latitude'])
    if data['weather_kind'] == 'forecast':
        row['issued_at'] = data['issued_at']
    return row


def answer(draft, key, supplied):
    data = draft['data']
    if key == 'radiation_basis':
        value = supplied.lower()
        if value not in RADIATION:
            raise ValueError('Choose net or solar.')
    else:
        try:
            value = float(supplied)
        except ValueError as exc:
            raise ValueError('Supply a number in the stated meteorological units.') from exc
        if key in ('elevation_m', 'wind_height_m'):
            low, high = {'elevation_m': (-500, 9000), 'wind_height_m': (0.5, 100)}[key]
            number(key, value, low, high)
        else:
            base, index_text = key.rsplit('_', 1)
            bounds = {name: (low, high) for name, _, low, high in (*DAILY, *RADIATION.values())}
            number(key, value, *bounds[base])
            if base == 't_max_c' and value < data[f't_min_c_{index_text}']:
                raise ValueError('Maximum temperature must not be below minimum temperature.')
            if base == 'rh_max_pct' and value < data[f'rh_min_pct_{index_text}']:
                raise ValueError('Maximum humidity must not be below minimum humidity.')
            if base in ('net_radiation_mj_m2', 'solar_radiation_mj_m2'):
                # Fail on this answer before advancing. Do not strand the grower at
                # the final rainfall question with a much earlier radiation error.
                row = weather_record(draft, int(index_text), dict(data, **{key: value}))
                reference_et(parse_weather_records([row])[0])
    data[key] = value
