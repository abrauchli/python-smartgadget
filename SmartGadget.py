#!/usr/bin/env python3
"""
(c) Copyright 2020, Andreas Brauchli

Software and algorithms are provided "AS IS" and any and
all express or implied warranties are disclaimed.

THIS SOFTWARE IS PROVIDED BY SENSIRION "AS IS" AND ANY EXPRESS OR IMPLIED
WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED.  IN NO
EVENT SHALL SENSIRION BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT
OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING
IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY
OF SUCH DAMAGE.
"""

import asyncio
import logging
import time
import struct
from bleak import BleakClient, BleakScanner


def detection_callback(device, advertisement_data):
    print("{}: {} ({}) - advertisement_data: {}".format(device.address, device.name, device.rssi, advertisement_data))


async def scan():
    scanner = BleakScanner()
    scanner.register_detection_callback(detection_callback)

    await scanner.start()
    await asyncio.sleep(3.0)
    await scanner.stop()
    return await scanner.get_discovered_devices()


log = logging.getLogger(__name__)

SHTC1_NAME = 'SHTC1 smart gadget'
SHT3X_NAME = 'Smart Humigadget'
GADGET_NAMES = [SHTC1_NAME, SHT3X_NAME]


def filter_smartgadgets(devicelist):
    return [dev for dev in devicelist if dev.name in GADGET_NAMES]


def create_gadget(device, client=None, loop=None):
    """Factory method for creating a humigadget from a BLE Device"""
    if device.name == SHTC1_NAME:
        return SHTC1HumiGadget(device, client, loop)

    if device.name == SHT3X_NAME:
        return SHT3xHumiGadget(device, client, loop)

    return None


class Gadget:
    """
    Representation of a Sensirion BLE Gadget.

    Abstract Class with factory method `create'
    """
    RSSI_FORMAT = ['time', 'rssi']
    ADDRESS_TYPE = 'random'

    def __init__(self, device, client=None, loop=None, *args, **kwargs):
        self.device = device
        self.subscribable_services = set()
        self.subscribed_services = set()
        self._callbacks = []

        # self.device.get_rssi() is only available on MacOS at the time of
        # writing, so we keep it static
        rssi = (time.time(), self.device.rssi)
        self._rssi = dict(zip(self.RSSI_FORMAT, rssi))

        self._client = client or BleakClient(device)
        self._client.set_disconnected_callback(self._on_disconnected)

        self._handle_char_dict = None

        self._loop = loop or asyncio.get_event_loop()

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, type_cls, value, traceback):
        self.disconnect()

    def _on_disconnected(self, client):
        self._client = None
        self._handle_char_dict = None

    def _on_notify(self, handle, data):
        pass

    def connect(self):
        self._loop.run_until_complete(self._client.connect())
        services = self._loop.run_until_complete(self._client.get_services())
        self._handle_char_dict = services.characteristics

    def disconnect(self):
        if self._client:
            self._loop.run_until_complete(self._client.disconnect())

        self._client = None

    async def read_characteristic(self, uuid, unpack, zip_keys):
        """
        Read a characteristic by UUID, unpack values and zip the result

        :param unpack: can either be a format string to `struct.unpack' or a
                       callback that takes a data string and return an iterable

        :returns: a dict with zip_keys: {
                      'zip_keys[0]': timestamp,
                      'zip_keys[i]': unpacked_value[i]
                  } or None on error
        """
        try:
            data = await self._client.read_gatt_char(uuid)
            print("Read_characteristic ", uuid, unpack, data)

        except Exception:
            log.exception("Exception in read_characteristic: %s", uuid)
            return None

        if data is None:
            return None

        zip_vals = [time.time()]
        if isinstance(unpack, str):
            zip_vals.extend(struct.unpack(unpack, data))
        else:
            zip_vals.extend(unpack(data))
        return dict(zip(zip_keys, zip_vals))

    def write_characteristic(self, uuid, val):
        return self._loop.run_until_complete(self._client.write_gatt_char, uuid,
                                             val)

    def subscribe_service(self, uuid, callback):
        self._loop.run_until_complete(self._client.start_notify(uuid,
                                      callback))

    def unsubscribe_service(self, uuid):
        self._loop.run_until_complete(self._client.stop_notify(uuid))

    def subscribe(self, callback):
        """
        Subscribe to periodic measurement values from the gadget

        :param callback: is called at regular intervals with the same dict as
                         the expected property (e.g. from
                         `humidity_and_temperature` for a HumiGadget)
        """
        if callback not in self._callbacks:
            self._callbacks.append(callback)

        if len(self._callbacks) == 1:
            # start service subscription with the first bound callback
            for s in self.subscribable_services:
                try:
                    self.subscribe_service(s, self._on_notify)
                    self.subscribed_services.add(s)
                except Exception:
                    pass

    def unsubscribe(self, callback=None):
        """
        Unsubscribe from periodic measurement values from the gadget

        :param callback: Unsubscribe a specific callback or from all if callback
                         is `None`.
        """
        if not self._callbacks:
            return

        if callback:
            self._callbacks.append(callback)
        else:
            self._callbacks = []

        if not self.rht_callbacks:
            # unsubscribe service when releasing the last callback
            for s in self.subscribed_services:
                try:
                    self.unsubscribe_service(s)
                    self.subscribed_services.remove(s)
                except Exception:
                    log.exception("Error unsubscribing service %s", s)

    @property
    def address(self):
        return self.device.address

    @property
    def rssi(self):
        return self._rssi

    @property
    def connected(self):
        """
        Current connection state

        :returns: `True` if the gadget is connected, `False` otherwise
        """
        return self._client.is_connected()


class BatteryServiceMixin:
    BAT_SERV_UUID = '0000180f-0000-1000-8000-00805f9b34fb'
    BAT_CHAR_UUID = '00002a19-0000-1000-8000-00805f9b34fb'
    BAT_FORMAT = ['time', 'battery']

    @property
    def battery(self):
        """
        Get the last battery value

        :returns: a dict {
                      'time': timestamp,
                      'battery': battery_percentage
                  } or None on error
        """
        read_char = self.read_characteristic(self.BAT_CHAR_UUID, '<B',
                                             self.BAT_FORMAT)
        return self._loop.run_until_complete(read_char)


class HumidityServiceMixin:
    HUMI_SERV_UUID = '00001234-b38d-4985-720e-0f993a68ee41'
    HUMI_NOTI_UUID = '00001235-b38d-4985-720e-0f993a68ee41'
    HUMI_FORMAT = ['time', 'humidity']

    def __init__(self, *args, **kwargs):
        self.subscribable_services |= self.HUMI_NOTI_UUID

    @property
    def humidity(self):
        """
        Get the last temperature value

        :returns: a dict {
                      'time': timestamp,
                      'humidity': relative_humidity_in_percent,
                  } or None on error
        """
        read_char = self.read_characteristic(self.HUMI_NOTI_UUID, '<f',
                                             self.HUMI_FORMAT)
        return self._loop.run_until_complete(read_char)


class TemperatureServiceMixin:
    TEMP_SERV_UUID = '00002234-b38d-4985-720e-0f993a68ee41'
    TEMP_NOTI_UUID = '00002235-b38d-4985-720e-0f993a68ee41'
    TEMP_FORMAT = ['time', 'temperature']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.subscribable_services |= self.TEMP_NOTI_UUID

    @property
    def temperature(self):
        """
        Get the last temperature value

        :returns: a dict {
                      'time': timestamp,
                      'temperature': temperature_in_degC
                  } or None on error
        """
        read_char = self.read_characteristic(self.TEMP_NOTI_UUID, '<f',
                                             self.TEMP_FORMAT)
        return self._loop.run_until_complete(read_char)


class HumidityAndTemperatureServiceMixin:
    RHT_SERV_UUID = '0000aa20-0000-1000-8000-00805f9b34fb'
    RHT_CHAR_UUID = '0000aa21-0000-1000-8000-00805f9b34fb'
    RHT_FORMAT = ['time', 'temperature', 'humidity']

    def __init__(self, *args, **kwargs):
        self.subscribable_services |= self.RHT_CHAR_UUID

    @staticmethod
    def _rht_unpack_fixp(data):
        vals = struct.unpack('<hh', data)
        return (vals[0] / 100., vals[1] / 100.)

    @property
    def humidity_and_temperature(self):
        read_char = self.read_characteristic(self.RHT_CHAR_UUID,
                                             self._rht_unpack_fixp,
                                             self.RHT_FORMAT)
        return self._loop.run_until_complete(read_char)

    @property
    def temperature(self):
        return self.humidity_and_temperature

    @property
    def humidity(self):
        return self.humidity_and_temperature


class HumidityAndTemperatureVirtualServiceMixin:
    """ Provides a compatibility layer for the combined RHT readout when
    retrieving is only supported individually.
    Requires a base object hat implements both TemperatureServiceMixin and
    HumidityServiceMixin"""
    RHT_FORMAT = ['time', 'temperature', 'humidity']

    @property
    def humidity_and_temperature(self):
        """
        Get the last humidity and temperature values

        :returns: a dict {
                      'time': timestamp,
                      'humidity': relative_humidity_in_percent,
                      'temperature': temperature_in_degC
                  } or None on error
        """
        temp = self.temperature
        if temp is None:
            return None
        humi = self.humidity
        if humi is None:
            return None
        return dict(zip(self.RHT_FORMAT, (humi['time'], temp['temperature'],
                                          humi['humidity'])))


class LoggingServiceMixin:
    LOG_INTV_CHAR_UUID = '0000f239-b38d-4985-720e-0f993a68ee41'
    LOG_INTV_FORMAT = ['time', 'log_interval']

    @property
    def log_interval(self):
        """
        :returns: the logging and notification interval in ms
        """
        read_char = self.read_characteristic(self.LOG_INTV_CHAR_UUID, '<i',
                                             self.LOG_INTV_FORMAT)
        return self._loop.run_until_complete(read_char)

    @log_interval.setter
    def log_interval(self, interval_ms):
        """
        Sets the logger and notification interval in ms
        CAREFUL: THIS CLEARS THE CURRENT LOG ON THE GADGET
        """
        return self.write_characteristic(self.LOG_INTV_CHAR_UUID,
                                         bytearray(struct.pack('<i',
                                                               interval_ms)))


class SHT3xHumiGadget(Gadget, TemperatureServiceMixin, HumidityServiceMixin,
                      HumidityAndTemperatureVirtualServiceMixin,
                      LoggingServiceMixin, BatteryServiceMixin):
    """Representation of a Sensirion Smart SHT3x HumiGadget."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._current_rht = {}

    def _on_notify(self, handle, data):
        ts = time.time()
        val = struct.unpack('<f', data)[0]
        key = 'value'
        char_uuid = self._handle_char_dict[handle].uuid
        if char_uuid == self.TEMP_NOTI_UUID:
            key = 'temperature'
        elif char_uuid == self.HUMI_NOTI_UUID:
            key = 'humidity'
        self._current_rht['time'] = ts
        self._current_rht[key] = val
        if ('temperature' in self._current_rht and
                'humidity' in self._current_rht):
            for s in self.rht_callbacks:
                s(self._current_rht)
            self._current_rht = {}


class SHTC1HumiGadget(Gadget, HumidityAndTemperatureServiceMixin):
    """Representation of a Sensirion Smart SHTC1 HumiGadget."""

    ADDRESS_TYPE = 'public'

    def _on_notify(self, handle, data):
        vals = [time.time()]
        vals.extend(self._rht_unpack_fixp(data))
        cur_rht = dict(zip(self.RHT_FORMAT, vals))
        for s in self.rht_callbacks:
            s(cur_rht)


def main():
    loop = asyncio.get_event_loop()
    device_array = loop.run_until_complete(scan())
    devices = filter_smartgadgets(device_array)

    # # # Manually set devices
    # devices = [
    # #     {'Address': 'BC:6A:29:C1:B4:D1', 'Name': 'SHTC1'},
    # #    {'Address': 'DC:01:F6:33:D7:42', 'Name': 'Smart Humigadget'}
    # {'Name': 'Smart Humigadget', 'Paired': False, 'ServicesResolved':
    #          False, 'Adapter': '/org/bluez/hci0', 'Appearance': 512,
    #          'LegacyPairing': False, 'Alias': 'Smart Humigadget',
    #          'Connected': False, 'UUIDs':
    #          ['00001234-b38d-4985-720e-0f993a68ee41',
    #              '00001800-0000-1000-8000-00805f9b34fb',
    #              '00001801-0000-1000-8000-00805f9b34fb',
    #              '0000180a-0000-1000-8000-00805f9b34fb',
    #              '0000180f-0000-1000-8000-00805f9b34fb',
    #              '00002234-b38d-4985-720e-0f993a68ee41',
    #              '0000f234-b38d-4985-720e-0f993a68ee41'], 'Address':
    #          'DC:01:F6:33:D7:42', 'Trusted': False, 'Blocked': False}
    # ]

    for dev in devices:
        print("{}: {}".format(dev.name, dev.address))
        #try:
        with create_gadget(dev, loop=loop) as gadget:
            print("Connected ... rssi, bat, rht, log intv")
            print(gadget.rssi)
            print(gadget.battery)
            print(gadget.humidity_and_temperature)

            if isinstance(gadget, SHT3xHumiGadget):
                print(gadget.log_interval)

            # # print("Setting log interval (erases log)")
            # # gadget.log_interval = 5000
            print("Subscribing")
            gadget.subscribe(lambda rht: print(rht))
            time.sleep(10)

        #except KeyboardInterrupt:
        #    break
        #except Exception as e:
        #    print(e)
        #    raise e


if __name__ == '__main__':
    main()
