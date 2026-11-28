"""Tests for gRPC client error handling."""

import asyncio

import grpc
import grpc.aio
import httpx
import pytest

from pypolestar.grpc_client import PolestarGrpcClient

VIN = "YSMYKEAE7RB000000"
TOKEN = "token"

# Status codes the client must read as "this vehicle will never serve this data"
PERMANENT_STATUS_CODES = [
    grpc.StatusCode.PERMISSION_DENIED,
    grpc.StatusCode.UNIMPLEMENTED,
    grpc.StatusCode.NOT_FOUND,
]


def _rpc_error(code: grpc.StatusCode) -> grpc.aio.AioRpcError:
    return grpc.aio.AioRpcError(
        code=code,
        initial_metadata=grpc.aio.Metadata(),
        trailing_metadata=grpc.aio.Metadata(),
        details=f'Status(StatusCode="{code.name}", Detail="")',
    )


class FailingChannel:
    """Channel stub where every call raises, counting the attempts."""

    def __init__(self, error: grpc.aio.AioRpcError):
        self.error = error
        self.calls = 0

    def _fail(self, *args, **kwargs):
        self.calls += 1
        raise self.error

    def unary_unary(self, *args, **kwargs):
        return self._fail

    def unary_stream(self, *args, **kwargs):
        return self._fail


def _client(**channels) -> PolestarGrpcClient:
    client = PolestarGrpcClient(client_session=None)  # type: ignore[arg-type]
    for name, channel in channels.items():
        setattr(client, name, channel)
    return client


@pytest.mark.parametrize("code", PERMANENT_STATUS_CODES)
def test_target_soc_permanent_error_is_not_retried(code):
    """A Polestar 2 is not provisioned in PCCS: refuse once, then stop asking."""
    channel = FailingChannel(_rpc_error(code))
    client = _client(pccs_channel=channel)

    assert asyncio.run(client.get_target_soc(VIN, TOKEN)) is None
    assert client.is_target_soc_supported(VIN) is False
    assert channel.calls == 1

    assert asyncio.run(client.get_target_soc(VIN, TOKEN)) is None
    assert channel.calls == 1, "unsupported vehicle should not hit the network again"


def test_target_soc_transient_error_is_raised_and_retried():
    channel = FailingChannel(_rpc_error(grpc.StatusCode.UNAVAILABLE))
    client = _client(pccs_channel=channel)

    for _ in range(2):
        with pytest.raises(grpc.aio.AioRpcError):
            asyncio.run(client.get_target_soc(VIN, TOKEN))

    assert client.is_target_soc_supported(VIN) is True
    assert channel.calls == 2


@pytest.mark.parametrize("code", PERMANENT_STATUS_CODES)
def test_battery_permanent_error_is_not_retried(code):
    channel = FailingChannel(_rpc_error(code))
    client = _client(c3_channel=channel)

    assert asyncio.run(client.get_battery(VIN, TOKEN)) is None
    assert client.is_battery_supported(VIN) is False
    assert channel.calls == 1

    assert asyncio.run(client.get_battery(VIN, TOKEN)) is None
    assert channel.calls == 1


def test_battery_transient_error_is_raised():
    channel = FailingChannel(_rpc_error(grpc.StatusCode.INTERNAL))
    client = _client(c3_channel=channel)

    with pytest.raises(grpc.aio.AioRpcError):
        asyncio.run(client.get_battery(VIN, TOKEN))

    assert client.is_battery_supported(VIN) is True


def test_failed_reconnect_keeps_unsupported_vehicles():
    """The old channels survive a failed reconnect, so what we learned must too."""

    class FailingSession:
        async def get(self, *args, **kwargs):
            raise httpx.ConnectError("discovery unreachable")

    channel = FailingChannel(_rpc_error(grpc.StatusCode.PERMISSION_DENIED))
    client = PolestarGrpcClient(client_session=FailingSession())  # type: ignore[arg-type]
    client.pccs_channel = channel

    assert asyncio.run(client.get_target_soc(VIN, TOKEN)) is None
    assert client.is_target_soc_supported(VIN) is False

    with pytest.raises(httpx.ConnectError):
        asyncio.run(client.connect())

    assert client.is_target_soc_supported(VIN) is False
    assert asyncio.run(client.get_target_soc(VIN, TOKEN)) is None
    assert channel.calls == 1, "a failed reconnect must not resurrect refused calls"


# The eight best-effort C3 services added alongside battery/target_soc. All
# of them go through the same _mark_unsupported / UNSUPPORTED_STATUS_CODES
# machinery as battery/target_soc above, whether they're called unary
# (exterior, climate, availability, location, mycars) or streaming-first-
# message (health, odometer, precleaning) -- FailingChannel raises at call
# time either way, before any iteration would happen.
NEW_C3_METHODS = [
    ("get_exterior", "is_exterior_supported"),
    ("get_health", "is_health_supported"),
    ("get_odometer", "is_odometer_supported"),
    ("get_climate", "is_climate_supported"),
    ("get_availability", "is_availability_supported"),
    ("get_precleaning", "is_precleaning_supported"),
    ("get_location", "is_location_supported"),
    ("get_mycars", "is_mycars_supported"),
]


@pytest.mark.parametrize("method_name,is_supported_name", NEW_C3_METHODS)
@pytest.mark.parametrize("code", PERMANENT_STATUS_CODES)
def test_new_c3_service_permanent_error_is_not_retried(method_name, is_supported_name, code):
    channel = FailingChannel(_rpc_error(code))
    client = _client(c3_channel=channel)
    method = getattr(client, method_name)
    is_supported = getattr(client, is_supported_name)

    assert asyncio.run(method(VIN, TOKEN)) is None
    assert is_supported(VIN) is False
    assert channel.calls == 1

    assert asyncio.run(method(VIN, TOKEN)) is None
    assert channel.calls == 1, "unsupported vehicle should not hit the network again"


@pytest.mark.parametrize("method_name,is_supported_name", NEW_C3_METHODS)
def test_new_c3_service_transient_error_is_raised_and_retried(method_name, is_supported_name):
    channel = FailingChannel(_rpc_error(grpc.StatusCode.UNAVAILABLE))
    client = _client(c3_channel=channel)
    method = getattr(client, method_name)
    is_supported = getattr(client, is_supported_name)

    for _ in range(2):
        with pytest.raises(grpc.aio.AioRpcError):
            asyncio.run(method(VIN, TOKEN))

    assert is_supported(VIN) is True
    assert channel.calls == 2


@pytest.mark.parametrize("method_name", [name for name, _ in NEW_C3_METHODS])
def test_new_c3_service_raises_without_channel(method_name):
    client = PolestarGrpcClient(client_session=None)  # type: ignore[arg-type]
    method = getattr(client, method_name)

    with pytest.raises(RuntimeError, match="gRPC C3 channel not connected"):
        asyncio.run(method(VIN, TOKEN))


# The two live-schema-discovered PCCS services (no external schema existed
# for either; reverse-engineered directly against a real account): same
# streaming-first-message shape as get_target_soc.
NEW_PCCS_METHODS = [
    ("get_amp_limit", "is_amp_limit_supported"),
    ("get_charge_schedule", "is_charge_schedule_supported"),
]


@pytest.mark.parametrize("method_name,is_supported_name", NEW_PCCS_METHODS)
@pytest.mark.parametrize("code", PERMANENT_STATUS_CODES)
def test_new_pccs_service_permanent_error_is_not_retried(method_name, is_supported_name, code):
    channel = FailingChannel(_rpc_error(code))
    client = _client(pccs_channel=channel)
    method = getattr(client, method_name)
    is_supported = getattr(client, is_supported_name)

    assert asyncio.run(method(VIN, TOKEN)) is None
    assert is_supported(VIN) is False
    assert channel.calls == 1

    assert asyncio.run(method(VIN, TOKEN)) is None
    assert channel.calls == 1, "unsupported vehicle should not hit the network again"


@pytest.mark.parametrize("method_name,is_supported_name", NEW_PCCS_METHODS)
def test_new_pccs_service_transient_error_is_raised_and_retried(method_name, is_supported_name):
    channel = FailingChannel(_rpc_error(grpc.StatusCode.UNAVAILABLE))
    client = _client(pccs_channel=channel)
    method = getattr(client, method_name)
    is_supported = getattr(client, is_supported_name)

    for _ in range(2):
        with pytest.raises(grpc.aio.AioRpcError):
            asyncio.run(method(VIN, TOKEN))

    assert is_supported(VIN) is True
    assert channel.calls == 2


@pytest.mark.parametrize("method_name", [name for name, _ in NEW_PCCS_METHODS])
def test_new_pccs_service_raises_without_channel(method_name):
    client = PolestarGrpcClient(client_session=None)  # type: ignore[arg-type]
    method = getattr(client, method_name)

    with pytest.raises(RuntimeError, match="gRPC PCCS channel not connected"):
        asyncio.run(method(VIN, TOKEN))
