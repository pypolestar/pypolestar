"""Tests for gRPC models and parsing.

The .bin fixtures under tests/data/ are real protobuf responses captured from
the Polestar gRPC API via tests/capture_grpc_fixtures.py. Identifying fields
(vin, ids, source, timestamps) were replaced with fixed anonymized values
in memory before the bytes were written to disk, so the fixtures are safe
to commit while still exercising real wire-format data.
"""

from datetime import datetime, timezone
from pathlib import Path

from pypolestar.grpc_client import (
    _parse_amp_limit,
    _parse_availability,
    _parse_battery,
    _parse_charge_schedule,
    _parse_climate,
    _parse_exterior,
    _parse_health,
    _parse_location,
    _parse_mycars,
    _parse_odometer,
    _parse_precleaning,
    _parse_target_soc,
)
from pypolestar.grpc_models import (
    AlarmStatus,
    AvailabilityStatus,
    ChargeTargetLevelSettingType,
    ChargingConnectionStatus,
    ChargingStatus,
    ChargingType,
    ClimateRunningStatus,
    ExteriorLightWarning,
    GrpcBatteryData,
    GrpcTargetSocData,
    HeatingIntensity,
    LockStatus,
    OpenStatus,
    ServiceWarning,
    TyrePressureWarning,
)
from pypolestar.proto import (
    polestar_amplimit_pb2,
    polestar_availability_pb2,
    polestar_battery_pb2,
    polestar_battery_service_pb2,
    polestar_chargetimer_pb2,
    polestar_exterior_pb2,
    polestar_health_pb2,
    polestar_location_pb2,
    polestar_mycars_pb2,
    polestar_odometer_pb2,
    polestar_parkingclimatization_pb2,
    polestar_precleaning_pb2,
    polestar_target_soc_pb2,
)

DATADIR = Path(__file__).parent.resolve() / "data"

# Fixed values used by capture_grpc_fixtures.py when anonymizing.
ANON_VIN = "YSMYKEAE7RB000000"
ANON_ID = "00000000-0000-0000-0000-000000000000"
ANON_TIMESTAMP = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _load_battery_fixture() -> polestar_battery_service_pb2.GetBatteryResponse:
    raw = (DATADIR / "grpc_battery_response.bin").read_bytes()
    return polestar_battery_service_pb2.GetBatteryResponse.FromString(raw)


def _load_target_soc_fixture() -> polestar_target_soc_pb2.GetTargetSocResponse:
    raw = (DATADIR / "grpc_target_soc_response.bin").read_bytes()
    return polestar_target_soc_pb2.GetTargetSocResponse.FromString(raw)


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
    assert data.charging_type == ChargingType.CHARGING_TYPE_NONE
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
    msg = polestar_battery_pb2.Battery(
        charger_connection_status=polestar_battery_pb2.CHARGER_CONNECTION_STATUS_CONNECTED,
        charging_status=polestar_battery_pb2.CHARGING_STATUS_CHARGING,
        charging_type=polestar_battery_pb2.CHARGING_TYPE_DC,
        battery_charge_level_percentage=45.5,
        charging_power_watts=150_000,
        charging_current_amps=375,
        charging_voltage_volts=400,
        estimated_charging_time_minutes_to_target_distance=15,
        estimated_charging_time_minutes_to_minimum_soc=5,
    )
    data = _parse_battery(polestar_battery_pb2.Battery.FromString(msg.SerializeToString()))

    assert data.charger_connection_status == ChargingConnectionStatus.CHARGER_CONNECTION_STATUS_CONNECTED
    assert data.charging_status == ChargingStatus.CHARGING_STATUS_CHARGING
    assert data.charging_type == ChargingType.CHARGING_TYPE_DC
    assert data.charging_power_watts == 150_000
    assert data.charging_current_amps == 375
    assert data.charging_voltage_volts == 400


def test_parse_battery_unspecified_enums_default_to_unspecified():
    data = _parse_battery(polestar_battery_pb2.Battery.FromString(polestar_battery_pb2.Battery().SerializeToString()))

    assert data.charger_connection_status == ChargingConnectionStatus.CHARGER_CONNECTION_STATUS_UNSPECIFIED
    assert data.charging_status == ChargingStatus.CHARGING_STATUS_UNSPECIFIED
    assert data.charging_type == ChargingType.CHARGING_TYPE_UNSPECIFIED
    assert data.timestamp is None


def test_all_charging_status_values_are_mapped():
    # Every ChargingStatus enum value in the proto must have a corresponding
    # ChargingStatus member — otherwise we'd silently fall back to UNSPECIFIED
    # and mask real car state.
    for number in polestar_battery_pb2.ChargingStatus.values():
        msg = polestar_battery_pb2.Battery(charging_status=number)
        parsed = _parse_battery(polestar_battery_pb2.Battery.FromString(msg.SerializeToString()))
        expected_name = polestar_battery_pb2.ChargingStatus.Name(number)
        assert parsed.charging_status.name == expected_name, (
            f"ChargingStatus {expected_name} not mapped in grpc_models.ChargingStatus"
        )


def test_parse_target_soc_preserves_zero_level():
    # Regression: an earlier version used `x or None` which would turn a legitimate
    # 0 into None. Direct assignment means 0 stays 0.
    response = polestar_target_soc_pb2.GetTargetSocResponse(
        target_soc=polestar_target_soc_pb2.TargetSoc(
            battery_charge_target_level=0,
            charge_target_level_setting_type=polestar_target_soc_pb2.CUSTOM,
        ),
    )
    data = _parse_target_soc(polestar_target_soc_pb2.GetTargetSocResponse.FromString(response.SerializeToString()))
    assert data.battery_charge_target_level == 0


def test_parse_target_soc_with_pending():
    response = polestar_target_soc_pb2.GetTargetSocResponse(
        target_soc=polestar_target_soc_pb2.TargetSoc(
            battery_charge_target_level=90,
            charge_target_level_setting_type=polestar_target_soc_pb2.DAILY,
        ),
        pending_target_soc=polestar_target_soc_pb2.TargetSoc(
            battery_charge_target_level=100,
            charge_target_level_setting_type=polestar_target_soc_pb2.LONG_TRIP,
        ),
    )
    data = _parse_target_soc(polestar_target_soc_pb2.GetTargetSocResponse.FromString(response.SerializeToString()))

    assert data.battery_charge_target_level == 90
    assert data.charge_target_level_setting_type == ChargeTargetLevelSettingType.DAILY
    assert data.pending_battery_charge_target_level == 100
    assert data.pending_charge_target_level_setting_type == ChargeTargetLevelSettingType.LONG_TRIP


def test_parse_target_soc_empty_response():
    response = polestar_target_soc_pb2.GetTargetSocResponse.FromString(
        polestar_target_soc_pb2.GetTargetSocResponse().SerializeToString()
    )
    data = _parse_target_soc(response)

    assert data.battery_charge_target_level is None
    assert data.pending_battery_charge_target_level is None
    assert data.charge_target_level_setting_type == (
        ChargeTargetLevelSettingType.CHARGE_TARGET_LEVEL_SETTING_TYPE_UNSPECIFIED
    )


# --------------------------------------------------------------------------
# Best-effort services (field layout cross-referenced from public
# reverse-engineering projects, then spot-checked live -- see each
# .proto file's header comment for what was confirmed). These use
# synthetic messages only -- no captured fixtures exist yet for them.
# --------------------------------------------------------------------------


def test_parse_exterior_synthetic():
    msg = polestar_exterior_pb2.Exterior(
        central_lock=polestar_exterior_pb2.LOCK_STATUS_LOCKED,
        tailgate_lock=polestar_exterior_pb2.LOCK_STATUS_UNLOCKED,
        front_left_door=polestar_exterior_pb2.OPEN_STATUS_CLOSED,
        tailgate=polestar_exterior_pb2.OPEN_STATUS_AJAR,
        alarm=polestar_exterior_pb2.ALARM_STATUS_TRIGGERED,
    )
    data = _parse_exterior(polestar_exterior_pb2.Exterior.FromString(msg.SerializeToString()))

    assert data.central_lock == LockStatus.LOCK_STATUS_LOCKED
    assert data.tailgate_lock == LockStatus.LOCK_STATUS_UNLOCKED
    assert data.front_left_door == OpenStatus.OPEN_STATUS_CLOSED
    assert data.tailgate == OpenStatus.OPEN_STATUS_AJAR
    assert data.alarm == AlarmStatus.ALARM_STATUS_TRIGGERED
    # Fields never set on the wire default to UNSPECIFIED, not a guessed "safe" state.
    assert data.front_right_door == OpenStatus.OPEN_STATUS_UNSPECIFIED
    assert data.timestamp is None


def test_all_open_status_values_are_mapped():
    for number in polestar_exterior_pb2.OpenStatus.values():
        msg = polestar_exterior_pb2.Exterior(front_left_door=number)
        parsed = _parse_exterior(polestar_exterior_pb2.Exterior.FromString(msg.SerializeToString()))
        expected_name = polestar_exterior_pb2.OpenStatus.Name(number)
        assert parsed.front_left_door.name == expected_name


def test_parse_health_preserves_zero_pressure():
    # Same class of bug the target_soc "preserves zero" regression test guards
    # against: a `x or None` pattern would turn a real 0.0 kPa reading into None.
    msg = polestar_health_pb2.Health(
        front_left_tyre_pressure_kpa=0.0,
        days_to_service=0,
        service_warning=polestar_health_pb2.SERVICE_WARNING_REGULAR_MAINTENANCE_TIME_FOR_SERVICE,
    )
    data = _parse_health(polestar_health_pb2.Health.FromString(msg.SerializeToString()))

    assert data.front_left_tyre_pressure_kpa == 0.0
    assert data.days_to_service == 0
    assert data.service_warning == ServiceWarning.SERVICE_WARNING_REGULAR_MAINTENANCE_TIME_FOR_SERVICE


def test_parse_health_light_warnings_dict_covers_all_lights():
    msg = polestar_health_pb2.Health(
        brake_light_left_warning=polestar_health_pb2.EXTERIOR_LIGHT_WARNING_FAILURE,
        high_beam_right_warning=polestar_health_pb2.EXTERIOR_LIGHT_WARNING_NO_WARNING,
    )
    data = _parse_health(polestar_health_pb2.Health.FromString(msg.SerializeToString()))

    assert len(data.exterior_light_warnings) == 21
    assert data.exterior_light_warnings["brake_light_left"] == ExteriorLightWarning.EXTERIOR_LIGHT_WARNING_FAILURE
    assert data.exterior_light_warnings["high_beam_right"] == ExteriorLightWarning.EXTERIOR_LIGHT_WARNING_NO_WARNING
    assert data.exterior_light_warnings["fog_light_front"] == ExteriorLightWarning.EXTERIOR_LIGHT_WARNING_UNSPECIFIED


def test_all_health_tyre_pressure_warning_values_are_mapped():
    for number in polestar_health_pb2.TyrePressureWarning.values():
        msg = polestar_health_pb2.Health(front_left_tyre_pressure_warning=number)
        parsed = _parse_health(polestar_health_pb2.Health.FromString(msg.SerializeToString()))
        expected_name = polestar_health_pb2.TyrePressureWarning.Name(number)
        assert parsed.front_left_tyre_pressure_warning == TyrePressureWarning[expected_name]


def test_all_health_service_warning_values_are_mapped():
    # Guards the models.ServiceWarning extension: every gRPC-only member
    # (UNKNOWN_WARNING, ENGINE_HOURS_*) must exist on the shared enum too.
    for number in polestar_health_pb2.ServiceWarning.values():
        msg = polestar_health_pb2.Health(service_warning=number)
        parsed = _parse_health(polestar_health_pb2.Health.FromString(msg.SerializeToString()))
        expected_name = polestar_health_pb2.ServiceWarning.Name(number)
        assert parsed.service_warning.name == expected_name


def test_parse_odometer_preserves_zero_meters():
    msg = polestar_odometer_pb2.Odometer(odometer_meters=0, trip_meter_manual_km=12.5)
    data = _parse_odometer(polestar_odometer_pb2.Odometer.FromString(msg.SerializeToString()))

    assert data.odometer_meters == 0
    assert data.trip_meter_manual_km == 12.5


def test_parse_climate_synthetic():
    msg = polestar_parkingclimatization_pb2.ParkingClimatization(
        running_status=polestar_parkingclimatization_pb2.RUNNING_STATUS_ON,
        requested_front_left_seat=polestar_parkingclimatization_pb2.HEATING_INTENSITY_HIGH,
        requested_compartment_temperature_celsius=22.0,
        runtime_left_minutes=0,
    )
    data = _parse_climate(polestar_parkingclimatization_pb2.ParkingClimatization.FromString(msg.SerializeToString()))

    assert data.running_status == ClimateRunningStatus.RUNNING_STATUS_ON
    assert data.requested_front_left_seat == HeatingIntensity.HEATING_INTENSITY_HIGH
    assert data.requested_compartment_temperature_celsius == 22.0
    assert data.runtime_left_minutes == 0
    assert data.requested_front_right_seat == HeatingIntensity.HEATING_INTENSITY_UNSPECIFIED


def test_parse_availability_synthetic():
    msg = polestar_availability_pb2.Availability(
        availability_status=polestar_availability_pb2.AVAILABILITY_STATUS_UNAVAILABLE,
        unavailable_reason=polestar_availability_pb2.UNAVAILABLE_REASON_CAR_IN_USE,
    )
    data = _parse_availability(polestar_availability_pb2.Availability.FromString(msg.SerializeToString()))

    assert data.availability_status == AvailabilityStatus.AVAILABILITY_STATUS_UNAVAILABLE
    assert data.unavailable_reason.name == "UNAVAILABLE_REASON_CAR_IN_USE"


def test_parse_precleaning_preserves_falsy_values():
    msg = polestar_precleaning_pb2.PreCleaning(
        running_status=polestar_precleaning_pb2.RUNNING_STATUS_OFF,
        last_cycle_valid=False,
        runtime_left_minutes=0,
    )
    data = _parse_precleaning(polestar_precleaning_pb2.PreCleaning.FromString(msg.SerializeToString()))

    assert data.last_cycle_valid is False
    assert data.runtime_left_minutes == 0


def test_parse_location_preserves_zero_coordinates():
    # 0,0 is a real (if unlikely) coordinate -- must not be dropped like a
    # missing value would be.
    msg = polestar_location_pb2.LastParkedLocation(
        vin="X", location=polestar_location_pb2.Location(latitude=0.0, longitude=0.0), stale=True
    )
    data = _parse_location(polestar_location_pb2.LastParkedLocation.FromString(msg.SerializeToString()))

    assert data.latitude == 0.0
    assert data.longitude == 0.0
    assert data.stale is True


# --------------------------------------------------------------------------
# Live-schema-discovered services: no external schema existed anywhere for
# these, so they were reverse-engineered directly against a real account.
# These tests use synthetic, anonymized values shaped like a real captured
# response, not the real response itself.
# --------------------------------------------------------------------------


def test_parse_mycars_synthetic():
    entry = polestar_mycars_pb2.MyCarEntry(
        details=polestar_mycars_pb2.CarDetails(
            vin=ANON_VIN,
            model_name="Polestar 2",
            model_year="2023",
            installed_software_version="4.2.13",
            market="SE",
        ),
        registration_no="AA-00-AA",
    )
    data = _parse_mycars(polestar_mycars_pb2.MyCarEntry.FromString(entry.SerializeToString()))

    assert data.vin == ANON_VIN
    assert data.model_name == "Polestar 2"
    assert data.model_year == "2023"
    assert data.installed_software_version == "4.2.13"
    assert data.market == "SE"
    assert data.registration_no == "AA-00-AA"


def test_parse_mycars_empty_fields_become_none():
    entry = polestar_mycars_pb2.MyCarEntry.FromString(polestar_mycars_pb2.MyCarEntry().SerializeToString())
    data = _parse_mycars(entry)

    assert data.vin is None
    assert data.model_name is None
    assert data.registration_no is None


def test_parse_amp_limit_synthetic():
    response = polestar_amplimit_pb2.GetAmpLimitResponse(
        id=ANON_ID,
        vin=ANON_VIN,
        amp_limit=polestar_amplimit_pb2.AmpLimitReading(value=20, source="RCS"),
        updated_at=1735689600000,  # 2025-01-01T00:00:00Z in epoch millis
    )
    data = _parse_amp_limit(polestar_amplimit_pb2.GetAmpLimitResponse.FromString(response.SerializeToString()))

    assert data.value == 20
    assert data.pending_value is None
    assert data.updated_at == datetime(2025, 1, 1, tzinfo=timezone.utc)


def test_parse_amp_limit_preserves_zero_value():
    response = polestar_amplimit_pb2.GetAmpLimitResponse(
        amp_limit=polestar_amplimit_pb2.AmpLimitReading(value=0),
    )
    data = _parse_amp_limit(polestar_amplimit_pb2.GetAmpLimitResponse.FromString(response.SerializeToString()))
    assert data.value == 0


def test_parse_amp_limit_no_reading():
    response = polestar_amplimit_pb2.GetAmpLimitResponse.FromString(
        polestar_amplimit_pb2.GetAmpLimitResponse().SerializeToString()
    )
    data = _parse_amp_limit(response)
    assert data.value is None
    assert data.updated_at is None


def test_parse_charge_schedule_synthetic():
    response = polestar_chargetimer_pb2.GetGlobalChargeTimerStreamResponse(
        timer=polestar_chargetimer_pb2.ChargeTimerEntry(
            start=polestar_chargetimer_pb2.ScheduleTime(hour=23),
            end=polestar_chargetimer_pb2.ScheduleTime(hour=6),
        ),
        updated_at=1735689600000,
    )
    data = _parse_charge_schedule(
        polestar_chargetimer_pb2.GetGlobalChargeTimerStreamResponse.FromString(response.SerializeToString())
    )

    assert data.start_hour == 23
    assert data.end_hour == 6
    assert data.updated_at == datetime(2025, 1, 1, tzinfo=timezone.utc)


def test_parse_charge_schedule_preserves_midnight_hour():
    # Hour 0 (midnight) is a real, common schedule boundary -- must not be
    # dropped like a missing value would be.
    response = polestar_chargetimer_pb2.GetGlobalChargeTimerStreamResponse(
        timer=polestar_chargetimer_pb2.ChargeTimerEntry(
            start=polestar_chargetimer_pb2.ScheduleTime(hour=0),
            end=polestar_chargetimer_pb2.ScheduleTime(hour=0),
        ),
    )
    data = _parse_charge_schedule(
        polestar_chargetimer_pb2.GetGlobalChargeTimerStreamResponse.FromString(response.SerializeToString())
    )
    assert data.start_hour == 0
    assert data.end_hour == 0


def test_parse_charge_schedule_no_timer():
    response = polestar_chargetimer_pb2.GetGlobalChargeTimerStreamResponse.FromString(
        polestar_chargetimer_pb2.GetGlobalChargeTimerStreamResponse().SerializeToString()
    )
    data = _parse_charge_schedule(response)
    assert data.start_hour is None
    assert data.end_hour is None


# --------------------------------------------------------------------------
# CarDataCollection carries every gRPC telemetry model, so PolestarApi.get_data()
# (and cli.py's --dump, which json-serializes it) exposes them all in one place.
# --------------------------------------------------------------------------

# CarDataCollection field name -> parsed-from-empty instance of the model it holds.
GRPC_MODELS_BY_COLLECTION_FIELD = {
    "battery_data": _parse_battery(polestar_battery_pb2.Battery()),
    "target_soc": _parse_target_soc(polestar_target_soc_pb2.GetTargetSocResponse()),
    "grpc_exterior": _parse_exterior(polestar_exterior_pb2.Exterior()),
    "grpc_health": _parse_health(polestar_health_pb2.Health()),
    "grpc_odometer": _parse_odometer(polestar_odometer_pb2.Odometer()),
    "grpc_climate": _parse_climate(polestar_parkingclimatization_pb2.ParkingClimatization()),
    "grpc_availability": _parse_availability(polestar_availability_pb2.Availability()),
    "grpc_precleaning": _parse_precleaning(polestar_precleaning_pb2.PreCleaning()),
    "grpc_location": _parse_location(polestar_location_pb2.LastParkedLocation()),
    "grpc_mycars": _parse_mycars(polestar_mycars_pb2.MyCarEntry()),
    "grpc_amp_limit": _parse_amp_limit(polestar_amplimit_pb2.GetAmpLimitResponse()),
    "grpc_charge_schedule": _parse_charge_schedule(polestar_chargetimer_pb2.GetGlobalChargeTimerStreamResponse()),
}


def test_car_data_collection_round_trips_every_grpc_model_to_json():
    import json

    from pypolestar.models import CarDataCollection

    models = GRPC_MODELS_BY_COLLECTION_FIELD

    # every gRPC model has a slot on the collection ...
    assert set(models).issubset(CarDataCollection.model_fields)

    # ... pydantic accepts each one in its slot and keeps the instance intact ...
    collection = CarDataCollection(**models)
    for name, model in models.items():
        assert getattr(collection, name) is model

    # ... and the whole thing json-serializes (this is what cli.py --dump does)
    dumped = collection.model_dump(mode="json", exclude_none=True)
    assert set(models).issubset(dumped)
    json.dumps(dumped)
