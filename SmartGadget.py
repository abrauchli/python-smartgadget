#!/usr/bin/env python2.7
# -*- coding: utf-8 -*-
"""
(c) Copyright 2016 Sensirion AG, Switzerland

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

Written by Andreas Brauchli <andreas.brauchli@sensirion.com> based
on code by Kristian Baumann <kristian.baumann@sensirion.com>
"""

from __future__ import print_function

import struct
import time

import pygatt


class BLEAdapter(object):
    """
    Adapter for PyGatt default initialization and device scan.

    Usage: automatic resource management
    >>> with BLEAdapter() as ble: ...

    or manually managed with

    >>> ble = BLEAdapter()
    >>> ble.start()
    >>> # ...
    >>> ble.stop()
    """
    config = {
        'run_as_root'       : False,
        'reset_on_start'    : False,
        'cli_options'       : None,
        'address_type'      : 'random', # default 'public'
        'hci_device'        : 'hci0',
        'scan_duration_sec' : 2.0,
        'pygatt_debug'      : True,
    }

    def __init__(self, **kwargs):
        self.adapter = None

        for k in self.config.keys():
            if k in kwargs:
                self.config[k] = kwargs[k]

        if self.config['pygatt_debug']:
            import logging
            logging.basicConfig()
            logging.getLogger(pygatt.__name__).setLevel(logging.DEBUG)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, type_cls, value, traceback):
        self.stop()

    def start(self):
        """
        Start the adapter (interactive gatttool session).
        Start should only be called when creating the object wihout automatic
        resource control (i.e. when _not_ using the

        >>> with BLEAdapater() as ble:

        statement.)
        """
        self.adapter = pygatt.BluezBackend(
                hci_device=self.config['hci_device'])
        self.adapter.start()

    def stop(self):
        if self.adapter:
            self.adapter.stop()

    def connect(self, address, address_type=None, timeout=10):
        """
        Connect to device (synchroneous)

        :param address: device address as string e.g. '12:34:56:78:9A'
        :param address_type: None (default), 'public' or 'random'
        :param timeout: connection timeout in sec (Default 10)
        """
        if address_type is None:
            address_type = self.config['address_type']
        return self.adapter.connect(address, timeout=timeout,
                                    address_type=address_type)

    def disconnect(self, address=None):
        """
        Disconnect from device (asynchroneous)

        :param address: device address as string e.g. '12:34:56:78:9A' or None
                        to disconnect from all devices.
        """
        self.adapter.disconnect()

    def scan(self, duration=-1):
        """
        Start device discovery scan (synchroneous)

        :param duration: scan duration in sec (Default -1: use default duration)
        :returns: a list of (address, name) tuples
        """
        if duration == -1:
            duration = self.config['scan_duration_sec']
        return self.adapter.scan(timeout=duration)


class HumiGadget(object):
    """
    Representation of a Sensirion SmartHumiGadget.

    Abstract Class with factory method `create'
    """

    RSSI_FORMAT = ['time', 'rssi']
    HUMI_FORMAT = ['time', 'humidity']
    TEMP_FORMAT = ['time', 'temperature']
    RHT_FORMAT  = ['time', 'temperature', 'humidity']
    BAT_FORMAT  = ['time', 'battery']
    SHTC1_NAME = 'SHTC1 smart gadget'
    SHT3X_NAME = 'Smart Humigadget'
    GADGET_NAMES = [SHTC1_NAME, SHT3X_NAME]

    BAT_SRVC_UUID = '0000180f-0000-1000-8000-00805f9b34fb'
    BAT_CHAR_UUID = '00002a19-0000-1000-8000-00805f9b34fb'
    ADDRESS_TYPE = 'random'

    @staticmethod
    def filter_smartgadgets(devicelist):
        return [dev for dev in devicelist
                if 'Name' in dev and dev['Name'] in HumiGadget.GADGET_NAMES]

    @staticmethod
    def create(device, ble=None):
        if device['Name'] == HumiGadget.SHTC1_NAME:
            return SHTC1HumiGadget(device['Address'], ble)
        if device['Name'] == HumiGadget.SHT3X_NAME:
            return SHT3xHumiGadget(device['Address'], ble)
        return None

    def __init__(self, address, ble=None, *args, **kwargs):
        self.address = address
        self.rht_callbacks = []
        if ble is None:
            ble = BLEAdapter()
            ble.start()
        self.ble = ble
        self._con = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, type_cls, value, traceback):
        self.disconnect()

    def connect(self):
        if not self._con:
            self.rht_callbacks = []
            self._con = ble.connect(self.address, address_type=self.ADDRESS_TYPE)

    def disconnect(self):
        if self._con:
            if self.rht_callbacks:
                self.unsubscribe()
            self._con.disconnect()
        self._con = None
        self.rht_callbacks = []

    def read_characteristic(self, uuid, unpack, zip_keys):
        """
        Read a characteristic by UUID, unpack values and zip the result.

        :param unpack: can either be a format string to `struct.unpack' or a
                       callback that takes a data string and return an iterable.

        :returns: a dict with zip_keys: {
                      'zip_keys[0]': timestamp,
                      'zip_keys[i]': unpacked_value[i]
                  } or None on error
        """
        data = self._con.char_read(uuid, timeout=10)
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

    # @property
    # def rssi(self):
    #     # PyGatt raises NotImplementedException
    #     rssi = (time.time(), self._con.get_rssi())
    #     return dict(zip(self.RRSI_FORMAT, rssi))

    @property
    def connected(self):
        """
        Current connection state

        :returns: True if the gadget is connected, False otherwise
        """
        return bool(self._con)

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
        return dict(zip(self.RHT_FORMAT, (humi['time'], humi['humidity'],
                                          temp['temperature'])))

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
        return self.read_characteristic(self.BAT_CHAR_UUID, '<B',
                                        self.BAT_FORMAT)


class SHT3xHumiGadget(HumiGadget):
    """Representation of a Sensirion Smart SHT3x HumiGadget."""

    TEMP_SRVC_UUID = '00002234-b38d-4985-720e-0f993a68ee41'
    HUMI_SRVC_UUID = '00001234-b38d-4985-720e-0f993a68ee41'
    TEMP_NOTI_UUID = '00002235-b38d-4985-720e-0f993a68ee41'
    HUMI_NOTI_UUID = '00001235-b38d-4985-720e-0f993a68ee41'
    LOG_INTV_CHAR_UUID = '0000f239-b38d-4985-720e-0f993a68ee41'
    LOG_INTV_FORMAT = ['time', 'log_interval']

    def __init__(self, *args, **kwargs):
        super(SHT3xHumiGadget, self).__init__(*args, **kwargs)
        self._current_rht = {}

    @property
    def temperature(self):
        return self.read_characteristic(self.TEMP_NOTI_UUID, '<f',
                                        self.TEMP_FORMAT)

    @property
    def humidity(self):
        return self.read_characteristic(self.HUMI_NOTI_UUID, '<f',
                                        self.HUMI_FORMAT)

    @property
    def log_interval(self):
        """
        :returns: the logging and notification interval in ms
        """
        return self.read_characteristic(self.LOG_INTV_CHAR_UUID, '<i',
                                        self.LOG_INTV_FORMAT)

    @log_interval.setter
    def log_interval(self, interval_ms):
        """
        Sets the logger and notification interval in ms
        CAREFUL: THIS CLEARS THE CURRENT LOG ON THE GADGET
        """
        return self.write_characteristic(self.LOG_INTV_CHAR_UUID,
                                         bytearray(struct.pack('<i',
                                                               interval_ms)))

    def _on_float_value(self, handle, data):
        ts = time.time()
        val = struct.unpack('<f', data)[0]
        key = 'value'
        if handle == self._con.get_handle(self.TEMP_SRVC_UUID):
            key = 'temperature'
        elif handle == self._con.get_handle(self.HUMI_SRVC_UUID):
            key = 'humidity'
        self._current_rht['time'] = ts
        self._current_rht[key] = val
        if ('temperature' in self._current_rht and
                'humidity' in self._current_rhty):
            for s in self.rht_callbacks:
                s(self._current_rht)
            self._current_rht = {}

    def subscribe(self, callback):
        if callback not in self.rht_callbacks:
            self.rht_callbacks.append(callback)

        if len(self.rht_callbacks) == 1:
            # start service subscription with the first bound callback
            self._current_rht = {}
            self.subscribe_service(self.TEMP_NOTI_UUID, self._on_float_value)
            self.subscribe_service(self.HUMI_NOTI_UUID, self._on_float_value)

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
        return self.read_characteristic(self.RHT_CHAR_UUID, self._unpack_fixp,
                                        self.RHT_FORMAT)

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


if __name__ == '__main__':
    # General usage Tips:
    # Run this command to allow LE scan as unprivileged user
    # sudo setcap 'cap_net_raw,cap_net_admin+eip' `which hcitool`
    # When running into scanning issues or connection timeouts try restarting
    # the linux bluetooth service:
    # sudo /etc/init.d/bluetooth restart # for sysv
    # sudo service bluetooth restart     # for upstart (Ubuntu < 16.04)
    # sudo systemctl restart bluetooth   # for systemd (Ubuntu >= 16.04)
    #
    with BLEAdapter() as ble:
        # Scan devices
        devices = HumiGadget.filter_smartgadgets(ble.scan())
        print(devices)

        # # Manually set devices
        # devices = [
        #     {'address': 'BC:6A:29:C1:B4:D1', 'name': 'SHTC1'},
        #     {'address': 'DC:01:F6:33:D7:42', 'name': 'Smart'}
        # ]
        for dev in devices:
            with HumiGadget.create(dev, ble) as gadget:
                print(gadget.battery)
                print(gadget.humidity_and_temperature)
                if isinstance(gadget, SHT3xHumiGadget):
                    print(gadget.log_interval)
                gadget.subscribe(lambda rht: print(rht))
                time.sleep(20)
