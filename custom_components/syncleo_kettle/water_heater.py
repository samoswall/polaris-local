"""Water Heater platform for Syncleo Kettle."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.water_heater import (
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .coordinator import PolarisDataUpdateCoordinator
from .const import (
    DOMAIN,
    POLARIS_KETTLE_TYPE,
    POLARIS_KETTLE_WITH_WEIGHT_TYPE,
    POLARIS_KETTLE_WITH_NIGHT_TYPE,
    POLARIS_KETTLE_WITH_BACKLIGHT_TYPE,
    POLARIS_KETTLE_WITH_TEA_TIME_MODE_TYPE,
    POLARIS_KETTLE_WITH_KEEP_WITH_WARM_MODE_TYPE,
)


from .protocol import PowerType

_LOGGER = logging.getLogger(__name__)
_LOGGER.setLevel(logging.DEBUG)

# Маппинг режимов PowerType на состояния water_heater
POWER_TYPE_TO_OPERATION_MODE = {
    PowerType.OFF: "off",
    PowerType.ON: "heat",  # Простой нагрев
    PowerType.BOILKEEP: "boil_keep",  # Кипячение с удержанием
    PowerType.WARMUP: "warmup",  # Разогрев
    PowerType.WARMUPKEEP: "warmup_keep",  # Разогрев с удержанием
    PowerType.IQBOIL: "iq_boil",  # IQ кипячение
    PowerType.TEA: "tea_ceremony",  # Чайная церемония
}

OPERATION_MODE_TO_POWER_TYPE = {v: k for k, v in POWER_TYPE_TO_OPERATION_MODE.items()}

# Поддерживаемые режимы работы
SUPPORTED_OPERATION_MODES = [
    mode for mode in POWER_TYPE_TO_OPERATION_MODE.values()
]

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigType,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Syncleo Kettle water_heater platform from config entry."""
    coordinator: PolarisDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    
    # Проверяем что device_info был создан
    if coordinator.device_info is None:
        _LOGGER.error("Device info not available, cannot create entity")
        return
    _LOGGER.debug("--INFO-- %s", coordinator.device_info) # {'identifiers': {('syncleo_kettle', '485519efc4b1')}, 'name': 'Polaris PWK-1725CGLD 485519efc4b1', 'manufacturer': 'Polaris', 'model': 'PWK-1725CGLD', 'sw_version': '2.33', 'model_id': '86'}

    # Добавляем только чайники
    if (coordinator.device_info['model_id'] in POLARIS_KETTLE_TYPE) or (coordinator.device_info['model_id'] in POLARIS_KETTLE_WITH_WEIGHT_TYPE):
        async_add_entities([SyncleoKettleWaterHeater(coordinator, config_entry.entry_id)])

class SyncleoKettleWaterHeater(WaterHeaterEntity):
    """Representation of a Syncleo Kettle as a water_heater device."""
    
    _attr_has_entity_name = True
    _attr_translation_key = "syncleo_kettle"
    
    def __init__(self, coordinator: PolarisDataUpdateCoordinator, entry_id: str) -> None:
        """Initialize the water_heater device."""
        self.coordinator = coordinator
        self._entry_id = entry_id
        self._attr_unique_id = f"{coordinator._mac}_water_heater"
        self._attr_device_info = coordinator.device_info
        
        # Static attributes
        self._attr_temperature_unit = UnitOfTemperature.CELSIUS
        self._attr_supported_features = (
            WaterHeaterEntityFeature.TARGET_TEMPERATURE |
            WaterHeaterEntityFeature.OPERATION_MODE |
            # async_turn_on/async_turn_off are implemented below; without the flag
            # Home Assistant rejects both services with ServiceNotSupported.
            WaterHeaterEntityFeature.ON_OFF
        )
        # Режимы работы в зависимости от типа
        if (self.coordinator.device_info['model_id'] not in POLARIS_KETTLE_WITH_TEA_TIME_MODE_TYPE) and (self.coordinator.device_info['model_id'] not in POLARIS_KETTLE_WITH_KEEP_WITH_WARM_MODE_TYPE):
            self._attr_operation_list = [
                mode for mode in POWER_TYPE_TO_OPERATION_MODE.values() if mode not in {"tea_ceremony", "boil_keep"}
            ]
        elif (self.coordinator.device_info['model_id'] not in POLARIS_KETTLE_WITH_KEEP_WITH_WARM_MODE_TYPE):
           self._attr_operation_list = [
                mode for mode in POWER_TYPE_TO_OPERATION_MODE.values() if mode != "boil_keep"
            ]
        else:
            self._attr_operation_list = [
                mode for mode in POWER_TYPE_TO_OPERATION_MODE.values()
            ]
            
        self._attr_min_temp = 30
        self._attr_max_temp = 100
        self._attr_target_temperature_step = 5

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
    def current_operation(self) -> str:
        """Return current operation mode."""
        power_type = self.coordinator.data.get("power_type", PowerType.OFF)
        return POWER_TYPE_TO_OPERATION_MODE.get(power_type, "off")

    @property
    def is_heating(self) -> bool:
        """Return true if water heater is heating."""
        return self.coordinator.data.get("is_heating", False)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature."""
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is not None:
            await self.coordinator.async_set_temperature(int(temperature))

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        """Set new operation mode."""
        power_type = OPERATION_MODE_TO_POWER_TYPE.get(operation_mode, PowerType.OFF)
        await self.coordinator.async_set_power(power_type)

    @property
    def supported_features(self) -> WaterHeaterEntityFeature:
        """Return the list of supported features."""
        return self._attr_supported_features

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the water heater on using default mode (heat)."""
        await self.coordinator.async_set_power(PowerType.ON)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the water heater off."""
        await self.coordinator.async_set_power(PowerType.OFF)

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )

    @property
    def should_poll(self) -> bool:
        """No need to poll, coordinator notifies of updates."""
        return False