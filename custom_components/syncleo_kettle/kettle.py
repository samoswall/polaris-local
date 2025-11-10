from __future__ import annotations

import threading
import logging
import zeroconf
import asyncio
import time
from abc import abstractmethod
from ipaddress import ip_address, IPv4Address, IPv6Address
from typing import Optional, List, Union

from .protocol import (
    UDPConnection,
    ModeMessage,
    ChildLockMessage,
    VolumeMessage,
    BacklightMessage,
    NightMessage,
    ColorNightMessage,
    TargetTemperatureMessage,
    PowerType,
    ConnectionStatus,
    ConnectionStatusListener,
    WrappedMessage
)

_LOGGER = logging.getLogger(__name__)
_LOGGER.setLevel(logging.DEBUG)

class DeviceDiscover(threading.Thread, zeroconf.ServiceListener):
    si: Optional[zeroconf.ServiceInfo]
    _mac: str
    _sb: Optional[zeroconf.ServiceBrowser]
    _zc: Optional[zeroconf.Zeroconf]
    _listeners: List[DeviceListener]
    _valid_addresses: List[Union[IPv4Address, IPv6Address]]
    _only_ipv4: bool
    _use_shared_zeroconf: bool

    def __init__(self, mac: str,
                 listener: Optional[DeviceListener] = None,
                 only_ipv4=True,
                 zeroconf_instance: Optional[zeroconf.Zeroconf] = None):
        super().__init__()
        self.si = None
        self._mac = mac
        self._zc = zeroconf_instance  # Use shared instance
        self._sb = None
        self._only_ipv4 = only_ipv4
        self._valid_addresses = []
        self._listeners = []
        if isinstance(listener, DeviceListener):
            self._listeners.append(listener)
        self._logger = logging.getLogger(f'{__name__}.{self.__class__.__name__}')
        self._use_shared_zeroconf = zeroconf_instance is not None

    def add_listener(self, listener: DeviceListener):
        if listener not in self._listeners:
            self._listeners.append(listener)
        else:
            self._logger.warning(f'add_listener: listener {listener} already in the listeners list')

    def set_info(self, info: zeroconf.ServiceInfo):
        # Проверяем что properties не пустые
        if not info.properties:
            self._logger.debug("set_info: ignoring service info with empty properties")
            return
            
        valid_addresses = self._get_valid_addresses(info)
        if not valid_addresses:
            raise ValueError('no valid addresses')
        self._valid_addresses = valid_addresses
        self.si = info
        
        # Добавляем отладочное логирование
        self._logger.debug(f"set_info: received service info with properties: {info.properties}")
        if info.properties:
            for key, value in info.properties.items():
                key_str = key.decode('utf-8') if isinstance(key, bytes) else str(key)
                value_str = value.decode('utf-8') if isinstance(value, bytes) else str(value)
                self._logger.debug(f"set_info: property {key_str} = {value_str}")
        
        for f in self._listeners:
            try:
                f.device_updated()
            except Exception as exc:
                self._logger.error(f'set_info: error while calling device_updated on {f}')
                self._logger.exception(exc)

    def add_service(self, zc: zeroconf.Zeroconf, type_: str, name: str) -> None:
        self._add_update_service('add_service', zc, type_, name)

    def update_service(self, zc: zeroconf.Zeroconf, type_: str, name: str) -> None:
        self._add_update_service('update_service', zc, type_, name)

    def _add_update_service(self, method: str, zc: zeroconf.Zeroconf, type_: str, name: str) -> None:
        info = zc.get_service_info(type_, name)
        if name.startswith(f'{self._mac}.'):
            self._logger.info(f'{method}: type={type_} name={name}')
            
            # Игнорируем уведомления с пустыми свойствами
            if not info.properties:
                self._logger.debug(f'{method}: ignoring due to empty properties')
                return
                
            try:
                self.set_info(info)
            except ValueError as exc:
                self._logger.error(f'{method}: rejected: {str(exc)}')
        else:
            self._logger.debug(f'{method}: mac not matched: {info}')

    def remove_service(self, zc: zeroconf.Zeroconf, type_: str, name: str) -> None:
        if name.startswith(f'{self._mac}.'):
            self._logger.info(f'remove_service: type={type_} name={name}')
            # TODO what to do here?!

    def run(self):
        self._logger.debug('starting zeroconf service browser')
        
        # Only create Zeroconf instance if not provided
        if self._zc is None:
            ip_version = zeroconf.IPVersion.V4Only if self._only_ipv4 else zeroconf.IPVersion.All
            self._zc = zeroconf.Zeroconf(ip_version=ip_version)
        
        self._sb = zeroconf.ServiceBrowser(self._zc, "_syncleo._udp.local.", self)
        self._sb.join()

    def stop(self):
        if self._sb:
            try:
                self._sb.cancel()
            except RuntimeError:
                pass
            self._sb = None
        
        # Only close Zeroconf if we created it (not shared instance)
        if self._zc is not None and not self._use_shared_zeroconf:
            self._zc.close()
        self._zc = None

    def _get_valid_addresses(self, si: zeroconf.ServiceInfo) -> List[Union[IPv4Address, IPv6Address]]:
        """Get valid addresses from service info."""
        valid = []
        if not si.addresses:
            return valid
            
        for addr_bytes in si.addresses:
            try:
                addr = ip_address(addr_bytes)
                if self._only_ipv4 and not isinstance(addr, IPv4Address):
                    continue
                if isinstance(addr, IPv4Address) and str(addr).startswith('169.254.'):
                    continue
                valid.append(addr)
            except Exception as exc:
                self._logger.debug("Error processing address %s: %s", addr_bytes, exc)
        return valid

    @property
    def pubkey(self) -> bytes:
        """Get public key from service properties."""
        if not self.si or not self.si.properties:
            raise ValueError("No properties available")
        
        # Пробуем получить как bytes, если не получается - как строку
        pubkey_hex = self.si.properties.get(b'public')
        if pubkey_hex is None:
            # Пробуем получить как строку (если свойства уже декодированы)
            pubkey_hex_str = None
            for key, value in self.si.properties.items():
                key_str = key.decode('utf-8') if isinstance(key, bytes) else str(key)
                if key_str == 'public':
                    pubkey_hex_str = value.decode('utf-8') if isinstance(value, bytes) else str(value)
                    break
            
            if not pubkey_hex_str:
                raise ValueError("Public key not found in properties")
            
            try:
                return bytes.fromhex(pubkey_hex_str)
            except Exception as exc:
                raise ValueError(f"Invalid public key format: {exc}")
        else:
            try:
                return bytes.fromhex(pubkey_hex.decode('utf-8'))
            except Exception as exc:
                raise ValueError(f"Invalid public key format: {exc}")

    @property
    def curve(self) -> int:
        """Get curve type from service properties."""
        if not self.si or not self.si.properties:
            raise ValueError("No properties available")
        
        # Пробуем получить как bytes, если не получается - как строку
        curve_str = self.si.properties.get(b'curve')
        if curve_str is None:
            # Пробуем получить как строку (если свойства уже декодированы)
            curve_str_val = None
            for key, value in self.si.properties.items():
                key_str = key.decode('utf-8') if isinstance(key, bytes) else str(key)
                if key_str == 'curve':
                    curve_str_val = value.decode('utf-8') if isinstance(value, bytes) else str(value)
                    break
            
            if not curve_str_val:
                raise ValueError("Curve not found in properties")
            
            try:
                return int(curve_str_val)
            except Exception as exc:
                raise ValueError(f"Invalid curve format: {exc}")
        else:
            try:
                return int(curve_str.decode('utf-8'))
            except Exception as exc:
                raise ValueError(f"Invalid curve format: {exc}")

    @property
    def addr(self) -> Union[IPv4Address, IPv6Address]:
        return self._valid_addresses[0]

    @property
    def port(self) -> int:
        return int(self.si.port)

    @property
    def protocol(self) -> int:
        """Get protocol version from service properties."""
        if not self.si or not self.si.properties:
            raise ValueError("No properties available")
        
        # Пробуем получить как bytes, если не получается - как строку
        protocol_str = self.si.properties.get(b'protocol')
        if protocol_str is None:
            # Пробуем получить как строку (если свойства уже декодированы)
            protocol_str_val = None
            for key, value in self.si.properties.items():
                key_str = key.decode('utf-8') if isinstance(key, bytes) else str(key)
                if key_str == 'protocol':
                    protocol_str_val = value.decode('utf-8') if isinstance(value, bytes) else str(value)
                    break
            
            if not protocol_str_val:
                raise ValueError("Protocol not found in properties")
            
            try:
                return int(protocol_str_val)
            except Exception as exc:
                raise ValueError(f"Invalid protocol format: {exc}")
        else:
            try:
                return int(protocol_str.decode('utf-8'))
            except Exception as exc:
                raise ValueError(f"Invalid protocol format: {exc}")


class DeviceListener:
    @abstractmethod
    def device_updated(self):
        pass


class Kettle(DeviceListener, ConnectionStatusListener):
    mac: str
    device: Optional[DeviceDiscover]
    device_token: str
    conn: Optional[UDPConnection]
    conn_status: Optional[ConnectionStatus]
    _read_timeout: Optional[int]
    _logger: logging.Logger
    _find_evt: threading.Event

    def __init__(self, mac: str, device_token: str, read_timeout: Optional[int] = None):
        super().__init__()
        self.mac = mac
        self.device = None
        self.device_token = device_token
        self.conn = None
        self.conn_status = None
        self._read_timeout = read_timeout
        self._find_evt = threading.Event()
        self._logger = logging.getLogger(f'{__name__}.{self.__class__.__name__}[{mac}]')  # Добавляем MAC в логгер

    async def async_force_reconnect(self):
        """Force reconnection by completely stopping and recreating the connection."""
        self._logger.info("Forcing complete reconnection...")
        if self.conn:
            # Останавливаем соединение
            self.conn.stop_connection()
            # Ждем завершения
            await asyncio.sleep(0.5)
            # Проверяем, что поток завершился
            if self.conn.is_alive():
                self._logger.warning("Connection thread still alive after stop, forcing interruption")
                self.conn.interrupted = True
                self.conn.join(timeout=1.0)
            self.conn = None  # Важно: обнуляем соединение
            self._logger.info("Connection stopped and reset")
        else:
            self._logger.debug("No active connection to reconnect")

    # Синхронная версия для использования в executor
    def force_reconnect(self):
        """Synchronous version for executor."""
        self._logger.info("Forcing complete reconnection (sync)...")
        if self.conn:
            self.conn.stop_connection()
            # В синхронном контексте можно использовать time.sleep
            time.sleep(0.5)
            self.conn = None
        else:
            self._logger.debug("No active connection to reconnect")

    def device_updated(self):
        """Handle device updates from discovery."""
        self._find_evt.set()
        
        # Логируем обновление, но не форсируем переподключение здесь
        current_service_info = self.device.si
        _LOGGER.debug(f'device updated for MAC {self.mac}, port: {current_service_info.port if current_service_info else "unknown"}')
        
        # Проверяем что properties не пустые
        if (current_service_info and 
            hasattr(current_service_info, 'properties') and 
            current_service_info.properties):
            
            # Уведомляем координатор об обновлении устройства
            if hasattr(self, '_coordinator_listener'):
                try:
                    self._coordinator_listener.device_updated()
                except Exception as exc:
                    self._logger.error(f"Error notifying coordinator: {exc}")
        else:
            _LOGGER.debug("Device update ignored - empty properties")
    
    # Добавляем метод для установки слушателя координатора
    def set_coordinator_listener(self, listener):
        """Set coordinator listener for device updates."""
        self._coordinator_listener = listener

    def connection_status_updated(self, status: ConnectionStatus):
        self.conn_status = status

    def discover(self, wait=True, timeout=None, zeroconf_instance=None, discovered_device_info=None) -> Optional[zeroconf.ServiceInfo]:
        do_start = False
        
        # Если передана информация об обнаруженном устройстве, используем ее
        if discovered_device_info and not self.device:
            _LOGGER.info("Using pre-discovered device info for MAC: %s", self.mac)
            
            # Создаем DeviceDiscover
            self.device = DeviceDiscover(self.mac, listener=self, only_ipv4=True, zeroconf_instance=zeroconf_instance)
            
            # Создаем фиктивный ServiceInfo с правильными типами данных
            from zeroconf import ServiceInfo
            import socket
            
            # Конвертируем адреса в bytes
            addresses = []
            for ip in discovered_device_info['addresses']:
                try:
                    addr_bytes = socket.inet_pton(socket.AF_INET, ip)
                    addresses.append(addr_bytes)
                except OSError:
                    # Пропускаем невалидные адреса
                    continue
            
            # Создаем properties как bytes (как ожидает zeroconf)
            properties = {
                b'public': discovered_device_info['public_key'].encode('utf-8'),
                b'curve': discovered_device_info['curve'].encode('utf-8'),
                b'protocol': discovered_device_info['protocol'].encode('utf-8'),
                b'vendor': discovered_device_info['vendor'].encode('utf-8'),
                b'basetype': discovered_device_info['basetype'].encode('utf-8'),
                b'devtype': discovered_device_info['devtype'].encode('utf-8'),
                b'firmware': discovered_device_info['firmware'].encode('utf-8'),
            }
            
            # Используем строки для name и server
            service_info = ServiceInfo(
                type_="_syncleo._udp.local.",  # строка
                name=discovered_device_info['name'],  # строка
                addresses=addresses,  # список bytes
                port=discovered_device_info['port'],  # int
                properties=properties,  # dict с bytes ключами и значениями
                server=discovered_device_info['name'].split('.')[0] + '.local.'  # строка
            )
            
            # Устанавливаем информацию об устройстве
            self.device.set_info(service_info)
            self._find_evt.set()
            return service_info
        
        if not self.device:
            self.device = DeviceDiscover(self.mac, listener=self, only_ipv4=True, zeroconf_instance=zeroconf_instance)
            do_start = True
            self._logger.debug('discover: started device discovery')
        else:
            self._logger.warning('discover: already started')
    
        if do_start:
            self.device.start()
    
        if wait:
            self._find_evt.clear()
            try:
                # Ждем с таймаутом, но если свойства пустые, продолжаем ждать
                found_with_properties = False
                start_time = time.time()
                while not found_with_properties and (timeout is None or (time.time() - start_time < timeout)):
                    remaining_time = timeout - (time.time() - start_time) if timeout else None
                    if remaining_time and remaining_time <= 0:
                        break
                        
                    wait_time = min(1.0, remaining_time) if remaining_time else 1.0
                    self._find_evt.wait(timeout=wait_time)
                    if self.device.si and self.device.si.properties:
                        found_with_properties = True
                    else:
                        self._logger.debug("Discovered device has empty properties, continuing to wait...")
                        self._find_evt.clear()
                        
                if not found_with_properties:
                    self._logger.warning("Timeout waiting for device with properties")
                    return None
                    
            except KeyboardInterrupt:
                self.device.stop()
                return None
            return self.device.si

    def start_server_if_needed(self,
                               incoming_message_listener=None,
                               connection_status_listener=None):
        # Проверяем что device инициализирован
        if not self.device:
            self._logger.error("Device not initialized, cannot start server")
            return
            
        if not self.device.si:
            self._logger.error("Device service info not available, cannot start server")
            return
    
        # Если соединение существует, но поток не живой - пересоздаем
        if self.conn and not self.conn.is_alive():
            self._logger.info("Connection thread is dead, recreating...")
            self.conn = None
    
        if self.conn:
            self._logger.warning('start_server_if_needed: server is already started!')
            # Обновляем параметры существующего соединения
            self.conn.set_address(self.device.addr, self.device.port)
            self.conn.set_device_pubkey(self.device.pubkey)
            return
    
        # Проверяем что device имеет все необходимые свойства
        try:
            curve = self.device.curve
            protocol = self.device.protocol
            pubkey = self.device.pubkey
            self._logger.debug(f"Device properties - curve: {curve}, protocol: {protocol}, pubkey: {pubkey.hex()[:16]}...")
        except Exception as exc:
            self._logger.error(f"Missing device properties: {exc}")
            return
    
        assert self.device.curve == 29, f'curve type {self.device.curve} is not implemented'
        assert self.device.protocol == 2, f'protocol {self.device.protocol} is not supported'
    
        kw = {}
        if self._read_timeout is not None:
            kw['read_timeout'] = self._read_timeout
        
        # Создаем новое соединение
        self.conn = UDPConnection(addr=self.device.addr,
                                  port=self.device.port,
                                  device_pubkey=self.device.pubkey,
                                  device_token=bytes.fromhex(self.device_token), **kw)
        if incoming_message_listener:
            self.conn.add_incoming_message_listener(incoming_message_listener)
    
        self.conn.add_connection_status_listener(self)
        if connection_status_listener:
            self.conn.add_connection_status_listener(connection_status_listener)
    
        self.conn.start()
        self._logger.info("New UDP connection started")

    def stop_all(self):
        # when we stop server, we should also stop device discovering service
        if self.conn:
            self.conn.interrupted = True
            self.conn = None
        if self.device:
            self.device.stop()
            self.device = None

    def is_connected(self) -> bool:
        return self.conn is not None and self.conn_status == ConnectionStatus.CONNECTED

    def set_power(self, power_type: PowerType, callback: callable):
        if self.conn is None:
            self._logger.error("Cannot set power: not connected")
            callback(False)
            return
            
        message = ModeMessage(power_type)
        self.conn.enqueue_message(WrappedMessage(message, handler=callback, ack=True))

    def set_target_temperature(self, temp: int, callback: callable):
        if self.conn is None:
            self._logger.error("Cannot set temperature: not connected")
            callback(False)
            return
            
        message = TargetTemperatureMessage(temp)
        self.conn.enqueue_message(WrappedMessage(message, handler=callback, ack=True))
        

    def set_child_lock(self, enabled: bool, callback: callable):
        """Set child lock state."""
        _LOGGER.debug("ChildLockMessage: %s", enabled)
        if self.conn is None:
            self._logger.error("Cannot set child lock: not connected")
            callback(False)
            return
            
        message = ChildLockMessage(enabled)
        self.conn.enqueue_message(WrappedMessage(message, handler=callback, ack=True))

    def set_volume(self, enabled: bool, callback: callable):
        """Set volume state."""
        if self.conn is None:
            self._logger.error("Cannot set volume: not connected")
            callback(False)
            return
            
        message = VolumeMessage(enabled)
        self.conn.enqueue_message(WrappedMessage(message, handler=callback, ack=True))

    def set_backlight(self, enabled: bool, callback: callable):
        """Set backlight state."""
        if self.conn is None:
            self._logger.error("Cannot set backlight: not connected")
            callback(False)
            return
            
        message = BacklightMessage(enabled)
        self.conn.enqueue_message(WrappedMessage(message, handler=callback, ack=True))

    def set_night(self, enabled: bool, callback: callable):
        """Set night state."""
        if self.conn is None:
            self._logger.error("Cannot set night: not connected")
            callback(False)
            return
            
        message = NightMessage(enabled)
        self.conn.enqueue_message(WrappedMessage(message, handler=callback, ack=True))

    def set_color_night(self, r: int, g: int, b: int, w: int = 0, data_length: int = 4, callback: callable = None):
        """Set color night state with variable data length."""
        if self.conn is None:
            self._logger.error("Cannot set color night: not connected")
            if callback:
                callback(False)
            return
            
        message = ColorNightMessage(r, g, b, w, data_length)
        self.conn.enqueue_message(WrappedMessage(message, handler=callback, ack=True))