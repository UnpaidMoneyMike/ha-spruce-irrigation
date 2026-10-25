"""The forecast Spruce plans with (Apple WeatherKit at the controller's location), as a weather entity: the same
rain, temperature and wind the rain / freeze / wind skips are decided on."""
from __future__ import annotations

from typing import Any

from homeassistant.components.weather import (
    Forecast,
    WeatherEntity,
    WeatherEntityFeature,
)
from homeassistant.const import UnitOfLength, UnitOfPrecipitationDepth, UnitOfPressure, UnitOfSpeed, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import SpruceEntity

# WeatherKit condition codes -> Home Assistant conditions
CONDITIONS = {
    "Clear": "sunny", "MostlyClear": "sunny", "PartlyCloudy": "partlycloudy", "MostlyCloudy": "cloudy",
    "Cloudy": "cloudy", "Foggy": "fog", "Haze": "fog", "Smoky": "fog", "Breezy": "windy", "Windy": "windy",
    "Drizzle": "rainy", "Rain": "rainy", "SunShowers": "rainy", "ScatteredShowers": "rainy", "HeavyRain": "pouring",
    "IsolatedThunderstorms": "lightning-rainy", "ScatteredThunderstorms": "lightning-rainy",
    "StrongStorms": "lightning-rainy", "Thunderstorms": "lightning-rainy", "Hail": "hail",
    "Flurries": "snowy", "Snow": "snowy", "SunFlurries": "snowy", "HeavySnow": "snowy", "Blizzard": "snowy",
    "BlowingSnow": "snowy", "Sleet": "snowy-rainy", "FreezingDrizzle": "snowy-rainy", "FreezingRain": "snowy-rainy",
    "WintryMix": "snowy-rainy", "Frigid": "exceptional", "Hot": "exceptional", "BlowingDust": "exceptional",
    "Hurricane": "exceptional", "TropicalStorm": "exceptional",
}


def _condition(item: dict) -> str | None:
    condition = CONDITIONS.get(item.get("conditionCode"))
    if condition == "sunny" and item.get("daylight") is False:
        return "clear-night"
    return condition


def _pct(v: Any) -> float | None:
    return round(v * 100) if isinstance(v, (int, float)) else None


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([SpruceWeather(entry)])


class SpruceWeather(SpruceEntity, WeatherEntity):
    _attr_translation_key = "forecast"
    _attr_attribution = "Weather by Apple WeatherKit, through Spruce"
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_native_precipitation_unit = UnitOfPrecipitationDepth.MILLIMETERS
    _attr_native_wind_speed_unit = UnitOfSpeed.KILOMETERS_PER_HOUR
    _attr_native_pressure_unit = UnitOfPressure.HPA
    _attr_native_visibility_unit = UnitOfLength.KILOMETERS
    _attr_supported_features = WeatherEntityFeature.FORECAST_DAILY | WeatherEntityFeature.FORECAST_HOURLY

    def __init__(self, entry) -> None:
        super().__init__(entry, entry.runtime_data.history, "forecast")

    @property
    def _now(self) -> dict:
        hourly = self.history.forecast.get("hourly") or []
        return hourly[0] if hourly else {}

    @property
    def available(self) -> bool:
        return super().available and bool(self.history.forecast)

    @property
    def condition(self) -> str | None:
        return _condition(self._now)

    @property
    def native_temperature(self) -> float | None:
        return self._now.get("temperature")

    @property
    def native_apparent_temperature(self) -> float | None:
        return self._now.get("temperatureApparent")

    @property
    def native_dew_point(self) -> float | None:
        return self._now.get("temperatureDewPoint")

    @property
    def humidity(self) -> float | None:
        return _pct(self._now.get("humidity"))

    @property
    def cloud_coverage(self) -> float | None:
        return _pct(self._now.get("cloudCover"))

    @property
    def native_pressure(self) -> float | None:
        return self._now.get("pressure")

    @property
    def native_wind_speed(self) -> float | None:
        return self._now.get("windSpeed")

    @property
    def native_wind_gust_speed(self) -> float | None:
        return self._now.get("windGust")

    @property
    def wind_bearing(self) -> float | None:
        return self._now.get("windDirection")

    @property
    def uv_index(self) -> float | None:
        return self._now.get("uvIndex")

    @property
    def native_visibility(self) -> float | None:
        v = self._now.get("visibility")
        return round(v / 1000, 1) if isinstance(v, (int, float)) else None

    async def async_forecast_daily(self) -> list[Forecast] | None:
        return [Forecast(
            datetime=d["forecastStart"], condition=CONDITIONS.get(d.get("conditionCode")),
            native_temperature=d.get("temperatureMax"), native_templow=d.get("temperatureMin"),
            native_precipitation=d.get("precipitationAmount"), precipitation_probability=_pct(d.get("precipitationChance")),
            humidity=_pct(d.get("humidity")), native_wind_speed=d.get("windSpeedMax", d.get("windSpeed")),
            native_wind_gust_speed=d.get("windGustSpeedMax"), wind_bearing=d.get("windDirection"),
        ) for d in self.history.forecast.get("daily") or [] if d.get("forecastStart")]

    async def async_forecast_hourly(self) -> list[Forecast] | None:
        return [Forecast(
            datetime=h["forecastStart"], condition=_condition(h), native_temperature=h.get("temperature"),
            native_apparent_temperature=h.get("temperatureApparent"), native_precipitation=h.get("precipitationAmount"),
            precipitation_probability=_pct(h.get("precipitationChance")), humidity=_pct(h.get("humidity")),
            cloud_coverage=_pct(h.get("cloudCover")), native_pressure=h.get("pressure"),
            native_wind_speed=h.get("windSpeed"), native_wind_gust_speed=h.get("windGust"),
            wind_bearing=h.get("windDirection"), uv_index=h.get("uvIndex"),
        ) for h in self.history.forecast.get("hourly") or [] if h.get("forecastStart")]

    @callback
    def _handle_coordinator_update(self) -> None:
        super()._handle_coordinator_update()
        self.hass.async_create_task(self.async_update_listeners(None))
