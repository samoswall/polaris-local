"""Light platform for Syncleo Kettle."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import (
    LightEntity,
    ColorMode,
    ATTR_RGB_COLOR,
    ATTR_BRIGHTNESS,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .coordinator import PolarisDataUpdateCoordinator
from .const import DOMAIN, POLARIS_KETTLE_WITH_NIGHT_TYPE

_LOGGER = logging.getLogger(__name__)
_LOGGER.setLevel(logging.DEBUG)

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigType,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Syncleo Kettle light platform from config entry."""
    coordinator: PolarisDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    
    # Проверяем что device_info был создан
    if coordinator.device_info is None:
        _LOGGER.error("Device info not available, cannot create entity")
        return
    # Добавляем только чайники с ночником
    if coordinator.device_info['model_id'] in POLARIS_KETTLE_WITH_NIGHT_TYPE:
        async_add_entities([KettleNightLight(coordinator, config_entry.entry_id)])

class KettleNightLight(LightEntity):
    """Representation of a Kettle Night Light."""
    
    _attr_has_entity_name = True
    _attr_translation_key = "night_light"
    
    def __init__(self, coordinator: PolarisDataUpdateCoordinator, entry_id: str) -> None:
        """Initialize the night light."""
        self.coordinator = coordinator
        self._entry_id = entry_id
        self._attr_unique_id = f"{coordinator._mac}_night_light"
        self._attr_device_info = coordinator.device_info
        
        # Light attributes
        self._attr_supported_color_modes = {ColorMode.RGB}
        self._attr_color_mode = ColorMode.RGB
        self._attr_brightness = 255  # Яркость в диапазоне 0-255
        self._attr_effect_list = None  # No effects
        self._attr_effect = None
        
        # Храним базовый цвет без учета яркости
        self._base_rgb_color = (255, 255, 255)  # Белый по умолчанию
        self._data_length = 4

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.data.get("connected", False)

    @property
    def is_on(self) -> bool:
        """Return true if night light is on."""
        return self.coordinator.data.get("night", False)

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Return the rgb color value with brightness applied."""
        color_data = self.coordinator.data.get("color_night", {})
        r = color_data.get("r", 255)
        g = color_data.get("g", 255)
        b = color_data.get("b", 255)
        self._data_length = color_data.get("data_length", 4)
        
        # Сохраняем базовый цвет (то, что пришло от устройства)
        self._base_rgb_color = (r, g, b)
        
        # Применяем яркость к цвету для отображения в HA
        brightness_factor = self.brightness / 255.0
        return (
            int(r * brightness_factor),
            int(g * brightness_factor),
            int(b * brightness_factor)
        )

    @property
    def brightness(self) -> int:
        """Return the brightness of the light (0-255)."""
        return self._attr_brightness

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on."""
        update_needed = False
        
        # First turn on night mode if it's off
        if not self.is_on:
            await self.coordinator.async_set_night(True)
            update_needed = True
        
        # Handle brightness change
        if ATTR_BRIGHTNESS in kwargs:
            new_brightness = kwargs[ATTR_BRIGHTNESS]
            self._attr_brightness = new_brightness
            _LOGGER.debug("Setting brightness to: %s", new_brightness)
            
            # При изменении яркости пересчитываем цвет для устройства
            if self._base_rgb_color and new_brightness < 255:
                r, g, b = self._base_rgb_color
                brightness_factor = new_brightness / 255.0
                r_full = min(255, int(r / brightness_factor)) if brightness_factor > 0 else 0
                g_full = min(255, int(g / brightness_factor)) if brightness_factor > 0 else 0
                b_full = min(255, int(b / brightness_factor)) if brightness_factor > 0 else 0
                await self.coordinator.async_set_color_night(r_full, g_full, b_full, 0, self._data_length)
            
            update_needed = True
            
        # Handle color change
        if ATTR_RGB_COLOR in kwargs:
            rgb_color = kwargs[ATTR_RGB_COLOR]
            
            # При выборе цвета в HA, он уже учитывает текущую яркость
            # Поэтому нам нужно преобразовать его обратно в "полную яркость"
            r, g, b = rgb_color
            
            if self.brightness < 255:
                # Цвет пришел с уже примененной яркостью, преобразуем обратно
                brightness_factor = self.brightness / 255.0
                r_full = min(255, int(r / brightness_factor)) if brightness_factor > 0 else 0
                g_full = min(255, int(g / brightness_factor)) if brightness_factor > 0 else 0
                b_full = min(255, int(b / brightness_factor)) if brightness_factor > 0 else 0
                
                # Сохраняем базовый цвет (то, что отправляем на устройство)
                self._base_rgb_color = (r_full, g_full, b_full)
                await self.coordinator.async_set_color_night(r_full, g_full, b_full, 0, self._data_length)
            else:
                # При 100% яркости просто используем полученный цвет
                self._base_rgb_color = (r, g, b)
                await self.coordinator.async_set_color_night(r, g, b, 0, self._data_length)
                
            update_needed = True
        
        if update_needed:
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        await self.coordinator.async_set_night(False)
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )

    @property
    def should_poll(self) -> bool:
        """No need to poll, coordinator notifies of updates."""
        return False