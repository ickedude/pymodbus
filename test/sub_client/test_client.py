"""Test client sync."""
import asyncio
import socket
import ssl
from unittest import mock

import pytest

import pymodbus.bit_read_message as pdu_bit_read
import pymodbus.bit_write_message as pdu_bit_write
import pymodbus.client as lib_client
import pymodbus.diag_message as pdu_diag
import pymodbus.file_message as pdu_file_msg
import pymodbus.other_message as pdu_other_msg
import pymodbus.register_read_message as pdu_reg_read
import pymodbus.register_write_message as pdu_req_write
from pymodbus import Framer
from pymodbus.client.base import ModbusBaseClient
from pymodbus.client.mixin import ModbusClientMixin
from pymodbus.datastore import ModbusSlaveContext
from pymodbus.datastore.store import ModbusSequentialDataBlock
from pymodbus.exceptions import ConnectionException, ModbusException, ModbusIOException
from pymodbus.transport import CommType


BASE_PORT = 6500


@pytest.mark.parametrize(
    "arglist",
    [
        [
            {},
            {"address": 0x01},
            {"address": 0x01, "value": False},
            {"msg": b"long message"},
            {"toggle": False},
            {"address": 0x01, "values": [False, True]},
            {"address": 0x01, "values": [22, 44]},
            {"records": (0, 0)},
        ]
    ],
)
@pytest.mark.parametrize(
    ("method", "arg", "pdu_request"),
    [
        ("read_coils", 1, pdu_bit_read.ReadCoilsRequest),
        ("read_discrete_inputs", 1, pdu_bit_read.ReadDiscreteInputsRequest),
        ("read_holding_registers", 1, pdu_reg_read.ReadHoldingRegistersRequest),
        ("read_input_registers", 1, pdu_reg_read.ReadInputRegistersRequest),
        ("write_coil", 2, pdu_bit_write.WriteSingleCoilRequest),
        ("write_register", 2, pdu_req_write.WriteSingleRegisterRequest),
        ("read_exception_status", 0, pdu_other_msg.ReadExceptionStatusRequest),
        ("diag_query_data", 3, pdu_diag.ReturnQueryDataRequest),
        ("diag_restart_communication", 4, pdu_diag.RestartCommunicationsOptionRequest),
        ("diag_read_diagnostic_register", 0, pdu_diag.ReturnDiagnosticRegisterRequest),
        (
            "diag_change_ascii_input_delimeter",
            0,
            pdu_diag.ChangeAsciiInputDelimiterRequest,
        ),
        ("diag_force_listen_only", 0, pdu_diag.ForceListenOnlyModeRequest),
        ("diag_clear_counters", 0, pdu_diag.ClearCountersRequest),
        ("diag_read_bus_message_count", 0, pdu_diag.ReturnBusMessageCountRequest),
        (
            "diag_read_bus_comm_error_count",
            0,
            pdu_diag.ReturnBusCommunicationErrorCountRequest,
        ),
        (
            "diag_read_bus_exception_error_count",
            0,
            pdu_diag.ReturnBusExceptionErrorCountRequest,
        ),
        ("diag_read_slave_message_count", 0, pdu_diag.ReturnSlaveMessageCountRequest),
        (
            "diag_read_slave_no_response_count",
            0,
            pdu_diag.ReturnSlaveNoResponseCountRequest,
        ),
        ("diag_read_slave_nak_count", 0, pdu_diag.ReturnSlaveNAKCountRequest),
        ("diag_read_slave_busy_count", 0, pdu_diag.ReturnSlaveBusyCountRequest),
        (
            "diag_read_bus_char_overrun_count",
            0,
            pdu_diag.ReturnSlaveBusCharacterOverrunCountRequest,
        ),
        ("diag_read_iop_overrun_count", 0, pdu_diag.ReturnIopOverrunCountRequest),
        ("diag_clear_overrun_counter", 0, pdu_diag.ClearOverrunCountRequest),
        ("diag_getclear_modbus_response", 0, pdu_diag.GetClearModbusPlusRequest),
        ("write_coils", 5, pdu_bit_write.WriteMultipleCoilsRequest),
        ("write_registers", 6, pdu_req_write.WriteMultipleRegistersRequest),
        ("readwrite_registers", 1, pdu_reg_read.ReadWriteMultipleRegistersRequest),
        ("mask_write_register", 1, pdu_req_write.MaskWriteRegisterRequest),
        ("report_slave_id", 0, pdu_other_msg.ReportSlaveIdRequest),
        ("read_file_record", 7, pdu_file_msg.ReadFileRecordRequest),
        ("write_file_record", 7, pdu_file_msg.WriteFileRecordRequest),
        ("read_fifo_queue", 1, pdu_file_msg.ReadFifoQueueRequest),
    ],
)
def test_client_mixin(arglist, method, arg, pdu_request):
    """Test mixin responses."""
    pdu_to_call = None

    def fake_execute(_self, request):
        """Set PDU request."""
        nonlocal pdu_to_call
        pdu_to_call = request

    with mock.patch.object(ModbusClientMixin, "execute", fake_execute):
        getattr(ModbusClientMixin(), method)(**arglist[arg])
        assert isinstance(pdu_to_call, pdu_request)


@pytest.mark.parametrize(
    "arg_list",
    [
        {
            "fix": {
                "opt_args": {
                    "timeout": 3 + 2,
                    "retries": 3 + 2,
                    "retry_on_empty": True,
                    "close_comm_on_error": True,
                    "strict": False,
                    "broadcast_enable": not False,
                    "reconnect_delay": 117,
                    "reconnect_delay_max": 250,
                },
                "defaults": {
                    "timeout": 3,
                    "retries": 3,
                    "retry_on_empty": False,
                    "close_comm_on_error": False,
                    "strict": True,
                    "broadcast_enable": False,
                    "reconnect_delay": 100,
                    "reconnect_delay_max": 1000 * 60 * 5,
                },
            },
            "serial": {
                "pos_arg": "/dev/tty",
                "opt_args": {
                    "framer": Framer.ASCII,
                    "baudrate": 19200 + 500,
                    "bytesize": 8 - 1,
                    "parity": "E",
                    "stopbits": 1 + 1,
                    "handle_local_echo": True,
                },
                "defaults": {
                    "host": None,
                    "port": "/dev/tty",
                    "framer": Framer.RTU,
                    "baudrate": 19200,
                    "bytesize": 8,
                    "parity": "N",
                    "stopbits": 1,
                    "handle_local_echo": False,
                },
            },
            "tcp": {
                "pos_arg": "192.168.1.2",
                "opt_args": {
                    "port": 112,
                    "framer": Framer.ASCII,
                    "source_address": ("195.6.7.8", 1025),
                },
                "defaults": {
                    "host": "192.168.1.2",
                    "port": 502,
                    "framer": Framer.SOCKET,
                    "source_address": None,
                },
            },
            "tls": {
                "pos_arg": "192.168.1.2",
                "opt_args": {
                    "port": 211,
                    "framer": Framer.ASCII,
                    "source_address": ("195.6.7.8", 1025),
                    "sslctx": None,
                    "certfile": None,
                    "keyfile": None,
                    "password": None,
                },
                "defaults": {
                    "host": "192.168.1.2",
                    "port": 802,
                    "framer": Framer.TLS,
                    "source_address": None,
                    "sslctx": None,
                    "certfile": None,
                    "keyfile": None,
                    "password": None,
                },
            },
            "udp": {
                "pos_arg": "192.168.1.2",
                "opt_args": {
                    "port": 121,
                    "framer": Framer.ASCII,
                    "source_address": ("195.6.7.8", 1025),
                },
                "defaults": {
                    "host": "192.168.1.2",
                    "port": 502,
                    "framer": Framer.SOCKET,
                    "source_address": None,
                },
            },
        },
    ],
)
@pytest.mark.parametrize(
    ("type_args", "clientclass"),
    [
        # TBD ("serial", lib_client.AsyncModbusSerialClient),
        # TBD ("serial", lib_client.ModbusSerialClient),
        ("tcp", lib_client.AsyncModbusTcpClient),
        ("tcp", lib_client.ModbusTcpClient),
        ("tls", lib_client.AsyncModbusTlsClient),
        ("tls", lib_client.ModbusTlsClient),
        ("udp", lib_client.AsyncModbusUdpClient),
        ("udp", lib_client.ModbusUdpClient),
    ],
)
@pytest.mark.parametrize("test_default", [True, False])
async def test_client_instanciate(
    arg_list,
    type_args,
    clientclass,
    test_default,
):
    """Try to instantiate clients."""
    cur_args = arg_list[type_args]
    if test_default:
        client = clientclass(cur_args["pos_arg"])
        to_test = dict(arg_list["fix"]["defaults"], **cur_args["defaults"])
    else:
        client = clientclass(
            cur_args["pos_arg"],
            **arg_list["fix"]["opt_args"],
            **cur_args["opt_args"],
        )
        to_test = dict(arg_list["fix"]["opt_args"], **cur_args["opt_args"])
        to_test["host"] = cur_args["defaults"]["host"]

    # Test information methods
    client.last_frame_end = 2
    client.silent_interval = 2
    assert client.idle_time() == 4
    client.last_frame_end = None
    assert not client.idle_time()

    # a successful execute
    client.connect = lambda: True
    client.transport = lambda: None
    client.transaction = mock.Mock(**{"execute.return_value": True})

    # a unsuccessful connect
    client.connect = lambda: False
    client.transport = None
    with pytest.raises(ConnectionException):
        client.execute()

async def test_serial_not_installed():
    """Try to instantiate clients."""
    with mock.patch(
        "pymodbus.client.serial.PYSERIAL_MISSING"
    ) as _pyserial_missing:
        _pyserial_missing = True
        with pytest.raises(RuntimeError):
            lib_client.AsyncModbusSerialClient("/dev/tty")


async def test_client_modbusbaseclient():
    """Test modbus base client class."""
    client = ModbusBaseClient(
        Framer.ASCII,
        host="localhost",
        port=BASE_PORT + 1,
        CommType=CommType.TCP,
    )
    client.register(pdu_bit_read.ReadCoilsResponse)
    assert str(client)
    client.close()


async def test_client_connection_made():
    """Test protocol made connection."""
    client = lib_client.AsyncModbusTcpClient("127.0.0.1")
    assert not client.connected
    client.connection_made(mock.AsyncMock())
    assert client.connected
    client.close()


async def test_client_base_async():
    """Test modbus base client class."""
    with mock.patch(
        "pymodbus.client.base.ModbusBaseClient.connect"
    ) as p_connect, mock.patch(
        "pymodbus.client.base.ModbusBaseClient.close"
    ) as p_close:
        asyncio.get_event_loop()
        p_connect.return_value = asyncio.Future()
        p_connect.return_value.set_result(True)
        p_close.return_value = asyncio.Future()
        p_close.return_value.set_result(True)
        async with ModbusBaseClient(
            Framer.ASCII,
            host="localhost",
            port=BASE_PORT + 2,
            CommType=CommType.TCP,
        ) as client:
            str(client)
        p_connect.return_value = asyncio.Future()
        p_connect.return_value.set_result(False)
        p_close.return_value = asyncio.Future()
        p_close.return_value.set_result(False)


@pytest.mark.skip()
async def test_client_protocol_receiver():
    """Test the client protocol data received."""
    base = ModbusBaseClient(Framer.SOCKET)
    transport = mock.MagicMock()
    base.connection_made(transport)
    assert base.transport == transport
    assert base.transport
    data = b"\x00\x00\x12\x34\x00\x06\xff\x01\x01\x02\x00\x04"

    # setup existing request
    assert not list(base.transaction)
    response = base.build_response(0x00)  # pylint: disable=protected-access
    base.data_received(data)
    result = response.result()
    assert isinstance(result, pdu_bit_read.ReadCoilsResponse)

    base.transport = None
    with pytest.raises(ConnectionException):
        await base.build_response(0x00)  # pylint: disable=protected-access


@pytest.mark.skip()
async def test_client_protocol_response():
    """Test the udp client protocol builds responses."""
    base = ModbusBaseClient(Framer.SOCKET)
    response = base.build_response(0x00)  # pylint: disable=protected-access
    excp = response.exception()
    assert isinstance(excp, ConnectionException)
    assert not list(base.transaction)

    base.transport = lambda: None
    base.build_response(0x00)  # pylint: disable=protected-access
    assert len(list(base.transaction)) == 1


async def test_client_protocol_handler():
    """Test the client protocol handles responses."""
    base = ModbusBaseClient(
        Framer.ASCII, host="localhost", port=+3, CommType=CommType.TCP
    )
    transport = mock.MagicMock()
    base.connection_made(transport=transport)
    reply = pdu_bit_read.ReadCoilsRequest(1, 1)
    reply.transaction_id = 0x00
    base._handle_response(None)  # pylint: disable=protected-access
    base._handle_response(reply)  # pylint: disable=protected-access
    response = base.build_response(0x00)  # pylint: disable=protected-access
    base._handle_response(reply)  # pylint: disable=protected-access
    result = response.result()
    assert result == reply


class MockTransport:
    """Mock transport class which responds with an appropriate encoded packet."""

    def __init__(self, base, req, retries=0):
        """Initialize MockTransport."""
        self.base = base
        self.retries = retries

        db = ModbusSequentialDataBlock(0, [0] * 100)
        self.ctx = ModbusSlaveContext(di=db, co=db, hr=db, ir=db)
        self.req = req

    async def delayed_resp(self):
        """Send a response to a received packet."""
        await asyncio.sleep(0.05)
        resp = self.req.execute(self.ctx)
        pkt = self.base.framer.buildPacket(resp)
        self.base.data_received(pkt)

    def write(self, data, addr=None):
        """Write data to the transport, start a task to send the response."""
        if self.retries:
            self.retries -= 1
            return
        self.delayed_resp_task = asyncio.create_task(self.delayed_resp())
        self.delayed_resp_task.set_name("delayed response")

    def close(self):
        """Close the transport."""
        pass


async def test_client_protocol_execute():
    """Test the client protocol execute method."""
    base = ModbusBaseClient(Framer.SOCKET, host="127.0.0.1")
    request = pdu_bit_read.ReadCoilsRequest(1, 1)
    transport = MockTransport(base, request)
    base.connection_made(transport=transport)

    response = await base.async_execute(request)
    assert not response.isError()
    assert isinstance(response, pdu_bit_read.ReadCoilsResponse)

async def test_client_execute_broadcast():
    """Test the client protocol execute method."""
    base = ModbusBaseClient(Framer.SOCKET, host="127.0.0.1")
    base.broadcast_enable = True
    request = pdu_bit_read.ReadCoilsRequest(1, 1)
    transport = MockTransport(base, request)
    base.connection_made(transport=transport)

    response = await base.async_execute(request)
    assert  response == b'Broadcast write sent - no response expected'

async def test_client_protocol_retry():
    """Test the client protocol execute method with retries."""
    base = ModbusBaseClient(Framer.SOCKET, host="127.0.0.1", timeout=0.1)
    request = pdu_bit_read.ReadCoilsRequest(1, 1)
    transport = MockTransport(base, request, retries=2)
    base.connection_made(transport=transport)

    response = await base.async_execute(request)
    assert transport.retries == 0
    assert not response.isError()
    assert isinstance(response, pdu_bit_read.ReadCoilsResponse)


async def test_client_protocol_timeout():
    """Test the client protocol execute method with timeout."""
    base = ModbusBaseClient(Framer.SOCKET, host="127.0.0.1", timeout=0.1, retries=2)
    # Avoid creating do_reconnect() task
    base.connection_lost = mock.MagicMock()
    request = pdu_bit_read.ReadCoilsRequest(1, 1)
    transport = MockTransport(base, request, retries=4)
    base.connection_made(transport=transport)

    with pytest.raises(ModbusIOException):
        await base.async_execute(request)
    assert transport.retries == 1


def test_client_udp_connect():
    """Test the Udp client connection method."""
    with mock.patch.object(socket, "socket") as mock_method:

        class DummySocket:
            """Dummy socket."""

            fileno = 1

            def settimeout(self, *a, **kwa):
                """Set timeout."""

            def setblocking(self, _flag):
                """Set blocking."""

        mock_method.return_value = DummySocket()
        client = lib_client.ModbusUdpClient("127.0.0.1")
        assert client.connect()

    with mock.patch.object(socket, "socket") as mock_method:
        mock_method.side_effect = OSError()
        client = lib_client.ModbusUdpClient("127.0.0.1")
        assert not client.connect()


def test_client_tcp_connect():
    """Test the tcp client connection method."""
    with mock.patch.object(socket, "create_connection") as mock_method:
        _socket = mock.MagicMock()
        mock_method.return_value = _socket
        client = lib_client.ModbusTcpClient("127.0.0.1")
        _socket.getsockname.return_value = ("dmmy", 1234)
        assert client.connect()

    with mock.patch.object(socket, "create_connection") as mock_method:
        mock_method.side_effect = OSError()
        client = lib_client.ModbusTcpClient("127.0.0.1")
        assert not client.connect()


def test_client_tcp_reuse():
    """Test the tcp client connection method."""
    with mock.patch.object(socket, "create_connection") as mock_method:
        _socket = mock.MagicMock()
        mock_method.return_value = _socket
        client = lib_client.ModbusTcpClient("127.0.0.1")
        _socket.getsockname.return_value = ("dmmy", 1234)
        assert client.connect()
    client.close()
    with mock.patch.object(socket, "create_connection") as mock_method:
        _socket = mock.MagicMock()
        mock_method.return_value = _socket
        client = lib_client.ModbusTcpClient("127.0.0.1")
        _socket.getsockname.return_value = ("dmmy", 1234)
        assert client.connect()
    client.close()


def test_client_tls_connect():
    """Test the tls client connection method."""
    with mock.patch.object(ssl.SSLSocket, "connect") as mock_method:
        client = lib_client.ModbusTlsClient("127.0.0.1")
        assert client.connect()

    with mock.patch.object(socket, "create_connection") as mock_method:
        mock_method.side_effect = OSError()
        client = lib_client.ModbusTlsClient("127.0.0.1")
        assert not client.connect()


def test_client_tls_connect2():
    """Test the tls client connection method."""
    with mock.patch.object(ssl.SSLSocket, "connect") as mock_method:
        client = lib_client.ModbusTlsClient("127.0.0.1", source_address=("0.0.0.0", 0))
        assert client.connect()

    with mock.patch.object(socket, "create_connection") as mock_method:
        mock_method.side_effect = OSError()
        client = lib_client.ModbusTlsClient("127.0.0.1")
        assert not client.connect()


@pytest.mark.parametrize(
    ("datatype", "value", "registers"),
    [
        (ModbusClientMixin.DATATYPE.STRING, "abcd", [0x6162, 0x6364]),
        (ModbusClientMixin.DATATYPE.STRING, "a", [0x6100]),
        (ModbusClientMixin.DATATYPE.UINT16, 27123, [0x69F3]),
        (ModbusClientMixin.DATATYPE.INT16, -27123, [0x960D]),
        (ModbusClientMixin.DATATYPE.UINT32, 27123, [0x0000, 0x69F3]),
        (ModbusClientMixin.DATATYPE.UINT32, 32145678, [0x01EA, 0x810E]),
        (ModbusClientMixin.DATATYPE.INT32, -32145678, [0xFE15, 0x7EF2]),
        (
            ModbusClientMixin.DATATYPE.UINT64,
            1234567890123456789,
            [0x1122, 0x10F4, 0x7DE9, 0x8115],
        ),
        (
            ModbusClientMixin.DATATYPE.INT64,
            -1234567890123456789,
            [0xEEDD, 0xEF0B, 0x8216, 0x7EEB],
        ),
        (ModbusClientMixin.DATATYPE.FLOAT32, 27123.5, [0x46D3, 0xE700]),
        (ModbusClientMixin.DATATYPE.FLOAT32, 3.141592, [0x4049, 0x0FD8]),
        (ModbusClientMixin.DATATYPE.FLOAT32, -3.141592, [0xC049, 0x0FD8]),
        (ModbusClientMixin.DATATYPE.FLOAT64, 27123.5, [0x40DA, 0x7CE0, 0x0000, 0x0000]),
        (
            ModbusClientMixin.DATATYPE.FLOAT64,
            3.14159265358979,
            [0x4009, 0x21FB, 0x5444, 0x2D11],
        ),
        (
            ModbusClientMixin.DATATYPE.FLOAT64,
            -3.14159265358979,
            [0xC009, 0x21FB, 0x5444, 0x2D11],
        ),
    ],
)
def test_client_mixin_convert(datatype, registers, value):
    """Test converter methods."""
    regs = ModbusClientMixin.convert_to_registers(value, datatype)
    result = ModbusClientMixin.convert_from_registers(regs, datatype)
    if datatype == ModbusClientMixin.DATATYPE.FLOAT32:
        result = round(result, 6)
    assert regs == registers
    assert result == value


def test_client_mixin_convert_fail():
    """Test convert fail."""
    with pytest.raises(TypeError):
        ModbusClientMixin.convert_to_registers(123, ModbusClientMixin.DATATYPE.STRING)

    with pytest.raises(ModbusException):
        ModbusClientMixin.convert_from_registers([123], ModbusClientMixin.DATATYPE.FLOAT64)


async def test_client_build_response():
    """Test fail of build_response."""
    client = ModbusBaseClient(Framer.RTU)
    with pytest.raises(ConnectionException):
        await client.build_response(0)

async def test_client_mixin_execute():
    """Test dummy execute."""
    client = ModbusClientMixin()
    assert client.execute(None)
