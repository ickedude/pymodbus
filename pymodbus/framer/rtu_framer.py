"""RTU framer."""
# pylint: disable=missing-type-doc
import struct
import time

from pymodbus.exceptions import (
    InvalidMessageReceivedException,
    ModbusIOException,
)
from pymodbus.framer.base import BYTE_ORDER, FRAME_HEADER, ModbusFramer
from pymodbus.logging import Log
from pymodbus.utilities import ModbusTransactionState, checkCRC, computeCRC


RTU_FRAME_HEADER = BYTE_ORDER + FRAME_HEADER


# --------------------------------------------------------------------------- #
# Modbus RTU Message
# --------------------------------------------------------------------------- #
class ModbusRtuFramer(ModbusFramer):
    """Modbus RTU Frame controller.

        [ Start Wait ] [Address ][ Function Code] [ Data ][ CRC ][  End Wait  ]
          3.5 chars     1b         1b               Nb      2b      3.5 chars

    Wait refers to the amount of time required to transmit at least x many
    characters.  In this case it is 3.5 characters.  Also, if we receive a
    wait of 1.5 characters at any point, we must trigger an error message.
    Also, it appears as though this message is little endian. The logic is
    simplified as the following::

        block-on-read:
            read until 3.5 delay
            check for errors
            decode

    The following table is a listing of the baud wait times for the specified
    baud rates::

        ------------------------------------------------------------------
         Baud  1.5c (18 bits)   3.5c (38 bits)
        ------------------------------------------------------------------
         1200   13333.3 us       31666.7 us
         4800    3333.3 us        7916.7 us
         9600    1666.7 us        3958.3 us
        19200     833.3 us        1979.2 us
        38400     416.7 us         989.6 us
        ------------------------------------------------------------------
        1 Byte = start + 8 bits + parity + stop = 11 bits
        (1/Baud)(bits) = delay seconds
    """

    method = "rtu"

    def __init__(self, decoder, **kwargs):
        """Initialize a new instance of the framer.

        :param decoder: The decoder factory implementation to use
        """
        super().__init__(decoder, **kwargs)
        self._hsize = 0x01
        self._end = b"\x0d\x0a"
        self._min_frame_size = 4
        self.function_codes = decoder.lookup.keys() if decoder else {}

    # ----------------------------------------------------------------------- #
    # Private Helper Functions
    # ----------------------------------------------------------------------- #
    def decode_data(self, data):
        """Decode data."""
        if len(data) > self._hsize:
            uid = int(data[0])
            fcode = int(data[1])
            return {"slave": uid, "fcode": fcode}
        return {}

    def checkFrame(self):
        """Check if the next frame is available.

        Return True if we were successful.

        1. Populate header
        2. Discard frame if UID does not match
        """
        try:
            self.populateHeader()
            frame_size = self._header["len"]
            data = self._buffer[: frame_size - 2]
            crc = self._header["crc"]
            crc_val = (int(crc[0]) << 8) + int(crc[1])
            return checkCRC(data, crc_val)
        except (IndexError, KeyError, struct.error):
            return False

    def advanceFrame(self):
        """Skip over the current framed message.

        This allows us to skip over the current message after we have processed
        it or determined that it contains an error. It also has to reset the
        current frame header handle
        """
        self._buffer = self._buffer[self._header["len"] :]
        Log.debug("Frame advanced, resetting header!!")
        self._header = {"uid": 0x00, "len": 0, "crc": b"\x00\x00"}

    def resetFrame(self):
        """Reset the entire message frame.

        This allows us to skip over errors that may be in the stream.
        It is hard to know if we are simply out of sync or if there is
        an error in the stream as we have no way to check the start or
        end of the message (python just doesn't have the resolution to
        check for millisecond delays).
        """
        x = self._buffer
        super().resetFrame()
        self._buffer = x

    def isFrameReady(self):
        """Check if we should continue decode logic.

        This is meant to be used in a while loop in the decoding phase to let
        the decoder know that there is still data in the buffer.

        :returns: True if ready, False otherwise
        """
        size = self._header.get("len", 0)
        if not size and len(self._buffer) > self._hsize:
            try:
                # Frame is ready only if populateHeader() successfully
                # populates crc field which finishes RTU frame otherwise,
                # if buffer is not yet long enough, populateHeader() raises IndexError
                size = self.populateHeader()
            except IndexError:
                return False

        return len(self._buffer) >= size if size > 0 else False

    def populateHeader(self, data=None):
        """Try to set the headers `uid`, `len` and `crc`.

        This method examines `self._buffer` and writes meta
        information into `self._header`.

        Beware that this method will raise an IndexError if
        `self._buffer` is not yet long enough.
        """
        data = data if data is not None else self._buffer
        self._header["uid"] = int(data[0])
        self._header["tid"] = int(data[0])
        size = self.get_expected_response_length(data)
        self._header["len"] = size

        if len(data) < size:
            # crc yet not available
            raise IndexError
        self._header["crc"] = data[size - 2 : size]
        return size

    def getFrame(self):
        """Get the next frame from the buffer.

        :returns: The frame data or ""
        """
        start = self._hsize
        end = self._header["len"] - 2
        buffer = self._buffer[start:end]
        if end > 0:
            Log.debug("Getting Frame - {}", buffer, ":hex")
            return buffer
        return b""

    def populateResult(self, result):
        """Populate the modbus result header.

        The serial packets do not have any header information
        that is copied.

        :param result: The response packet
        """
        result.slave_id = self._header["uid"]
        result.transaction_id = self._header["tid"]

    def getFrameStart(self, slaves, broadcast, skip_cur_frame):
        """Scan buffer for a relevant frame start."""
        start = 1 if skip_cur_frame else 0
        if (buf_len := len(self._buffer)) < 4:
            return False
        for i in range(start, buf_len - 3):  # <slave id><function code><crc 2 bytes>
            if not broadcast and self._buffer[i] not in slaves:
                continue
            if (
                self._buffer[i + 1] not in self.function_codes
                and (self._buffer[i + 1] - 0x80) not in self.function_codes
            ):
                continue
            if i:
                self._buffer = self._buffer[i:]  # remove preceding trash.
            return True
        if buf_len > 3:
            self._buffer = self._buffer[-3:]
        return False

    # ----------------------------------------------------------------------- #
    # Public Member Functions
    # ----------------------------------------------------------------------- #
    def frameProcessIncomingPacket(self, single, callback, slave, _tid=None, **kwargs):
        """Process new packet pattern."""
        broadcast = not slave[0]
        skip_cur_frame = False
        while self.getFrameStart(slave, broadcast, skip_cur_frame):
            if not self.isFrameReady():
                Log.debug("Frame - not ready")
                break
            if not self.checkFrame():
                Log.debug("Frame check failed, ignoring!!")
                self.resetFrame()
                skip_cur_frame = True
                continue
            if not self._validate_slave_id(slave, single):
                header_txt = self._header["uid"]
                Log.debug("Not a valid slave id - {}, ignoring!!", header_txt)
                self.resetFrame()
                skip_cur_frame = True
                continue
            self._process(callback)

    def buildPacket(self, message):
        """Create a ready to send modbus packet.

        :param message: The populated request/response to send
        """
        data = message.encode()
        packet = (
            struct.pack(RTU_FRAME_HEADER, message.slave_id, message.function_code)
            + data
        )
        packet += struct.pack(">H", computeCRC(packet))
        # Ensure that transaction is actually the slave id for serial comms
        message.transaction_id = message.slave_id
        return packet

    def sendPacket(self, message):
        """Send packets on the bus with 3.5char delay between frames.

        :param message: Message to be sent over the bus
        :return:
        """
        super().resetFrame()
        start = time.time()
        timeout = start + self.client.comm_params.timeout_connect
        while self.client.state != ModbusTransactionState.IDLE:
            if self.client.state == ModbusTransactionState.TRANSACTION_COMPLETE:
                timestamp = round(time.time(), 6)
                Log.debug(
                    "Changing state to IDLE - Last Frame End - {} Current Time stamp - {}",
                    self.client.last_frame_end,
                    timestamp,
                )
                if self.client.last_frame_end:
                    idle_time = self.client.idle_time()
                    if round(timestamp - idle_time, 6) <= self.client.silent_interval:
                        Log.debug(
                            "Waiting for 3.5 char before next send - {} ms",
                            self.client.silent_interval * 1000,
                        )
                        time.sleep(self.client.silent_interval)
                else:
                    # Recovering from last error ??
                    time.sleep(self.client.silent_interval)
                self.client.state = ModbusTransactionState.IDLE
            elif self.client.state == ModbusTransactionState.RETRYING:
                # Simple lets settle down!!!
                # To check for higher baudrates
                time.sleep(self.client.comm_params.timeout_connect)
                break
            elif time.time() > timeout:
                Log.debug(
                    "Spent more time than the read time out, "
                    "resetting the transaction to IDLE"
                )
                self.client.state = ModbusTransactionState.IDLE
            else:
                Log.debug("Sleeping")
                time.sleep(self.client.silent_interval)
        size = self.client.send(message)
        self.client.last_frame_end = round(time.time(), 6)
        return size

    def recvPacket(self, size):
        """Receive packet from the bus with specified len.

        :param size: Number of bytes to read
        :return:
        """
        result = self.client.recv(size)
        self.client.last_frame_end = round(time.time(), 6)
        return result

    def _process(self, callback, error=False):
        """Process incoming packets irrespective error condition."""
        data = self._buffer if error else self.getFrame()
        if (result := self.decoder.decode(data)) is None:
            raise ModbusIOException("Unable to decode request")
        if error and result.function_code < 0x80:
            raise InvalidMessageReceivedException(result)
        self.populateResult(result)
        self.advanceFrame()
        callback(result)  # defer or push to a thread?

    def get_expected_response_length(self, data):
        """Get the expected response length.

        :param data: Message data read so far
        :raises IndexError: If not enough data to read byte count
        :return: Total frame size
        """
        func_code = int(data[1])
        pdu_class = self.decoder.lookupPduClass(func_code)
        return pdu_class.calculateRtuFrameSize(data)


# __END__
