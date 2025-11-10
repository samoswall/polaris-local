"""Sensor platform for Syncleo Kettle."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import UnitOfTemperature, UnitOfMass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .coordinator import PolarisDataUpdateCoordinator
from .const import DOMAIN, POLARIS_KETTLE_WITH_WEIGHT_TYPE
from .protocol import DeviceHardwareMessage

_LOGGER = logging.getLogger(__name__)
_LOGGER.setLevel(logging.DEBUG)

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigType,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Syncleo Kettle sensor platform from config entry."""
    coordinator: PolarisDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    
    # Проверяем что device_info был создан
    if coordinator.device_info is None:
        _LOGGER.error("Device info not available, cannot create entity")
        return
    # Добавляем только чайники с весом
    if coordinator.device_info['model_id'] in POLARIS_KETTLE_WITH_WEIGHT_TYPE:
        sensors = [
            CurrentTemperatureSensor(coordinator, config_entry.entry_id),
            DeviceHardwareSensor(coordinator, config_entry.entry_id),
            WeightSensor(coordinator, config_entry.entry_id),
        ]
    else:
        sensors = [
            CurrentTemperatureSensor(coordinator, config_entry.entry_id),
            DeviceHardwareSensor(coordinator, config_entry.entry_id),
        ]
    
    async_add_entities(sensors)

class WeightSensor(SensorEntity):
    """Representation of a Weight sensor."""
    
    _attr_has_entity_name = True
    _attr_name = "Weight"
    _attr_native_unit_of_measurement = UnitOfMass.GRAMS
    _attr_device_class = SensorDeviceClass.WEIGHT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:weight-gram"
    
    def __init__(self, coordinator: PolarisDataUpdateCoordinator, entry_id: str) -> None:
        """Initialize the Current Temperature sensor."""
        self.coordinator = coordinator
        self._entry_id = entry_id
        self._attr_unique_id = f"{coordinator._mac}_weight"
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.data.get("connected", False)

    @property
    def native_value(self) -> float | None:
        """Return the weight."""
        return self.coordinator.data.get("weight")

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )

    @property
    def should_poll(self) -> bool:
        """No need to poll, coordinator notifies of updates."""
        return False


class CurrentTemperatureSensor(SensorEntity):
    """Representation of a Current Temperature sensor."""
    
    _attr_has_entity_name = True
    _attr_name = "Current Temperature"
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:thermometer"
    
    def __init__(self, coordinator: PolarisDataUpdateCoordinator, entry_id: str) -> None:
        """Initialize the Current Temperature sensor."""
        self.coordinator = coordinator
        self._entry_id = entry_id
        self._attr_unique_id = f"{coordinator._mac}_current_temperature"
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.data.get("connected", False)

    @property
    def native_value(self) -> float | None:
        """Return the current temperature."""
        return self.coordinator.data.get("current_temperature")

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )

    @property
    def should_poll(self) -> bool:
        """No need to poll, coordinator notifies of updates."""
        return False

class DeviceHardwareSensor(SensorEntity):
    """Representation of a Device Hardware sensor."""
    
    _attr_has_entity_name = True
    _attr_name = "Device Hardware"
    _attr_icon = "mdi:chip"
    
    def __init__(self, coordinator: PolarisDataUpdateCoordinator, entry_id: str) -> None:
        """Initialize the Device Hardware sensor."""
        self.coordinator = coordinator
        self._entry_id = entry_id
        self._attr_unique_id = f"{coordinator._mac}_device_hardware"
        self._attr_device_info = coordinator.device_info
        self._hw_version = None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.data.get("connected", False)

    @property
    def native_value(self) -> str | None:
        """Return the hardware version as string."""
        # Пробуем получить hardware из координатора
        hw_data = self.coordinator.data.get("device_hardware")
        if hw_data and isinstance(hw_data, list):
            return ".".join(map(str, hw_data))
        # Или из локального кэша
        if self._hw_version:
            return ".".join(map(str, self._hw_version))
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        hw_data = self.coordinator.data.get("device_hardware") or self._hw_version
        if not hw_data or not isinstance(hw_data, list):
            return {}
            
        return {
            "hw_major": hw_data[0] if len(hw_data) > 0 else None,
            "hw_minor": hw_data[1] if len(hw_data) > 1 else None,
            "hw_revision": hw_data[2] if len(hw_data) > 2 else None,
            "full_version": ".".join(map(str, hw_data)),
        }

    def incoming_message(self, message) -> None:
        """Handle incoming messages to update hardware info."""
        if isinstance(message, DeviceHardwareMessage):
            self._hw_version = message.hw
            _LOGGER.debug("Device hardware updated via message: %s", message.hw)
            self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        # Add listener for coordinator updates
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )
        
        # Register for incoming messages
#        self.coordinator.kettle.conn.add_incoming_message_listener(self)

    @property
    def should_poll(self) -> bool:
        """No need to poll, coordinator notifies of updates."""
        return False