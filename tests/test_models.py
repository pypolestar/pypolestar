import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from pypolestar.models import (
    BrakeFluidLevelWarning,
    CarBatteryData,
    CarBatteryInformationData,
    CarHealthData,
    CarInformationData,
    CarOdometerData,
    CarTelematicsData,
    ChargingConnectionStatus,
    ChargingStatus,
    EngineCoolantLevelWarning,
    OilLevelWarning,
    ServiceWarning,
)

DATADIR = Path(__file__).parent.resolve() / "data"


@pytest.fixture
def polestar3_test_data():
    try:
        with open(DATADIR / "polestar3.json") as fp:
            return json.load(fp)
    except FileNotFoundError as exc:
        pytest.skip(f"Test data file not found: {exc.filename}")
    except json.JSONDecodeError as exc:
        pytest.skip(f"Invalid JSON in test data file: {exc}")


@pytest.fixture
def polestar2_test_data():
    try:
        with open(DATADIR / "polestar2.json") as fp:
            return json.load(fp)
    except FileNotFoundError as exc:
        pytest.skip(f"Test data file not found: {exc.filename}")
    except json.JSONDecodeError as exc:
        pytest.skip(f"Invalid JSON in test data file: {exc}")


def test_car_information_data_polestar3(polestar3_test_data):
    data = CarInformationData.from_dict(polestar3_test_data["getConsumerCarsV2"])
    # Verify expected attributes
    assert data is not None
    assert isinstance(data, CarInformationData)
    assert data.vin == "YSMYKEAE7RB000000"
    assert data.internal_vehicle_identifier == "1aaeb452-700e-46f3-9899-395b6219c8a6"
    assert data.registration_no == "MLB007"
    assert data.registration_date is None
    assert data.factory_complete_date == date(year=2024, month=4, day=16)
    assert data.model_name == "Polestar 3"
    assert (
        data.image_url
        == "https://cas.polestar.com/image/dynamic/MY24_2207/359/summary-transparent-v2/EA/1/72300/R80000/R102/LR02/EV02/K503/JB07/SW01/_/ET01/default.png?market=se"
    )
    assert data.battery == "400V lithium-ion battery, 111 kWh capacity, 17 modules"
    assert data.torque == "840 Nm / 620 lbf-ft"
    assert data.software_version is None
    assert data.battery_information == CarBatteryInformationData(capacity=111, voltage=400, modules=17)
    assert data.torque_nm == 840


def test_car_battery_information_data():
    # Polestar3
    assert CarBatteryInformationData.from_battery_str(
        "400V lithium-ion battery, 111 kWh capacity, 17 modules"
    ) == CarBatteryInformationData(voltage=400, capacity=111, modules=17)

    # Polestar 2 Standard range Single motor
    assert CarBatteryInformationData.from_battery_str(
        "400V lithium-ion battery, 69 kWh capacity, 24 modules"
    ) == CarBatteryInformationData(voltage=400, capacity=69, modules=24)

    # Polestar 2 Long range Single motor
    assert CarBatteryInformationData.from_battery_str(
        "400V lithium-ion battery, 82 kWh capacity, 27 modules"
    ) == CarBatteryInformationData(voltage=400, capacity=82, modules=27)

    # Imaginary Polestar
    assert CarBatteryInformationData.from_battery_str(
        "800V lithium-ion battery, 111 kWh capacity, 17 modules"
    ) == CarBatteryInformationData(voltage=800, capacity=111, modules=17)

    # Imaginary Polestar
    assert CarBatteryInformationData.from_battery_str("4xAAA") == CarBatteryInformationData(
        voltage=None, capacity=None, modules=None
    )


def test_car_information_data_invalid():
    with pytest.raises(KeyError):
        CarInformationData.from_dict({})  # Test with empty dict
    with pytest.raises(TypeError):
        CarInformationData.from_dict(None)  # Test with None


def test_car_battery_data_polestar3(polestar3_test_data):
    data = CarBatteryData.from_dict(polestar3_test_data["getBatteryData"])
    assert data is not None
    assert isinstance(data, CarBatteryData)
    assert data.average_energy_consumption_kwh_per_100km == 22.4
    assert data.battery_charge_level_percentage == 34
    assert data.charger_connection_status == ChargingConnectionStatus.CHARGER_CONNECTION_STATUS_DISCONNECTED
    assert data.charging_current_amps == 0
    assert data.charging_power_watts == 0
    assert data.charging_status == ChargingStatus.CHARGING_STATUS_IDLE
    assert data.estimated_charging_time_minutes_to_target_distance == 0
    assert data.estimated_charging_time_to_full_minutes == 0
    assert data.estimated_distance_to_empty_km == 150
    assert data.event_updated_timestamp == datetime(
        year=2024,
        month=11,
        day=11,
        hour=17,
        minute=47,
        second=13,
        tzinfo=timezone.utc,
    )
    assert data.event_updated_timestamp is not None
    assert data.event_updated_timestamp.timestamp() == 1731347233


def test_car_battery_data_rate():
    data = CarBatteryData(
        _received_timestamp=datetime.now(tz=timezone.utc),
        average_energy_consumption_kwh_per_100km=None,
        battery_charge_level_percentage=55,
        charger_connection_status=ChargingConnectionStatus.CHARGER_CONNECTION_STATUS_DISCONNECTED,
        charging_current_amps=0,
        charging_power_watts=0,
        charging_status=ChargingStatus.CHARGING_STATUS_IDLE,
        estimated_charging_time_minutes_to_target_distance=None,
        estimated_charging_time_to_full_minutes=60,
        estimated_distance_to_empty_km=300,
        event_updated_timestamp=None,
    )

    assert data.estimated_full_charge_range_km == 545.45

    fully_charged_at = datetime.now(tz=timezone.utc) + timedelta(minutes=59)
    assert data.estimated_fully_charged is not None
    assert data.estimated_fully_charged > fully_charged_at


def test_car_battery_data_invalid():
    with pytest.raises(KeyError):
        CarBatteryData.from_dict({})
    with pytest.raises(TypeError):
        CarBatteryData.from_dict(None)


def test_car_odometer_data_polestar3(polestar3_test_data):
    data = CarOdometerData.from_dict(polestar3_test_data["getOdometerData"])
    assert data is not None
    assert isinstance(data, CarOdometerData)
    assert data.average_speed_km_per_hour == 42
    assert data.event_updated_timestamp.timestamp() == 1731338116
    assert data.trip_meter_automatic_km == 4.2
    assert data.trip_meter_manual_km == 1984.0
    assert data.odometer_meters == 2001000


def test_car_odometer_data_invalid():
    with pytest.raises(KeyError):
        CarOdometerData.from_dict({})
    with pytest.raises(TypeError):
        CarOdometerData.from_dict(None)


def test_telematics_information_data_polestar3(polestar3_test_data):
    data = CarTelematicsData.from_dict(polestar3_test_data["carTelematics"])
    assert data is not None
    assert isinstance(data, CarTelematicsData)
    assert isinstance(data.health, CarHealthData) or data.health is None
    assert isinstance(data.battery, CarBatteryData)
    assert isinstance(data.odometer, CarOdometerData)


def test_telematics_information_data_polestar2(polestar2_test_data):
    data = CarTelematicsData.from_dict(polestar2_test_data["carTelematics"])

    assert data is not None
    assert isinstance(data, CarTelematicsData)

    assert isinstance(data.health, CarHealthData)
    assert data.health.days_to_service == 196
    assert data.health.distance_to_service_km == 5227
    assert data.health.engine_coolant_level_warning == EngineCoolantLevelWarning.ENGINE_COOLANT_LEVEL_WARNING_NO_WARNING
    assert data.health.brake_fluid_level_warning == BrakeFluidLevelWarning.BRAKE_FLUID_LEVEL_WARNING_NO_WARNING
    assert data.health.oil_level_warning == OilLevelWarning.OIL_LEVEL_WARNING_NO_WARNING
    assert data.health.service_warning == ServiceWarning.SERVICE_WARNING_NO_WARNING

    assert isinstance(data.battery, CarBatteryData)
    assert isinstance(data.odometer, CarOdometerData)
