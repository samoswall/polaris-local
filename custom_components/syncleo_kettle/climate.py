"""Climate platform for Syncleo heater-class devices (RusClimate convectors)."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .coordinator import PolarisDataUpdateCoordinator
from .const import (
    DOMAIN,
    POLARIS_HEATER_TYPE,
    HEATER_MIN_TEMP,
    HEATER_MAX_TEMP,
    HEATER_TEMP_STEP,
    HEATER_PRESET_COMFORT,
    HEATER_PRESET_TO_MODE,
    HEATER_MODE_TO_PRESET,
    HEATER_FAN_AUTO,
    HEATER_FAN_MODES,
    HEATER_MAX_INTENSITY,
)
from .protocol import PowerType

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigType,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the climate platform. Only for heater-class devices."""
    coordinator: PolarisDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    if coordinator.device_info is None:
        _LOGGER.error("Device info not available, cannot create climate entity")
        return

    if coordinator.device_info["model_id"] in POLARIS_HEATER_TYPE:
        async_add_entities([SyncleoHeaterClimate(coordinator, config_entry.entry_id)])


class SyncleoHeaterClimate(ClimateEntity):
    """Representation of a Syncleo/RusClimate convector heater."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_translation_key = "heater"

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
    _attr_preset_modes = list(HEATER_PRESET_TO_MODE.keys())
    _attr_fan_modes = HEATER_FAN_MODES
    _attr_min_temp = HEATER_MIN_TEMP
    _attr_max_temp = HEATER_MAX_TEMP
    _attr_target_temperature_step = HEATER_TEMP_STEP
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(self, coordinator: PolarisDataUpdateCoordinator, entry_id: str) -> None:
        """Initialize the heater climate device."""
        self.coordinator = coordinator
        self._entry_id = entry_id
        self._attr_unique_id = f"{coordinator._mac}_climate"
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.data.get("connected", False)

    @property
    def current_temperature(self) -> float | None:
        """Return the current temperature."""
        return self.coordinator.data.get("current_temperature")

    @property
    def target_temperature(self) -> float | None:
        """Return the temperature we try to reach."""
        return self.coordinator.data.get("target_temperature")

    @property
    def hvac_mode(self) -> HVACMode:
        """Return current operation mode."""
        if self.coordinator.data.get("power_type", PowerType.OFF) == PowerType.OFF:
            return HVACMode.OFF
        return HVACMode.HEAT

    @property
    def hvac_action(self) -> HVACAction:
        """Return current HVAC action."""
        if self.hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        if self.coordinator.data.get("is_heating", False):
            return HVACAction.HEATING
        return HVACAction.IDLE

    @property
    def preset_mode(self) -> str | None:
        """Return the current preset (comfort/eco/away).

        None when off or when running a manual intensity level (the device then
        reports a non-preset "manual" mode).
        """
        power_type = self.coordinator.data.get("power_type", PowerType.OFF)
        return HEATER_MODE_TO_PRESET.get(power_type.value)

    @property
    def fan_mode(self) -> str | None:
        """Return the intensity as a fan mode: 'auto' or '1'..'10'."""
        intensity = self.coordinator.data.get("intensity", 0)
        if not intensity:
            return HEATER_FAN_AUTO
        return str(intensity)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature."""
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is not None:
            await self.coordinator.async_set_temperature(int(temperature))

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new operation mode."""
        if hvac_mode == HVACMode.OFF:
            await self.coordinator.async_set_power_type(PowerType.OFF)
        else:  # HVACMode.HEAT -> resume in Comfort unless already on
            if self.coordinator.data.get("power_type", PowerType.OFF) == PowerType.OFF:
                await self.coordinator.async_set_power_type(
                    PowerType(HEATER_PRESET_TO_MODE[HEATER_PRESET_COMFORT])
                )

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set a preset (also powers the heater on; resets intensity to Auto)."""
        mode_value = HEATER_PRESET_TO_MODE.get(preset_mode)
        if mode_value is None:
            _LOGGER.warning("Unknown heater preset: %s", preset_mode)
            return
        await self.coordinator.async_set_power_type(PowerType(mode_value))

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set the intensity/power level. 'auto' or '1'..'10'.

        A fixed level makes the device switch to its manual mode.
        """
        if fan_mode == HEATER_FAN_AUTO:
            intensity = 0
        else:
            try:
                intensity = int(fan_mode)
            except ValueError:
                _LOGGER.warning("Unknown heater fan mode: %s", fan_mode)
                return
            intensity = max(1, min(HEATER_MAX_INTENSITY, intensity))
        await self.coordinator.async_set_intensity(intensity)

    async def async_turn_on(self) -> None:
        """Turn the heater on (Comfort)."""
        await self.coordinator.async_set_power_type(
            PowerType(HEATER_PRESET_TO_MODE[HEATER_PRESET_COMFORT])
        )

    async def async_turn_off(self) -> None:
        """Turn the heater off."""
        await self.coordinator.async_set_power_type(PowerType.OFF)

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )

    @property
    def should_poll(self) -> bool:
        """No need to poll, coordinator notifies of updates."""
        return False
