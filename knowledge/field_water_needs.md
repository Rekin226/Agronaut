# Understanding crop water needs

_Domain: soil_
_Evidence: guidance_
_Applicability: General soil-crop planning; no locally validated crop coefficients or irrigation prescription._
_Sources: [FAO-56 (1998), chapter 6](https://www.fao.org/4/x0490e/x0490e0b.htm); [FAO-56 (1998), chapter 8](https://www.fao.org/4/x0490e/x0490e0e.htm)_

## What the quantities mean

Reference evapotranspiration (ET0) describes atmospheric demand over a reference surface.
A crop coefficient (Kc) relates it to crop evapotranspiration under standard conditions:
ETc = Kc × ET0. This single coefficient includes both soil evaporation and plant
transpiration. Crop stage, climate and wetting affect its applicability; a crop name
alone does not select a locally suitable coefficient. See FAO-56 chapter 6.

Crop demand is not the amount to pump today. Rainfall, runoff, irrigation, drainage
and starting soil water affect the root-zone balance. Field capacity, wilting point,
effective root depth and the allowable depletion fraction describe available storage
and its depletion threshold. An initial soil-water estimate is needed to start that
balance. See FAO-56 chapter 8.

## Prepare an Agronaut calculation

Record the crop, planting date, observed stage, field area and time zone. Supply daily
grass-reference ET0 with its source and date; distinguish observations, forecasts,
historical data and scenarios. Preserve unknown rain as unknown. Supply the coefficient
and its stage, source and applicability explanation.

For a root-zone budget, add sourced soil properties, root depth, starting depletion
and dated water events. Agronaut keeps missing starting water state unknown. A demand
estimate alone cannot give a supported irrigation amount. Pumped volume, water reaching
the soil surface and water retained in the root zone are different records in Agronaut;
use the quantity required by the selected calculation.

## Next step and limits

Use `/fieldplan` for guided intake, or inspect `soil.crop_water` and
`soil.root_zone_budget` with `agronaut capabilities --system soil`.
Compare predicted water changes with separately recorded soil measurements before
relying on a plan. The software's checks do not establish local agronomic validity.
Do not copy the example crop parameters into a farm plan as measurements.

Original Agronaut guidance, source review 2026-10-20. Linked publications support the
concepts above; Agronaut workflow instructions describe this implementation.
