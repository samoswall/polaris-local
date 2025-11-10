"""The Syncleo Kettle integration."""
from __future__ import annotations

import logging
import asyncio
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .coordinator import PolarisDataUpdateCoordinator
from .discovery import SyncleoDiscovery

_LOGGER = logging.getLogger(__name__)
_LOGGER.setLevel(logging.DEBUG)

PLATFORMS: list[Platform] = [Platform.WATER_HEATER, Platform.SWITCH, Platform.LIGHT, Platform.SENSOR]

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Syncleo Kettle component."""
    # Предварительно настраиваем discovery с shared Zeroconf
    try:
        from homeassistant.components.zeroconf import async_get_instance
        zc = await async_get_instance(hass)
        discovery = SyncleoDiscovery.get_instance()
        discovery.set_zeroconf_instance(zc)
        
        # Запускаем discovery заранее
        await hass.async_add_executor_job(discovery.start_discovery)
        _LOGGER.info("Pre-started Syncleo device discovery with shared Zeroconf")
    except Exception as err:
        _LOGGER.warning("Could not pre-start discovery: %s", err)
    
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Syncleo Kettle from a config entry."""
    
    mac = entry.data["mac"].replace(":", "").lower()
    device_token = entry.data["device_token"]
    
    coordinator = PolarisDataUpdateCoordinator(hass, mac, device_token)
    
    # Get shared Zeroconf instance
    try:
        from homeassistant.components.zeroconf import async_get_instance
        zc = await async_get_instance(hass)
    except ImportError:
        _LOGGER.warning("Zeroconf not available, falling back to internal instance")
        zc = None
    
    # Try to get discovered device info from discovery singleton with short wait
    discovery = SyncleoDiscovery.get_instance()
    discovered_device_info = None
    
    # Ждем до 3 секунд для обнаружения устройства в кэше
    for _ in range(6):  # 6 попыток по 0.5 секунды = 3 секунды
        discovered_devices = await hass.async_add_executor_job(discovery.get_devices)
                                 
        for device in discovered_devices:
            if device['mac'] == mac:
                discovered_device_info = device
                _LOGGER.info("Found device in discovery cache: %s", device)
                break
        if discovered_device_info is not None:
            break
        await asyncio.sleep(0.5)
    
    # Setup the kettle connection
    try:
        await coordinator.async_setup(zc, discovered_device_info)
        await coordinator.async_config_entry_first_refresh()
    except Exception as err:
        _LOGGER.error("Device %s setup failed: %s", mac, err)
        await coordinator.shutdown()
        raise ConfigEntryNotReady(f"Device setup failed: {err}") from err
    
    hass.data.setdefault(entry.domain, {})
    hass.data[entry.domain][entry.entry_id] = coordinator
    
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        coordinator: PolarisDataUpdateCoordinator = hass.data[entry.domain].pop(entry.entry_id)
        await coordinator.shutdown()
    
    return unload_ok