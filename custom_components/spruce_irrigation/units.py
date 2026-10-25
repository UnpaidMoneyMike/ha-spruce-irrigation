"""The units the integration shows values in. Spruce stores US units (gallons, gpm, inches, °F, mph); the option
picks metric (the default), US, or a unit per quantity. Values are converted here, so every entity follows the
option whatever unit system Home Assistant itself uses."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.const import (
    UnitOfPrecipitationDepth,
    UnitOfSpeed,
    UnitOfTemperature,
    UnitOfVolume,
    UnitOfVolumeFlowRate,
)
from homeassistant.util.unit_conversion import (
    DistanceConverter,
    SpeedConverter,
    TemperatureConverter,
    VolumeConverter,
    VolumeFlowRateConverter,
)

from .const import (
    CONF_UNIT_FLOW,
    CONF_UNIT_RAIN,
    CONF_UNIT_TEMPERATURE,
    CONF_UNIT_VOLUME,
    CONF_UNIT_WIND,
    CONF_UNITS,
    UNITS_CUSTOM,
    UNITS_US,
)

# quantity -> (option key, metric unit, US unit)
QUANTITIES = {
    "volume": (CONF_UNIT_VOLUME, UnitOfVolume.LITERS, UnitOfVolume.GALLONS),
    "flow": (CONF_UNIT_FLOW, UnitOfVolumeFlowRate.LITERS_PER_MINUTE, UnitOfVolumeFlowRate.GALLONS_PER_MINUTE),
    "rain": (CONF_UNIT_RAIN, UnitOfPrecipitationDepth.MILLIMETERS, UnitOfPrecipitationDepth.INCHES),
    "temperature": (CONF_UNIT_TEMPERATURE, UnitOfTemperature.CELSIUS, UnitOfTemperature.FAHRENHEIT),
    "wind": (CONF_UNIT_WIND, UnitOfSpeed.KILOMETERS_PER_HOUR, UnitOfSpeed.MILES_PER_HOUR),
}
# Spruce's own unit of each quantity
SPRUCE = {"volume": UnitOfVolume.GALLONS, "flow": UnitOfVolumeFlowRate.GALLONS_PER_MINUTE,
          "rain": UnitOfPrecipitationDepth.INCHES, "temperature": UnitOfTemperature.FAHRENHEIT,
          "wind": UnitOfSpeed.MILES_PER_HOUR}
CONVERTERS = {"volume": VolumeConverter, "flow": VolumeFlowRateConverter, "rain": DistanceConverter,
              "temperature": TemperatureConverter, "wind": SpeedConverter}


class Units:
    """The unit chosen for each quantity, and conversion from Spruce's units (or litres / L/min, which the
    integration already works in) to it."""

    def __init__(self, options: Mapping[str, Any]) -> None:
        system = options.get(CONF_UNITS)
        self.unit: dict[str, str] = {}
        for quantity, (key, metric, us) in QUANTITIES.items():
            if system == UNITS_CUSTOM and options.get(key) in (metric, us):
                self.unit[quantity] = options[key]
            else:
                self.unit[quantity] = us if system == UNITS_US else metric

    @property
    def metric_volume(self) -> bool:
        return self.unit["volume"] == UnitOfVolume.LITERS

    def from_spruce(self, quantity: str, value: float | None) -> float | None:
        """A value in Spruce's unit (gal, gpm, in, °F, mph) in the chosen unit."""
        if value is None:
            return None
        return CONVERTERS[quantity].convert(value, SPRUCE[quantity], self.unit[quantity])

    def to_spruce(self, quantity: str, value: float) -> float:
        """A value in the chosen unit in Spruce's unit, for saving."""
        return CONVERTERS[quantity].convert(value, self.unit[quantity], SPRUCE[quantity])

    def from_metric(self, quantity: str, value: float | None) -> float | None:
        """A value the integration holds in litres, L/min, mm or °C, in the chosen unit."""
        if value is None:
            return None
        return CONVERTERS[quantity].convert(value, QUANTITIES[quantity][1], self.unit[quantity])
