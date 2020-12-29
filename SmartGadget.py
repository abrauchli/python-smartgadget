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


class HumiGadget:
    """
    Representation of a Sensirion SmartHumiGadget.

    Abstract Class with factory method `create'
    """

    RSSI_FORMAT = ['time', 'rssi']
    HUMI_FORMAT = ['time', 'humidity']
    TEMP_FORMAT = ['time', 'temperature']
    RHT_FORMAT = ['time', 'temperature', 'humidity']
    BAT_FORMAT = ['time', 'battery']
    SHTC1_NAME = 'SHTC1 smart gadget'
    SHT3X_NAME = 'Smart Humigadget'
    GADGET_NAMES = [SHTC1_NAME, SHT3X_NAME]

    BAT_SRVC_UUID = '0000180f-0000-1000-8000-00805f9b34fb'
    BAT_CHAR_UUID = '00002a19-0000-1000-8000-00805f9b34fb'
    ADDRESS_TYPE = 'random'

    @staticmethod
    def filter_smartgadgets(devicelist):
        return [dev for dev in devicelist
                if dev.name in HumiGadget.GADGET_NAMES]

    @staticmethod
    def create(device, client=None, loop=None):
        """Factory method for creating a humigadget from a BLE Device"""
        if device.name == HumiGadget.SHTC1_NAME:
            return SHTC1HumiGadget(device, client, loop)

        if device.name == HumiGadget.SHT3X_NAME:
            return SHT3xHumiGadget(device, client, loop)

        return None

    def __init__(self, device, client=None, loop=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.device = device

        # self.device.get_rssi() is only available on MacOS at the time of
        # writing, so we keep it static
        rssi = (time.time(), self.device.rssi)
        self._rssi = dict(zip(self.RSSI_FORMAT, rssi))

        self._client = client or BleakClient(device)
        self._client.set_disconnected_callback(self._disconnected_client)

        self._loop = loop or asyncio.get_event_loop()

        self.rht_callbacks = []

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, type_cls, value, traceback):
        self.disconnect()

    def _disconnected_client(self, client):
        self._client = None

    def connect(self):
        return self._loop.run_until_complete(self._client.connect())

    def disconnect(self):
        if self._client:
            if self.rht_callbacks:
                self.unsubscribe()

            return self._loop.run_until_complete(self._client.disconnect())

        self._client = None
        self.rht_callbacks = []

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
        return self._con.char_write(uuid, val)

    def subscribe_service(self, uuid, callback):
        self._con.subscribe(uuid, callback)

    def unsubscribe_service(self, uuid):
        self._con.unsubscribe(uuid)

    def subscribe(self, callback):
        """
        Subscribe to periodic RHT values from the gadget

        :param callback: is called at regular intervals with the same dict as
                         from humidity_and_temperature.
        """
        raise NotImplementedError()

    def unsubscribe(self, callback=None):
        """
        Unsubscribe from periodic RHT values from the gadget

        :param callback: Unsubscribe a specific callback or all if callback is
                         None.
        """
        raise NotImplementedError()

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

        :returns: True if the gadget is connected, False otherwise
        """
        return self._client.is_connected()

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

    @property
    def temperature(self):
        """
        Get the last temperature value

        :returns: a dict {
                      'time': timestamp,
                      'temperature': temperature_in_degC
                  } or None on error
        """
        raise NotImplementedError()

    @property
    def humidity(self):
        """
        Get the last temperature value

        :returns: a dict {
                      'time': timestamp,
                      'humidity': relative_humidity_in_percent,
                  } or None on error
        """
        raise NotImplementedError()

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


class SHT3xHumiGadget(HumiGadget):
    """Representation of a Sensirion Smart SHT3x HumiGadget."""

    TEMP_SRVC_UUID = '00002234-b38d-4985-720e-0f993a68ee41'
    HUMI_SRVC_UUID = '00001234-b38d-4985-720e-0f993a68ee41'
    TEMP_NOTI_UUID = '00002235-b38d-4985-720e-0f993a68ee41'
    HUMI_NOTI_UUID = '00001235-b38d-4985-720e-0f993a68ee41'
    LOG_INTV_CHAR_UUID = '0000f239-b38d-4985-720e-0f993a68ee41'
    LOG_INTV_FORMAT = ['time', 'log_interval']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._current_rht = {}

    @property
    def temperature(self):
        read_char = self.read_characteristic(self.TEMP_NOTI_UUID, '<f',
                                             self.TEMP_FORMAT)
        return self._loop.run_until_complete(read_char)

    @property
    def humidity(self):
        read_char = self.read_characteristic(self.HUMI_NOTI_UUID, '<f',
                                             self.HUMI_FORMAT)
        return self._loop.run_until_complete(read_char)

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

    def _on_propchange(self, iface, changed, invalidated,
                       service=None, uuid=None):
        if 'Value' not in changed:
            return
        ts = time.time()
        data = bytearray(changed['Value'])
        val = struct.unpack('<f', data)[0]
        key = 'value'
        if uuid == self.TEMP_NOTI_UUID:
            key = 'temperature'
        elif uuid == self.HUMI_NOTI_UUID:
            key = 'humidity'
        self._current_rht['time'] = ts
        self._current_rht[key] = val
        if ('temperature' in self._current_rht and
                'humidity' in self._current_rht):
            for s in self.rht_callbacks:
                s(self._current_rht)
            self._current_rht = {}

    def subscribe(self, callback):
        if callback not in self.rht_callbacks:
            self.rht_callbacks.append(callback)

        if len(self.rht_callbacks) == 1:
            # start service subscription with the first bound callback
            self._current_rht = {}
            self.subscribe_service(self.TEMP_NOTI_UUID, self._on_propchange)
            self.subscribe_service(self.HUMI_NOTI_UUID, self._on_propchange)

    def unsubscribe(self, callback=None):
        if not self.rht_callbacks:
            return

        if callback:
            self.rht_callbacks.remove(callback)
        else:
            self.rht_callbacks = []

        if not self.rht_callbacks:
            # unsubscribe service when releasing the last callback
            self.unsubscribe_service(self.TEMP_NOTI_UUID)
            self.unsubscribe_service(self.HUMI_NOTI_UUID)


class SHTC1HumiGadget(HumiGadget):
    """Representation of a Sensirion Smart SHTC1 HumiGadget."""

    RHT_SRVC_UUID = '0000aa20-0000-1000-8000-00805f9b34fb'
    RHT_CHAR_UUID = '0000aa21-0000-1000-8000-00805f9b34fb'
    ADDRESS_TYPE = 'public'

    @staticmethod
    def _unpack_fixp(data):
        vals = struct.unpack('<hh', data)
        return (vals[0] / 100., vals[1] / 100.)

    @property
    def humidity_and_temperature(self):
        read_char = self.read_characteristic(self.RHT_CHAR_UUID,
                                             self._unpack_fixp,
                                             self.RHT_FORMAT)
        return self._loop.run_until_complete(read_char)

    @property
    def temperature(self):
        return self.humidity_and_temperature

    @property
    def humidity(self):
        return self.humidity_and_temperature

    def _on_rht_value(self, handle, data):
        vals = [time.time()]
        vals.extend(self._unpack_fixp(data))
        cur_rht = dict(zip(self.RHT_FORMAT, vals))
        for s in self.rht_callbacks:
            s(cur_rht)

    def subscribe(self, callback):
        if callback not in self.rht_callbacks:
            self.rht_callbacks.append(callback)

        if len(self.rht_callbacks) == 1:
            # start service subscription with the first bound callback
            self.subscribe_service(self.RHT_CHAR_UUID, self._on_rht_value)

    def unsubscribe(self, callback=None):
        if not self.rht_callbacks:
            return

        if callback:
            self.rht_callbacks.remove(callback)
        else:
            self.rht_callbacks = []

        if not self.rht_callbacks:
            # unsubscribe service when releasing the last callback
            self.unsubscribe_service(self.RHT_CHAR_UUID)


def main():
    loop = asyncio.get_event_loop()
    device_array = loop.run_until_complete(scan())
    devices = HumiGadget.filter_smartgadgets(device_array)

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
        with HumiGadget.create(dev, loop=loop) as gadget:
            print("Connected ... rssi, bat, rht, log intv")
            print(gadget.rssi)
            print(gadget.battery)
            print(gadget.humidity_and_temperature)

            if isinstance(gadget, SHT3xHumiGadget):
                print(gadget.log_interval)

            # # print("Setting log interval (erases log)")
            # # gadget.log_interval = 5000
            # print("Subscribing")
            # gadget.subscribe(lambda rht: print(rht))
            # time.sleep(70)

        #except KeyboardInterrupt:
        #    break
        #except Exception as e:
        #    print(e)
        #    raise e


if __name__ == '__main__':
    main()
