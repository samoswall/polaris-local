"""Data update coordinator for Syncleo Kettle."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, POLARIS_DEVICE
from .kettle import Kettle
from .protocol import (
    PowerType,
    IncomingMessageListener,
    ConnectionStatusListener,
    ConnectionStatus,
    CurrentTemperatureMessage,
    ModeMessage,
    TargetTemperatureMessage,
    ChildLockMessage,
    VolumeMessage,
    BacklightMessage,
    NightMessage,
    ColorNightMessage,
    DeviceHardwareMessage,
    ErrorMessage,
    WeightMessage
)

_LOGGER = logging.getLogger(__name__)
_LOGGER.setLevel(logging.DEBUG)

class PolarisDataUpdateCoordinator(DataUpdateCoordinator, IncomingMessageListener, ConnectionStatusListener):
    """Class to manage fetching Polaris data."""
    
    def __init__(self, hass: HomeAssistant, mac: str, device_token: str) -> None:
        """Initialize global kettle data updater."""
        self.kettle = Kettle(mac, device_token)
        self._hass = hass
        self._mac = mac
        self._device_token = device_token
        
        super().__init__(
            hass,
            _LOGGER,
            name=f"Polaris Kettle {mac}",
            update_interval=timedelta(seconds=30),
        )
        
        self.data = {
            "current_temperature": None,
            "target_temperature": None,
            "power_type": PowerType.OFF,
            "is_heating": False,
            "child_lock": False,
            "volume": False,
            "backlight": False,
            "night": False,
            "color_night": {"r": 0, "g": 0, "b": 0},
            "error": False,
            "connected": False,
            "device_hardware": None,
            "weight": None,
        }
        
        # Device info будет установлен после discovery
        self.device_info = None
        self._reconnect_in_progress = False
        self._last_device_update = 0
        self._last_service_info = None
        self._last_reconnect_time = 0
        self._setup_complete = False  # Добавляем флаг завершения настройки
        self._discovered_device_info = None  # Сохраняем информацию об обнаруженном устройстве

    async def _async_update_data(self) -> dict[str, Any]:
        """Update data via library."""
        try:
            # Проверяем не только флаг connected, но и реальное состояние соединения
            is_actually_connected = (
                self.kettle.is_connected() and 
                self.kettle.conn and 
                self.kettle.conn.is_running()
            )
            
            if not is_actually_connected:
                _LOGGER.debug("Kettle is not connected (connection check failed)")
                self.data["connected"] = False
                # Попробуем переподключиться если устройство доступно
                if self.kettle.device and self.kettle.device.si:
                    _LOGGER.info("Device is available but connection is dead, attempting reconnect")
                    if not self._reconnect_in_progress:
                        # Запускаем переподключение асинхронно
                        self._hass.async_create_task(self._async_perform_reconnect())
                return self.data
            
            self.data["connected"] = True
            return self.data
            
        except Exception as err:
            _LOGGER.error(f"Error in data update: {err}")
            self.data["connected"] = False
            return self.data

    async def _async_perform_reconnect(self):
        """Perform reconnection asynchronously."""
        if self._reconnect_in_progress:
            return
            
        self._reconnect_in_progress = True
        try:
            _LOGGER.info("Performing reconnection from data update")
            
            # Полностью останавливаем соединение
            if self.kettle.conn:
                await self.kettle.async_force_reconnect()
            
            # Ждем завершения
            await asyncio.sleep(1.0)
            
            # Запускаем новое соединение только если device_info уже создан
            if self.device_info is not None:
                await self._hass.async_add_executor_job(
                    self.kettle.start_server_if_needed,
                    self,  # incoming_message_listener
                    self   # connection_status_listener
                )
                _LOGGER.info("Reconnection from data update completed")
            else:
                _LOGGER.warning("Cannot reconnect - device info not available")
                
        except Exception as err:
            _LOGGER.error(f"Failed to reconnect from data update: {err}")
        finally:
            self._reconnect_in_progress = False

    def _create_device_info(self, service_info) -> None:
        """Create device info from service info."""
        properties = service_info.properties
        
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
        
        # Получаем данные из свойств ServiceInfo
        vendor = str_properties.get('vendor', 'Polaris')
        basetype = str_properties.get('basetype', '00')
        devtype = str_properties.get('devtype', '00')
        firmware = str_properties.get('firmware', '0.00')
        
        # Используем сохраненную информацию об устройстве, если properties пустые
        if not str_properties and self._discovered_device_info:
            _LOGGER.info("Using saved device info due to empty properties")
            vendor = self._discovered_device_info.get('vendor', 'Polaris')
            basetype = self._discovered_device_info.get('basetype', '00')
            devtype = self._discovered_device_info.get('devtype', '00')
            firmware = self._discovered_device_info.get('firmware', '0.00')
        
        # Получаем модель из POLARIS_DEVICE или используем базовый тип
        try:
            model = POLARIS_DEVICE[int(devtype)]['model']
        except (KeyError, ValueError):
            model = f"Type {devtype}"
            _LOGGER.warning("Unknown device type: %s, using default model", devtype)
        
        _LOGGER.info(f"Device info: vendor={vendor}, basetype={basetype}, devtype={devtype}, firmware={firmware}, model={model}")
        
        self.device_info = DeviceInfo(
            identifiers={(DOMAIN, self._mac)},
            name=f"{vendor} {model} {self._mac}",
            manufacturer=vendor,
            model=model,
            sw_version=firmware,
            model_id=devtype,
        )
        
        
    def incoming_message(self, message) -> None:
        """Handle incoming messages from kettle."""
        _LOGGER.debug("Received message: %s", message)
        
        # Schedule update in event loop
        self._hass.loop.call_soon_threadsafe(
            self._async_handle_incoming_message, message
        )

    @callback
    def _async_handle_incoming_message(self, message) -> None:
        """Handle incoming messages in event loop."""
        if isinstance(message, CurrentTemperatureMessage):
            self.data["current_temperature"] = message.current_temperature
            # Determine if heating based on temperature and power state
            current_temp = self.data["current_temperature"]
            target_temp = self.data["target_temperature"]
            power_type = self.data["power_type"]
            
            # Нагрев происходит когда устройство включено и текущая температура меньше целевой
            if (power_type != PowerType.OFF and 
                current_temp is not None and 
                target_temp is not None and
                current_temp < target_temp):
                self.data["is_heating"] = True
            else:
                self.data["is_heating"] = False
                
        elif isinstance(message, ModeMessage):
            self.data["power_type"] = message.pt
            if message.pt == PowerType.OFF:
                self.data["is_heating"] = False
                
        elif isinstance(message, TargetTemperatureMessage):
            self.data["target_temperature"] = message.temperature
            
        elif isinstance(message, ChildLockMessage):
            self.data["child_lock"] = message.value
            
        elif isinstance(message, VolumeMessage):
            self.data["volume"] = message.value
            
        elif isinstance(message, BacklightMessage):
            self.data["backlight"] = message.value
            
        elif isinstance(message, NightMessage):
            self.data["night"] = message.value
            
        elif isinstance(message, ColorNightMessage):
            self.data["color_night"] = {
                "r": message.r,
                "g": message.g, 
                "b": message.b,
                "w": message.w,
                "data_length": message.data_length
            }
        elif isinstance(message, WeightMessage):
            self.data["weight"] = message.weight
            _LOGGER.debug("---WEIGHT--- %s grams", message.weight)
    
        elif isinstance(message, DeviceHardwareMessage):
            self.data["device_hardware"] = message.hw
            _LOGGER.debug("---HARDWARE--- %s",message.hw)
            
        elif isinstance(message, ErrorMessage):
            self.data["error"] = message.value
        
        # Schedule update for entities
        self.async_set_updated_data(self.data)

    def connection_status_updated(self, status: ConnectionStatus) -> None:
        """Handle connection status updates."""
        _LOGGER.debug("Connection status updated: %s", status)
        
        # Schedule update in event loop
        self._hass.loop.call_soon_threadsafe(
            self._async_handle_connection_status, status
        )

    @callback
    def _async_handle_connection_status(self, status: ConnectionStatus) -> None:
        """Handle connection status updates in event loop."""
        self.data["connected"] = status == ConnectionStatus.CONNECTED
        
        # Только для RECONNECTING статуса и если нет активного переподключения
        if status == ConnectionStatus.RECONNECTING and not self._reconnect_in_progress:
            _LOGGER.info("Connection status changed to RECONNECTING, initiating restart")
            self._reconnect_in_progress = True
            
            async def restart_connection():
                try:
                    # Ждем перед переподключением
                    await asyncio.sleep(3.0)
                    
                    _LOGGER.info("Performing connection restart due to RECONNECTING status")
                    
                    # Полностью пересоздаем соединение
                    if self.kettle.conn:
                        await self.kettle.async_force_reconnect()
                    
                    await asyncio.sleep(1.0)
                    
                    # Запускаем новое соединение в executor
                    await self._hass.async_add_executor_job(
                        self.kettle.start_server_if_needed,
                        self,  # incoming_message_listener
                        self   # connection_status_listener
                    )
                    
                    _LOGGER.info("Connection restart from RECONNECTING status completed")
                    
                except Exception as err:
                    _LOGGER.error(f"Failed to restart connection from RECONNECTING status: {err}")
                finally:
                    self._reconnect_in_progress = False
            
            # Запускаем в фоне
            self._hass.async_create_task(restart_connection())
        
        self.async_set_updated_data(self.data)

    def device_updated(self):
        """Handle device updates from discovery."""
        current_time = time.time()
        
        # Если setup еще не завершен, игнорируем обновления
        if not self._setup_complete:
            _LOGGER.debug("Device update ignored - setup not complete")
            return
        
        # Получаем текущую информацию о сервисе
        current_service_info = self.kettle.device.si if self.kettle.device else None
        
        # Если properties пустые, не переподключаемся
        if current_service_info and (not hasattr(current_service_info, 'properties') or not current_service_info.properties):
            _LOGGER.debug("Device update ignored - empty properties")
            return
        
        # Проверяем, действительно ли нужно переподключаться
        needs_reconnect = self._should_reconnect(current_service_info, current_time)
        
        if needs_reconnect:
            _LOGGER.info("Device discovery updated, refreshing connection")
            # Schedule update in event loop
            self._hass.loop.call_soon_threadsafe(
                self._async_handle_device_updated
            )
        else:
            _LOGGER.debug("Device update ignored - no significant changes detected")
    
    def _should_reconnect(self, current_service_info, current_time: float) -> bool:
        """Determine if reconnection is needed based on service changes."""
        # Защита от слишком частых переподключений
        if current_time - self._last_reconnect_time < 30:  # Минимум 30 секунд между переподключениями
            _LOGGER.debug("Skipping reconnect - too recent")
            return False
            
        if self._reconnect_in_progress:
            _LOGGER.debug("Skipping reconnect - already in progress")
            return False
            
        # Если это первое обновление
        if self._last_service_info is None:
            self._last_service_info = current_service_info
            self._last_reconnect_time = current_time
            return True
            
        # Сравниваем ключевые параметры
        old_info = self._last_service_info
        new_info = current_service_info
        
        # Проверяем изменения в адресе или порте
        address_changed = (old_info.addresses != new_info.addresses if old_info and new_info else False)
        port_changed = (old_info.port != new_info.port if old_info and new_info else False)
        
        # Проверяем изменения в ключевых свойствах
        properties_changed = False
        if old_info and new_info and old_info.properties and new_info.properties:
            key_properties = [b'public', b'curve', b'protocol']
            for prop in key_properties:
                old_val = old_info.properties.get(prop)
                new_val = new_info.properties.get(prop)
                if old_val != new_val:
                    properties_changed = True
                    _LOGGER.info("Property %s changed: %s -> %s", prop, old_val, new_val)
                    break
        
        needs_reconnect = address_changed or port_changed or properties_changed
        
        if needs_reconnect:
            _LOGGER.info("Reconnection needed - changes detected: address=%s, port=%s, properties=%s", 
                        address_changed, port_changed, properties_changed)
            self._last_service_info = new_info
            self._last_reconnect_time = current_time
        else:
            _LOGGER.debug("No significant changes detected, skipping reconnect")
            
        return needs_reconnect


    def _create_device_info_from_dict(self, device_info: dict) -> None:
        """Create device info from discovered device info dict."""
        vendor = device_info.get('vendor', 'Polaris')
        basetype = device_info.get('basetype', '00')
        devtype = device_info.get('devtype', '00')
        firmware = device_info.get('firmware', '0.00')
        
        # Сохраняем информацию об устройстве для будущего использования
        self._discovered_device_info = device_info
        
        # Получаем модель из POLARIS_DEVICE или используем базовый тип
        try:
            model = POLARIS_DEVICE[int(devtype)]['model']
        except (KeyError, ValueError):
            model = f"Type {devtype}"
            _LOGGER.warning("Unknown device type: %s, using default model", devtype)
        
        _LOGGER.info(f"Device info from dict: vendor={vendor}, basetype={basetype}, devtype={devtype}, firmware={firmware}, model={model}")
        
        self.device_info = DeviceInfo(
            identifiers={(DOMAIN, self._mac)},
            name=f"{vendor} {model} {self._mac}",
            manufacturer=vendor,
            model=model,
            sw_version=firmware,
            model_id=devtype,
        )


    @callback
    def _async_handle_device_updated(self) -> None:
        """Handle device updates in event loop."""
        _LOGGER.info("Handling device update in main thread")
        
        # Дополнительная защита от частых переподключений
        current_time = time.time()
        if self._reconnect_in_progress or (current_time - self._last_device_update < 10):
            _LOGGER.debug("Skipping device update - reconnect already in progress or too recent")
            return
            
        self._last_device_update = current_time
        self._reconnect_in_progress = True
        
        async def restart_connection():
            try:
                _LOGGER.info("Performing connection restart after device update")
                
                # Полностью останавливаем соединение
                if self.kettle.conn:
                    await self.kettle.async_force_reconnect()
                
                # Ждем завершения
                await asyncio.sleep(1.0)
                
                # Запускаем новое соединение только если device_info уже создан
                if self.device_info is not None:
                    await self._hass.async_add_executor_job(
                        self.kettle.start_server_if_needed,
                        self,  # incoming_message_listener
                        self   # connection_status_listener
                    )
                    _LOGGER.info("Connection restart completed")
                else:
                    _LOGGER.warning("Cannot restart connection - device info not available")
                    
            except Exception as err:
                _LOGGER.error("Failed to restart connection after device update: %s", err)
            finally:
                self._reconnect_in_progress = False
        
        # Запускаем в фоне
        self._hass.async_create_task(restart_connection())


    async def async_setup(self, zeroconf_instance: Any = None, discovered_device_info: dict = None) -> None:
        """Set up the kettle connection."""
        # Если уже настроено, не делаем ничего
        if self.device_info is not None and hasattr(self, '_setup_complete') and self._setup_complete:
            _LOGGER.debug("Kettle already setup, skipping")
            return
            
        try:
            # Если есть информация об обнаруженном устройстве, используем ее
            if discovered_device_info:
                _LOGGER.info("Using pre-discovered device info")
                # Создаем device_info с реальными данными устройства
                self._create_device_info_from_dict(discovered_device_info)
                
                # Устанавливаем себя как слушатель обновлений устройства
                self.kettle.set_coordinator_listener(self)
                
                # Запускаем discover с предварительно обнаруженной информацией
                await self._hass.async_add_executor_job(
                    self.kettle.discover, 
                    True,  # wait
                    5,     # timeout
                    zeroconf_instance,
                    discovered_device_info  # discovered_device_info
                )
                
                # Start server - это синхронный метод
                await self._hass.async_add_executor_job(
                    self.kettle.start_server_if_needed,
                    self,  # incoming_message_listener
                    self   # connection_status_listener
                )
            else:
                # Run discovery in executor since it's blocking
                service_info = await self._hass.async_add_executor_job(
                    self.kettle.discover, True, 10, zeroconf_instance
                )
                
                if not service_info:
                    _LOGGER.warning("Device discovery failed - device not found, but will continue with setup")
                    # Не прерываем настройку, продолжаем без service_info
                    # Создаем базовый device_info
                    self._create_device_info_from_dict({
                        'vendor': 'Polaris',
                        'basetype': '00', 
                        'devtype': '00',
                        'firmware': '0.00'
                    })
                else:
                    # Создаем device_info с реальными данными устройства
                    self._create_device_info(service_info)
                
                # Устанавливаем себя как слушатель обновлений устройства
                self.kettle.set_coordinator_listener(self)
                
                # Start server - это синхронный метод
                await self._hass.async_add_executor_job(
                    self.kettle.start_server_if_needed,
                    self,  # incoming_message_listener
                    self   # connection_status_listener
                )
            
            self._setup_complete = True
            _LOGGER.info("Kettle setup completed successfully")
            
        except Exception as err:
            _LOGGER.error("Failed to setup kettle: %s", err)
            await self.shutdown()
            raise UpdateFailed(f"Setup failed: {err}") from err

    async def async_set_power(self, power_type: PowerType) -> None:
        """Set kettle power state."""
        def set_power():
            self.kettle.set_power(power_type, lambda x: _LOGGER.debug("Power set callback: %s", x))
        
        await self._hass.async_add_executor_job(set_power)

    async def async_set_temperature(self, temperature: int) -> None:
        """Set target temperature."""
        def set_temperature():
            self.kettle.set_target_temperature(temperature, lambda x: _LOGGER.debug("Temperature set callback: %s", x))
        
        await self._hass.async_add_executor_job(set_temperature)

    async def shutdown(self) -> None:
        """Shutdown the kettle connection."""
        def stop():
            self.kettle.stop_all()
        
        await self._hass.async_add_executor_job(stop)
        

    async def async_set_child_lock(self, enabled: bool) -> None:
        """Set child lock state."""
        def set_child_lock():
#            message = ChildLockMessage(enabled)
            self.kettle.set_child_lock(enabled, lambda x: _LOGGER.debug(f"Child lock set callback: {x}"))
        
        await self._hass.async_add_executor_job(set_child_lock)

    async def async_set_volume(self, enabled: bool) -> None:
        """Set volume state."""
        def set_volume():
#            message = VolumeMessage(enabled)
            self.kettle.set_volume(enabled, lambda x: _LOGGER.debug(f"Volume set callback: {x}"))
        
        await self._hass.async_add_executor_job(set_volume)

    async def async_set_backlight(self, enabled: bool) -> None:
        """Set backlight state."""
        def set_backlight():
#            message = BacklightMessage(enabled)
            self.kettle.set_backlight(enabled, lambda x: _LOGGER.debug(f"Backlight set callback: {x}"))
        
        await self._hass.async_add_executor_job(set_backlight)
        
    async def async_set_night(self, enabled: bool) -> None:
        """Set night state."""
        def set_night():
            self.kettle.set_night(enabled, lambda x: _LOGGER.debug(f"Night set callback: {x}"))
        
        await self._hass.async_add_executor_job(set_night)

    async def async_set_color_night(self, r: int, g: int, b: int, w: int = 0, data_length: int = 4) -> None:
        """Set color night state with variable data length."""
        def set_color_night():
            self.kettle.set_color_night(r, g, b, w, data_length, 
                                       lambda x: _LOGGER.debug(f"Color night set callback: {x}"))
        
        await self._hass.async_add_executor_job(set_color_night)