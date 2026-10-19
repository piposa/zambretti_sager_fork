"""Weather platform for the Zambretti & Sager integration."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.components.weather import (
    Forecast,
    WeatherEntity,
    WeatherEntityFeature,
)
from homeassistant.const import UnitOfPressure, UnitOfSpeed, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .algorithms import (
    ZAMBRETTI_MAPPING,
    calculate_precipitation_probability,
    calculate_sager_forecast,
    calculate_zambretti_index,
)
from .const import DOMAIN, VERSION
from .coordinator import ZambrettiConfigEntry, ZambrettiSagerCoordinator

ZAMBRETTI_TO_CONDITION: dict[str, str] = {
    "settled_fine": "sunny",
    "fine_weather": "sunny",
    "fine_becoming_less_settled": "partlycloudy",
    "fairly_fine_showery_later": "rainy",
    "showery_becoming_more_unsettled": "rainy",
    "unsettled_rain_later": "rainy",
    "rain_at_times_worse_later": "rainy",
    "rain_at_times_becoming_very_unsettled": "rainy",
    "very_unsettled_rain": "rainy",
    "fine_possibly_showers": "partlycloudy",
    "fairly_fine_showers_likely": "rainy",
    "showery_bright_intervals": "cloudy",
    "changeable_some_rain": "rainy",
    "unsettled_rain_at_times": "rainy",
    "rain_at_frequent_intervals": "rainy",
    "stormy_much_rain": "lightning-rainy",
    "becoming_fine": "sunny",
    "fairly_fine_improving": "partlycloudy",
    "fairly_fine_possibly_showers_early": "partlycloudy",
    "showery_early_improving": "cloudy",
    "changeable_mending": "cloudy",
    "rather_unsettled_clearing_later": "cloudy",
    "unsettled_probably_improving": "cloudy",
    "unsettled_short_fine_intervals": "partlycloudy",
    "very_unsettled_finer_at_times": "rainy",
    "stormy_possibly_improving": "lightning-rainy",
    "stable": "clear-night",
    "sager_fair_improving": "sunny",
    "sager_fair_tending_to_deteriorate": "partlycloudy",
    "sager_fair_no_change": "sunny",
    "sager_unsettled_rain_likely": "rainy",
    "sager_unsettled_probably_improving": "cloudy",
    "sager_unsettled_rain_at_times": "rainy",
    "sager_changeable_becoming_fairer": "partlycloudy",
    "sager_changeable_becoming_more_unsettled": "cloudy",
    "sager_variable_slowly_improving": "partlycloudy",
    "sager_variable_slowly_deteriorating": "cloudy",
    "sager_variable_some_change": "cloudy",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZambrettiConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Zambretti & Sager weather entity."""
    coordinator = entry.runtime_data
    async_add_entities([ZambrettiWeather(coordinator)])


class ZambrettiWeather(CoordinatorEntity, WeatherEntity):
    """Weather entity that exposes Zambretti forecasts to HA's native weather card."""

    _attr_name = "Weather Station"
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_native_pressure_unit = UnitOfPressure.HPA
    _attr_native_wind_speed_unit = UnitOfSpeed.METERS_PER_SECOND
    _attr_supported_features = (
        WeatherEntityFeature.FORECAST_HOURLY | WeatherEntityFeature.FORECAST_DAILY
    )
    _attr_has_entity_name = True

    def __init__(self, coordinator: ZambrettiSagerCoordinator) -> None:
        """Initialize the weather entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_weather"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name="Weather Station",
            manufacturer="Zambretti & Sager",
            model="Software Forecaster",
            sw_version=VERSION,
        )

    @property
    def data(self):
        return self.coordinator.data

    @property
    def condition(self) -> str | None:
        d = self.data
        if not d or not d.available:
            return None
        if d.is_raining is True:
            return "rainy"
        state = d.sager_state if d.wind_degrees is not None else d.zambretti_state
        condition = ZAMBRETTI_TO_CONDITION.get(state) if state else None
        return "clear-night" if d.is_night and condition == "sunny" else condition

    @property
    def native_temperature(self) -> float | None:
        d = self.data
        return d.temperature if d else None

    @property
    def native_pressure(self) -> float | None:
        d = self.data
        if not d or d.p_now is None:
            return None
        return d.p_now

    @property
    def native_humidity(self) -> float | None:
        d = self.data
        return d.humidity if d else None

    @property
    def native_wind_speed(self) -> float | None:
        d = self.data
        return d.wind_speed if d else None

    @property
    def wind_bearing(self) -> float | None:
        d = self.data
        return d.wind_degrees if d else None

    async def async_forecast_hourly(self) -> list[Forecast] | None:
        """Return forecasts for each of the next 24 hours."""
        if not self.data or not self.data.available or self.data.p_now is None:
            return None
        return [self._build_forecast(hours) for hours in range(1, 25)]

    async def async_forecast_daily(self) -> list[Forecast] | None:
        """Return forecasts for each of the next five days."""
        if not self.data or not self.data.available or self.data.p_now is None:
            return None
        return [self._build_forecast(days * 24) for days in range(1, 6)]

    @property
    def forecast(self) -> list[Forecast] | None:
        """Return the integration's native 6, 12, and 24 hour forecast points."""
        d = self.data
        if not d or not d.available or d.p_now is None:
            return None
        return [self._build_forecast(hours) for hours in (6, 12, 24)]

    def _build_forecast(self, target_hours: int) -> Forecast:
        """Build one forecast by extrapolating the best available pressure trend."""
        d = self.data

        if target_hours <= 6:
            reference_pressure, reference_hours = d.p_3h, 3
        elif target_hours <= 12:
            reference_pressure, reference_hours = d.p_6h, 6
        else:
            reference_pressure, reference_hours = d.p_12h, 12

        if reference_pressure is None:
            reference_pressure, reference_hours = d.p_now, 1

        hourly_rate = (d.p_now - reference_pressure) / reference_hours
        trend_delta_3h = hourly_rate * 3
        predicted_pressure = round(d.p_now + hourly_rate * target_hours, 1)

        if d.wind_degrees is not None:
            state = calculate_sager_forecast(
                predicted_pressure, trend_delta_3h, d.wind_degrees
            )
        else:
            state = ZAMBRETTI_MAPPING.get(
                calculate_zambretti_index(predicted_pressure, trend_delta_3h), "stable"
            )

        probability = calculate_precipitation_probability(
            predicted_pressure, trend_delta_3h, d.humidity
        )
        if d.is_raining is True or (d.rain_amount is not None and d.rain_amount > 0):
            probability = max(probability, 90)

        return Forecast(
            datetime=(dt_util.utcnow() + timedelta(hours=target_hours)).isoformat(),
            condition=ZAMBRETTI_TO_CONDITION.get(state),
            native_temperature=d.temperature,
            native_temperature_unit=self._attr_native_temperature_unit,
            native_pressure=predicted_pressure,
            native_pressure_unit=self._attr_native_pressure_unit,
            native_wind_speed=d.wind_speed,
            native_wind_speed_unit=self._attr_native_wind_speed_unit,
            wind_bearing=d.wind_degrees,
            humidity=d.humidity,
            precipitation_probability=probability,
            native_precipitation=(
                d.rain_amount
                if d.rain_amount is not None and d.rain_amount > 0
                else _estimated_precipitation(probability)
            ),
        )


def _estimated_precipitation(probability: int | None) -> float:
    """Estimate precipitation amount when no real rain measurement is available."""
    if probability is None or probability < 30:
        return 0.0
    if probability >= 90:
        return 5.0
    if probability >= 70:
        return 2.5
    if probability >= 50:
        return 1.0
    return 0.2
