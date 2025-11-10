"""Config flow for Syncleo Kettle integration."""
from __future__ import annotations

import logging
import asyncio
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .coordinator import PolarisDataUpdateCoordinator
from .discovery import SyncleoDiscovery
from .const import DOMAIN, POLARIS_DEVICE 

_LOGGER = logging.getLogger(__name__)
_LOGGER.setLevel(logging.DEBUG)

class ConfigFlow(config_entries.ConfigFlow, domain="syncleo_kettle"):
    """Handle a config flow for Syncleo Kettle."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovered_devices = {}
        self._device_options = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        
        # Get singleton discovery instance
        discovery = SyncleoDiscovery.get_instance()
        
        # Ensure discovery uses shared Zeroconf instance
        try:
            from homeassistant.components.zeroconf import async_get_instance
            zc = await async_get_instance(self.hass)
            discovery.set_zeroconf_instance(zc)
        except Exception as err:
            _LOGGER.warning("Could not set shared Zeroconf: %s", err)
        
        # Start discovery if not already started
        await self.hass.async_add_executor_job(discovery.start_discovery)
        
        # Даем время для обнаружения устройств
        await asyncio.sleep(3.0)  # Увеличили задержку
        
        # Get current list of devices
        devices = await self.hass.async_add_executor_job(discovery.get_devices)
        
        # Prepare device list for dropdown
        self._device_options = {}
        for device in devices:
            # Формируем понятное описание устройства
            description = f"{device['devtype']}: {device['mac']}"
            if device['vendor'] != 'Unknown':
                description += f" ({device['vendor']}"
                if int(device['basetype']) in POLARIS_DEVICE:
                    description += f" {POLARIS_DEVICE[int(device['basetype'])]['model']}"
                else:
                    description += f" Unknown"
                description += ")"

#            description = f"{device['devtype']}: {device['mac']}"
#            if device['vendor'] != 'Unknown':
#                description += f" ({device['vendor']}"
#                if device['basetype'] != 'Unknown':
#                    description += f" {device['basetype']}"
#                description += ")"






            
            self._device_options[device['mac']] = description
        
        # Store device info for later use
        self._discovered_devices = {device['mac']: device for device in devices}

        _LOGGER.debug("Found %s devices for config flow", len(self._device_options))
        for mac, device in self._discovered_devices.items():
            _LOGGER.debug("Device %s: %s", mac, device)

        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input, self._discovered_devices)
            except ValueError as err:
                errors["base"] = "invalid_input"
                _LOGGER.error("Validation error: %s", err)
            except ConnectionError as err:
                errors["base"] = "cannot_connect"
                _LOGGER.error("Connection error: %s", err)
            except Exception as err:
                _LOGGER.exception("Unexpected exception during validation")
                errors["base"] = "unknown"
            else:
                # Check if we don't already have an entry with this MAC
                await self.async_set_unique_id(user_input["mac"].replace(":", "").lower())
                self._abort_if_unique_id_configured()
                
                return self.async_create_entry(title=info["title"], data=user_input)

        # If no devices found, show manual input
        if not self._device_options:
            _LOGGER.info("No devices found, showing manual input form")
            schema = vol.Schema({
                vol.Required("mac"): str,
                vol.Required("device_token"): str,
            })
            
            return self.async_show_form(
                step_id="user",
                data_schema=schema,
                errors=errors,
                description_placeholders={
                    "no_devices": "No devices found. Please enter MAC address manually."
                }
            )

        # Show dropdown with discovered devices
        _LOGGER.info("Showing device selection with %s devices", len(self._device_options))
        default_mac = next(iter(self._device_options.keys())) if self._device_options else ""
        schema = vol.Schema({
            vol.Required("mac", default=default_mac): vol.In(self._device_options),
            vol.Required("device_token"): str,
        })

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "device_count": str(len(self._device_options))
            }
        )

    async def async_step_zeroconf(self, discovery_info: zeroconf.ZeroconfServiceInfo) -> FlowResult:
        """Handle zeroconf discovery."""
        _LOGGER.info("Discovered device via zeroconf: %s", discovery_info)
        
        # Extract MAC from hostname
        hostname = discovery_info.hostname
        mac = hostname.split('.')[0].replace(':', '').lower() if hostname else None
        
        if not mac or len(mac) != 12:
            return self.async_abort(reason="invalid_discovery_info")
            
        # Normalize MAC address
        normalized_mac = mac.lower()
        
        await self.async_set_unique_id(normalized_mac)
        self._abort_if_unique_id_configured()
        
        # Извлекаем информацию об устройстве из свойств Zeroconf
        properties = discovery_info.properties
        vendor = "Unknown"
        basetype = "00"
        devtype = "00"
        
        if properties:
            # Конвертируем свойства в строковый формат
            str_properties = {}
            for key, value in properties.items():
                try:
                    key_str = key.decode('utf-8') if isinstance(key, bytes) else str(key)
                    value_str = value.decode('utf-8') if isinstance(value, bytes) else str(value)
                    str_properties[key_str] = value_str
                except Exception as prop_err:
                    _LOGGER.debug("Error decoding property %s: %s", key, prop_err)
                    continue
            
            vendor = str_properties.get('vendor', 'Unknown')
            basetype = str_properties.get('basetype', '00')
            devtype = str_properties.get('devtype', '00')
        
        # Создаем понятное имя устройства
        try:
            model = POLARIS_DEVICE[int(devtype)]['model']
        except (KeyError, ValueError):
            model = f"Type {devtype}"
        
        device_name = f"{vendor} {model}"
        
        # Создаем контекст для отображения понятного имени
        self.context["title_placeholders"] = {
            "name": device_name,
            "mac": normalized_mac
        }
        return await self.async_step_user()

async def validate_input(hass: HomeAssistant, data: dict[str, Any], discovered_devices: dict = None) -> dict[str, Any]:
    """Validate the user input allows us to connect."""
    
    mac = data["mac"].replace(":", "").lower()
    device_token = data["device_token"]
    devtype = discovered_devices[mac]["devtype"]
    _LOGGER.debug("---DATA--- %s %s %s", devtype, mac, device_token)
    
    # Basic validation
    if len(mac) != 12:
        raise ValueError("Invalid MAC address format")
    
    if len(device_token) != 32:
        raise ValueError("Invalid device token length")
    
    # Get shared Zeroconf instance
    try:
        from homeassistant.components.zeroconf import async_get_instance
        zc = await async_get_instance(hass)
    except ImportError:
        zc = None
    
    # Try to discover device
    coordinator = PolarisDataUpdateCoordinator(hass, mac, device_token)
    
    try:
        # Если у нас есть информация об устройстве из discovery, используем ее
        discovered_device_info = None
        if discovered_devices and mac in discovered_devices:
            discovered_device_info = discovered_devices[mac]
            _LOGGER.info("Using discovered device info: %s", discovered_device_info)
        
        await coordinator.async_setup(zc, discovered_device_info)
        await asyncio.sleep(1)  # Даем время на установку соединения
        await coordinator.shutdown()
    except Exception as err:
        _LOGGER.error("Validation failed: %s", err)
        raise ConnectionError(f"Cannot connect to device: {err}") from err
    
    return {"title": f"{discovered_devices[mac]['vendor']} {POLARIS_DEVICE[int(devtype)]['model']} {mac}"}