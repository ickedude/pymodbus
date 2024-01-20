"""Socket framer."""
# pylint: disable=missing-type-doc
import struct
from typing import Tuple

from pymodbus.exceptions import (
    InvalidMessageReceivedException,
    ModbusIOException,
)
from pymodbus.framer.base import SOCKET_FRAME_HEADER, ModbusFramer
from pymodbus.logging import Log


# --------------------------------------------------------------------------- #
# Modbus TCP Message
# --------------------------------------------------------------------------- #


class ModbusSocketFramer(ModbusFramer):
    """Modbus Socket Frame controller.

    Before each modbus TCP message is an MBAP header which is used as a
    message frame.  It allows us to easily separate messages as follows::

        [         MBAP Header         ] [ Function Code] [ Data ] \
        [ tid ][ pid ][ length ][ uid ]
          2b     2b     2b        1b           1b           Nb

        while len(message) > 0:
            tid, pid, length`, uid = struct.unpack(">HHHB", message)
            request = message[0:7 + length - 1`]
            message = [7 + length - 1:]

        * length = uid + function code + data
        * The -1 is to account for the uid byte
    """

    method = "socket"

    def __init__(self, decoder, **kwargs):
        """Initialize a new instance of the framer.

        :param decoder: The decoder factory implementation to use
        """
        super().__init__(decoder, **kwargs)
        self._hsize = 0x07
        self.transport = kwargs.get('transport')

    def _validate_slave_id(self, slaves: list, single: bool) -> bool:
        """Validate if the received data is valid for the client.

        :param slaves: list of slave id for which the transaction is valid
        :param single: Set to true to treat this as a single context
        :return:
        """
        if single: # match any
            return True
        if 0 in slaves: # broadcast
            return True
        if self.transport is None:
            return self._header["uid"] in slaves
        if (peer := self.transport.get_extra_info('peername')) is None:
            return self._header["uid"] in slaves
        slave_id: Tuple[str, int] = (peer[0], self._header["uid"])
        return slave_id in slaves

    # ----------------------------------------------------------------------- #
    # Private Helper Functions
    # ----------------------------------------------------------------------- #
    def checkFrame(self):
        """Check and decode the next frame.

        Return true if we were successful.
        """
        if not self.isFrameReady():
            return False
        (
            self._header["tid"],
            self._header["pid"],
            self._header["len"],
            self._header["uid"],
        ) = struct.unpack(">HHHB", self._buffer[0 : self._hsize])

        # someone sent us an error? ignore it
        if self._header["len"] < 2:
            self.advanceFrame()
        # we have at least a complete message, continue
        elif len(self._buffer) - self._hsize + 1 >= self._header["len"]:
            return True
        # we don't have enough of a message yet, wait
        return False

    def advanceFrame(self):
        """Skip over the current framed message.

        This allows us to skip over the current message after we have processed
        it or determined that it contains an error. It also has to reset the
        current frame header handle
        """
        length = self._hsize + self._header["len"]
        self._buffer = self._buffer[length:]
        self._header = {"tid": 0, "pid": 0, "len": 0, "uid": 0}

    def isFrameReady(self):
        """Check if we should continue decode logic.

        This is meant to be used in a while loop in the decoding phase to let
        the decoder factory know that there is still data in the buffer.

        :returns: True if ready, False otherwise
        """
        return len(self._buffer) > self._hsize

    def getFrame(self):
        """Return the next frame from the buffered data.

        :returns: The next full frame buffer
        """
        length = self._hsize + self._header["len"]
        return self._buffer[self._hsize : length]

    # ----------------------------------------------------------------------- #
    # Public Member Functions
    # ----------------------------------------------------------------------- #
    def decode_data(self, data):
        """Decode data."""
        if len(data) > self._hsize:
            tid, pid, length, uid, fcode = struct.unpack(
                SOCKET_FRAME_HEADER, data[0 : self._hsize + 1]
            )
            return {
                "tid": tid,
                "pid": pid,
                "length": length,
                "slave": uid,
                "fcode": fcode,
            }
        return {}

    def frameProcessIncomingPacket(self, single, callback, slave, tid=None, **kwargs):
        """Process new packet pattern.

        This takes in a new request packet, adds it to the current
        packet stream, and performs framing on it. That is, checks
        for complete messages, and once found, will process all that
        exist.  This handles the case when we read N + 1 or 1 // N
        messages at a time instead of 1.

        The processed and decoded messages are pushed to the callback
        function to process and send.
        """
        if not self.checkFrame():
            Log.debug("Frame check failed, ignoring!!")
            return
        if not self._validate_slave_id(slave, single):
            header_txt = self._header["uid"]
            Log.debug("Not a valid slave id - {}, ignoring!!", header_txt)
            self.resetFrame()
            return
        self._process(callback, tid)

    def _process(self, callback, tid, error=False):
        """Process incoming packets irrespective error condition."""
        data = self._buffer if error else self.getFrame()
        if (result := self.decoder.decode(data)) is None:
            self.resetFrame()
            raise ModbusIOException("Unable to decode request")
        if error and result.function_code < 0x80:
            raise InvalidMessageReceivedException(result)
        self.populateResult(result)
        self.advanceFrame()
        if tid and tid != result.transaction_id:
            self.resetFrame()
        else:
            callback(result)  # defer or push to a thread?

    def buildPacket(self, message):
        """Create a ready to send modbus packet.

        :param message: The populated request/response to send
        """
        data = message.encode()
        packet = struct.pack(
            SOCKET_FRAME_HEADER,
            message.transaction_id,
            message.protocol_id,
            len(data) + 2,
            message.slave_id,
            message.function_code,
        )
        packet += data
        return packet


# __END__
