"""Explicit FAO-56 (1998) daily grass-reference net-radiation estimation."""

from dataclasses import asdict
from datetime import date
from math import acos, cos, pi, radians, sin, sqrt, tan

from .coefficients import FAO_METEOROLOGY, Constant
from .validation import number

MODEL_VERSION = 'daily-net-radiation-0.1.0-fao56-1998'
CONSTANTS = {
    'solar_constant': Constant(0.0820, 'MJ/m2/min', '21', FAO_METEOROLOGY),
    'orbital_days': Constant(365, 'day', '23,24', FAO_METEOROLOGY),
    'distance_amplitude': Constant(0.033, '1', '23', FAO_METEOROLOGY),
    'declination_amplitude': Constant(0.409, 'radian', '24', FAO_METEOROLOGY),
    'declination_phase': Constant(1.39, 'radian', '24', FAO_METEOROLOGY),
    'clear_sky_base': Constant(0.75, '1', '37', FAO_METEOROLOGY),
    'clear_sky_elevation': Constant(0.00002, '1/m', '37', FAO_METEOROLOGY),
    'grass_albedo': Constant(0.23, '1', '38', FAO_METEOROLOGY),
    'stefan_boltzmann': Constant(4.903e-9, 'MJ/K4/m2/day', '39', FAO_METEOROLOGY),
    'kelvin_offset': Constant(273.16, 'K', '39', FAO_METEOROLOGY),
    'humidity_base': Constant(0.34, '1', '39', FAO_METEOROLOGY),
    'humidity_coefficient': Constant(0.14, '1/sqrt(kPa)', '39', FAO_METEOROLOGY),
    'cloud_multiplier': Constant(1.35, '1', '39', FAO_METEOROLOGY),
    'cloud_offset': Constant(0.35, '1', '39', FAO_METEOROLOGY),
}


def estimate_net_radiation(*, day, latitude_deg, elevation_m, t_min_c, t_max_c,
                           actual_vapour_pressure_kpa, solar_radiation_mj_m2):
    """Return the full estimation trace; never substitute solar directly for net.

    This bounded implementation declines polar darkness and negative empirical
    correction factors, rather than adding an undocumented lower-factor clamp.
    """
    if type(day) is not date:
        raise ValueError('radiation day must be a calendar date')
    number('latitude_deg', latitude_deg, -90, 90)
    number('elevation_m', elevation_m, -500, 9000)
    number('t_min_c', t_min_c, -80, 60)
    number('t_max_c', t_max_c, -80, 60)
    if t_min_c > t_max_c:
        raise ValueError('t_min_c must not exceed t_max_c')
    number('actual_vapour_pressure_kpa', actual_vapour_pressure_kpa, 0)
    number('solar_radiation_mj_m2', solar_radiation_mj_m2, 0, 60)
    c = {key: item.value for key, item in CONSTANTS.items()}
    j = day.timetuple().tm_yday
    phase = 2*pi*j/c['orbital_days']
    dr = 1+c['distance_amplitude']*cos(phase)
    declination = c['declination_amplitude']*sin(phase-c['declination_phase'])
    latitude = radians(latitude_deg)
    sunset = acos(max(-1, min(1, -tan(latitude)*tan(declination))))
    ra = 1440/pi*c['solar_constant']*dr*(sunset*sin(latitude)*sin(declination)
                                        + cos(latitude)*cos(declination)*sin(sunset))
    clear_sky = (c['clear_sky_base']+c['clear_sky_elevation']*elevation_m)*ra
    if clear_sky <= 1e-9:
        raise ValueError('solar radiation estimate unavailable during polar darkness; supply net radiation')
    ratio = solar_radiation_mj_m2/clear_sky
    used_ratio = min(ratio, 1.0)
    humidity_factor = c['humidity_base']-c['humidity_coefficient']*sqrt(actual_vapour_pressure_kpa)
    cloud_factor = c['cloud_multiplier']*used_ratio-c['cloud_offset']
    if humidity_factor < 0 or cloud_factor < 0:
        raise ValueError('solar radiation estimate is outside the nonnegative correction-factor '
                         'envelope of this implementation; supply net radiation')
    shortwave = (1-c['grass_albedo'])*solar_radiation_mj_m2
    longwave = c['stefan_boltzmann']*((t_max_c+c['kelvin_offset'])**4
        + (t_min_c+c['kelvin_offset'])**4)/2*humidity_factor*cloud_factor
    net = shortwave-longwave
    number('estimated net radiation', net, -50, 60)
    return dict(model_version=MODEL_VERSION, source=FAO_METEOROLOGY,
        inputs=dict(day=day.isoformat(), latitude_deg=latitude_deg, elevation_m=elevation_m,
            t_min_c=t_min_c, t_max_c=t_max_c, actual_vapour_pressure_kpa=actual_vapour_pressure_kpa,
            solar_radiation_mj_m2=solar_radiation_mj_m2),
        values=dict(day_of_year=j, extraterrestrial_mj_m2=ra, clear_sky_mj_m2=clear_sky,
            solar_clear_sky_ratio=ratio, longwave_ratio_used=used_ratio,
            humidity_factor=humidity_factor, cloud_factor=cloud_factor,
            net_shortwave_mj_m2=shortwave, net_outgoing_longwave_mj_m2=longwave,
            net_radiation_mj_m2=net),
        ratio_capped=ratio > 1,
        coefficients={key: asdict(value) for key, value in CONSTANTS.items()},
        assumptions=['FAO-56 daily grass reference: albedo 0.23; clear-sky radiation estimated from latitude, date and elevation.',
            'The solar/clear-sky ratio is capped at 1 only in the longwave correction; supplied solar energy is unchanged.',
            'Calendar day-of-year is used with the FAO-56 365-day orbital denominator, including leap years.'],
        limitations=['Net radiation is estimated, not a measured flux; radiation coefficients require local evaluation.',
            'Daily temperature and humidity extremes are supplied; no conversion from interval means is inferred.',
            'Polar darkness and negative empirical humidity/cloud correction factors are outside this implementation.'])
