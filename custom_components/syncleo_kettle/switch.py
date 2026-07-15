"""Switch platform for Syncleo Kettle."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .coordinator import PolarisDataUpdateCoordinator
from .const import DOMAIN, POLARIS_KETTLE_WITH_BACKLIGHT_TYPE, POLARIS_HEATER_TYPE

_LOGGER = logging.getLogger(__name__)
_LOGGER.setLevel(logging.DEBUG)

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigType,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Syncleo Kettle switch platform from config entry."""
    coordinator: PolarisDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    is_heater = coordinator.device_info['model_id'] in POLARIS_HEATER_TYPE

    # Child lock and sound (volume) are common to kettles and heaters.
    switches = [
        ChildLockSwitch(coordinator, config_entry.entry_id),
        VolumeSwitch(coordinator, config_entry.entry_id),
    ]

    # Backlight/display: kettles that expose it, plus heaters (configurable display).
    if is_heater or coordinator.device_info['model_id'] in POLARIS_KETTLE_WITH_BACKLIGHT_TYPE:
        switches.append(BacklightSwitch(coordinator, config_entry.entry_id))

    # Heaters expose open-window detection. Power on/off is intentionally NOT
    # exposed as a switch: the climate entity already provides HEAT/OFF via the
    # same underlying power command, so a separate PowerSwitch would be redundant.
    if is_heater:
        switches.append(WindowDetectionSwitch(coordinator, config_entry.entry_id))

    async_add_entities(switches)

class ChildLockSwitch(SwitchEntity):
    """Representation of a Child Lock switch."""
    
    _attr_has_entity_name = True
    _attr_translation_key = "child_lock"
    
    def __init__(self, coordinator: PolarisDataUpdateCoordinator, entry_id: str) -> None:
        """Initialize the Child Lock switch."""
        self.coordinator = coordinator
        self._entry_id = entry_id
        self._attr_unique_id = f"{coordinator._mac}_child_lock"
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.data.get("connected", False)

    @property
    def is_on(self) -> bool:
        """Return true if child lock is enabled."""
        return self.coordinator.data.get("child_lock", False)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the child lock on."""
        await self.coordinator.async_set_child_lock(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the child lock off."""
        await self.coordinator.async_set_child_lock(False)

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )

    @property
    def should_poll(self) -> bool:
        """No need to poll, coordinator notifies of updates."""
        return False

class VolumeSwitch(SwitchEntity):
    """Representation of a Volume switch."""
    
    _attr_has_entity_name = True
    _attr_translation_key = "volume"
    
    def __init__(self, coordinator: PolarisDataUpdateCoordinator, entry_id: str) -> None:
        """Initialize the Volume switch."""
        self.coordinator = coordinator
        self._entry_id = entry_id
        self._attr_unique_id = f"{coordinator._mac}_volume"
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.data.get("connected", False)

    @property
    def is_on(self) -> bool:
        """Return true if volume is enabled."""
        return self.coordinator.data.get("volume", False)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the volume on."""
        await self.coordinator.async_set_volume(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the volume off."""
        await self.coordinator.async_set_volume(False)

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )

    @property
    def should_poll(self) -> bool:
        """No need to poll, coordinator notifies of updates."""
        return False

class BacklightSwitch(SwitchEntity):
    """Representation of a Backlight switch."""
    
    _attr_has_entity_name = True
    _attr_translation_key = "backlight"
    
    def __init__(self, coordinator: PolarisDataUpdateCoordinator, entry_id: str) -> None:
        """Initialize the Backlight switch."""
        self.coordinator = coordinator
        self._entry_id = entry_id
        self._attr_unique_id = f"{coordinator._mac}_backlight"
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.data.get("connected", False)

    @property
    def is_on(self) -> bool:
        """Return true if backlight is enabled."""
        return self.coordinator.data.get("backlight", False)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the backlight on."""
        await self.coordinator.async_set_backlight(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the backlight off."""
        await self.coordinator.async_set_backlight(False)

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )

    @property
    def should_poll(self) -> bool:
        """No need to poll, coordinator notifies of updates."""
        return False


class WindowDetectionSwitch(SwitchEntity):
    """Toggle for the heater's open-window detection."""

    _attr_has_entity_name = True
    _attr_translation_key = "window_detection"
    _attr_icon = "mdi:window-open-variant"

    def __init__(self, coordinator: PolarisDataUpdateCoordinator, entry_id: str) -> None:
        """Initialize the window detection switch."""
        self.coordinator = coordinator
        self._entry_id = entry_id
        self._attr_unique_id = f"{coordinator._mac}_window_detection"
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.data.get("connected", False)

    @property
    def is_on(self) -> bool:
        """Return true if open-window detection is enabled."""
        return self.coordinator.data.get("window_detection", False)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable open-window detection."""
        await self.coordinator.async_set_window_detection(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable open-window detection."""
        await self.coordinator.async_set_window_detection(False)

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )

    @property
    def should_poll(self) -> bool:
        """No need to poll, coordinator notifies of updates."""
        return False
