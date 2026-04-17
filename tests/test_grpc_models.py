"""Tests for gRPC models and parsing.

The .bin fixtures under tests/data/ are real protobuf responses captured from
the Polestar gRPC API via tests/capture_grpc_fixtures.py. Identifying fields
(vin, ids, source, timestamps) were replaced with fixed anonymized values
in memory before the bytes were written to disk, so the fixtures are safe
to commit while still exercising real wire-format data.
"""

from datetime import datetime, timezone
from pathlib import Path

from pypolestar.grpc_client import _parse_battery, _parse_target_soc
from pypolestar.grpc_models import (
    ChargeTargetLevelSettingType,
    ChargingConnectionStatus,
    ChargingStatus,
    ChargingType,
    GrpcBatteryData,
    GrpcTargetSocData,
)
from pypolestar.proto import battery_pb2, battery_service_pb2, target_soc_pb2

DATADIR = Path(__file__).parent.resolve() / "data"

# Fixed values used by capture_grpc_fixtures.py when anonymizing.
ANON_VIN = "YSMYKEAE7RB000000"
ANON_ID = "00000000-0000-0000-0000-000000000000"
ANON_TIMESTAMP = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _load_battery_fixture() -> battery_service_pb2.GetBatteryResponse:
    raw = (DATADIR / "grpc_battery_response.bin").read_bytes()
    return battery_service_pb2.GetBatteryResponse.FromString(raw)


def _load_target_soc_fixture() -> target_soc_pb2.GetTargetSocResponse:
    raw = (DATADIR / "grpc_target_soc_response.bin").read_bytes()
    return target_soc_pb2.GetTargetSocResponse.FromString(raw)


def test_battery_fixture_envelope():
    response = _load_battery_fixture()
    assert response.id == ANON_ID
    assert response.vin == ANON_VIN
    assert response.HasField("battery")


def test_parse_battery_fixture():
    response = _load_battery_fixture()
    data = _parse_battery(response.battery)

    assert isinstance(data, GrpcBatteryData)
    assert data.charger_connection_status == ChargingConnectionStatus.CHARGER_CONNECTION_STATUS_CONNECTED
    assert data.charging_status == ChargingStatus.CHARGING_STATUS_IDLE
    assert data.charging_type == ChargingType.CHARGING_TYPE_AC
    assert data.battery_charge_level_percentage == 26.0
    assert data.estimated_distance_to_empty_km == 111
    assert data.estimated_charging_time_to_full_minutes == 1
    assert data.timestamp == ANON_TIMESTAMP


def test_target_soc_fixture_envelope():
    response = _load_target_soc_fixture()
    assert response.id == ANON_ID
    assert response.vin == ANON_VIN


def test_parse_target_soc_fixture():
    response = _load_target_soc_fixture()
    data = _parse_target_soc(response)

    assert isinstance(data, GrpcTargetSocData)
    assert data.battery_charge_target_level == 80
    assert data.charge_target_level_setting_type == ChargeTargetLevelSettingType.CUSTOM
    assert data.pending_battery_charge_target_level is None
    assert data.pending_charge_target_level_setting_type == (
        ChargeTargetLevelSettingType.CHARGE_TARGET_LEVEL_SETTING_TYPE_UNSPECIFIED
    )


def test_parse_battery_synthetic_dc_charging():
    """Exercise fields the captured fixture didn't populate (DC charging, power/amps/volts)."""
    msg = battery_pb2.Battery(
        charger_connection_status=battery_pb2.CHARGER_CONNECTION_STATUS_CONNECTED,
        charging_status=battery_pb2.CHARGING_STATUS_CHARGING,
        charging_type=battery_pb2.CHARGING_TYPE_DC,
        battery_charge_level_percentage=45.5,
        charging_power_watts=150_000,
        charging_current_amps=375,
        charging_voltage_volts=400,
        estimated_charging_time_minutes_to_target_distance=15,
        estimated_charging_time_minutes_to_minimum_soc=5,
    )
    data = _parse_battery(battery_pb2.Battery.FromString(msg.SerializeToString()))

    assert data.charger_connection_status == ChargingConnectionStatus.CHARGER_CONNECTION_STATUS_CONNECTED
    assert data.charging_status == ChargingStatus.CHARGING_STATUS_CHARGING
    assert data.charging_type == ChargingType.CHARGING_TYPE_DC
    assert data.charging_power_watts == 150_000
    assert data.charging_current_amps == 375
    assert data.charging_voltage_volts == 400


def test_parse_battery_unspecified_enums_default_to_unspecified():
    data = _parse_battery(battery_pb2.Battery.FromString(battery_pb2.Battery().SerializeToString()))

    assert data.charger_connection_status == ChargingConnectionStatus.CHARGER_CONNECTION_STATUS_UNSPECIFIED
    assert data.charging_status == ChargingStatus.CHARGING_STATUS_UNSPECIFIED
    assert data.charging_type == ChargingType.CHARGING_TYPE_UNSPECIFIED
    assert data.timestamp is None


def test_all_charging_status_values_are_mapped():
    # Every ChargingStatus enum value in the proto must have a corresponding
    # ChargingStatus member — otherwise we'd silently fall back to UNSPECIFIED
    # and mask real car state.
    for number in battery_pb2.ChargingStatus.values():
        msg = battery_pb2.Battery(charging_status=number)
        parsed = _parse_battery(battery_pb2.Battery.FromString(msg.SerializeToString()))
        expected_name = battery_pb2.ChargingStatus.Name(number)
        assert parsed.charging_status.name == expected_name, (
            f"ChargingStatus {expected_name} not mapped in grpc_models.ChargingStatus"
        )


def test_parse_target_soc_preserves_zero_level():
    # Regression: an earlier version used `x or None` which would turn a legitimate
    # 0 into None. Direct assignment means 0 stays 0.
    response = target_soc_pb2.GetTargetSocResponse(
        target_soc=target_soc_pb2.TargetSoc(
            battery_charge_target_level=0,
            charge_target_level_setting_type=target_soc_pb2.CUSTOM,
        ),
    )
    data = _parse_target_soc(target_soc_pb2.GetTargetSocResponse.FromString(response.SerializeToString()))
    assert data.battery_charge_target_level == 0


def test_parse_target_soc_with_pending():
    response = target_soc_pb2.GetTargetSocResponse(
        target_soc=target_soc_pb2.TargetSoc(
            battery_charge_target_level=90,
            charge_target_level_setting_type=target_soc_pb2.DAILY,
        ),
        pending_target_soc=target_soc_pb2.TargetSoc(
            battery_charge_target_level=100,
            charge_target_level_setting_type=target_soc_pb2.LONG_TRIP,
        ),
    )
    data = _parse_target_soc(target_soc_pb2.GetTargetSocResponse.FromString(response.SerializeToString()))

    assert data.battery_charge_target_level == 90
    assert data.charge_target_level_setting_type == ChargeTargetLevelSettingType.DAILY
    assert data.pending_battery_charge_target_level == 100
    assert data.pending_charge_target_level_setting_type == ChargeTargetLevelSettingType.LONG_TRIP


def test_parse_target_soc_empty_response():
    response = target_soc_pb2.GetTargetSocResponse.FromString(target_soc_pb2.GetTargetSocResponse().SerializeToString())
    data = _parse_target_soc(response)

    assert data.battery_charge_target_level is None
    assert data.pending_battery_charge_target_level is None
    assert data.charge_target_level_setting_type == (
        ChargeTargetLevelSettingType.CHARGE_TARGET_LEVEL_SETTING_TYPE_UNSPECIFIED
    )
