import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import StrEnum
from functools import cached_property
from typing import Any, Self

from .utils import (
    GqlDict,
    get_field_name_date,
    get_field_name_datetime,
    get_field_name_float,
    get_field_name_int,
    get_field_name_str,
)


class StrEnumOptional(StrEnum):
    @classmethod
    def get(cls, key: Any, default: Self) -> Self:
        try:
            return cls[key]
        except KeyError:
            return default


class ChargingConnectionStatus(StrEnumOptional):
    CHARGER_CONNECTION_STATUS_CONNECTED = "Connected"
    CHARGER_CONNECTION_STATUS_DISCONNECTED = "Disconnected"
    CHARGER_CONNECTION_STATUS_FAULT = "Fault"
    CHARGER_CONNECTION_STATUS_UNSPECIFIED = "Unspecified"


class ChargingStatus(StrEnumOptional):
    CHARGING_STATUS_DONE = "Done"
    CHARGING_STATUS_IDLE = "Idle"
    CHARGING_STATUS_CHARGING = "Charging"
    CHARGING_STATUS_FAULT = "Fault"
    CHARGING_STATUS_UNSPECIFIED = "Unspecified"
    CHARGING_STATUS_SCHEDULED = "Scheduled"
    CHARGING_STATUS_DISCHARGING = "Discharging"
    CHARGING_STATUS_ERROR = "Error"
    CHARGING_STATUS_SMART_CHARGING = "Smart Charging"


class BrakeFluidLevelWarning(StrEnumOptional):
    BRAKE_FLUID_LEVEL_WARNING_NO_WARNING = "No Warning"
    BRAKE_FLUID_LEVEL_WARNING_UNSPECIFIED = "Unspecified"
    BRAKE_FLUID_LEVEL_WARNING_TOO_LOW = "Too Low"


class EngineCoolantLevelWarning(StrEnumOptional):
    ENGINE_COOLANT_LEVEL_WARNING_NO_WARNING = "No Warning"
    ENGINE_COOLANT_LEVEL_WARNING_UNSPECIFIED = "Unspecified"
    ENGINE_COOLANT_LEVEL_WARNING_TOO_LOW = "Too Low"


class OilLevelWarning(StrEnumOptional):
    OIL_LEVEL_WARNING_NO_WARNING = "No Warning"
    OIL_LEVEL_WARNING_UNSPECIFIED = "Unspecified"
    OIL_LEVEL_WARNING_TOO_LOW = "Too Low"
    OIL_LEVEL_WARNING_TOO_HIGH = "Too High"
    OIL_LEVEL_WARNING_SERVICE_REQUIRED = "Service Required"


class ServiceWarning(StrEnumOptional):
    SERVICE_WARNING_NO_WARNING = "No Warning"
    SERVICE_WARNING_UNSPECIFIED = "Unspecified"
    SERVICE_WARNING_SERVICE_REQUIRED = "Service Required"
    SERVICE_WARNING_REGULAR_MAINTENANCE_ALMOST_TIME_FOR_SERVICE = "Regular Maintenance Almost Time For Service"
    SERVICE_WARNING_DISTANCE_DRIVEN_ALMOST_TIME_FOR_SERVICE = "Distance Driven Almost Time For Service"
    SERVICE_WARNING_REGULAR_MAINTENANCE_TIME_FOR_SERVICE = "Regular Maintenance Time For Service"
    SERVICE_WARNING_DISTANCE_DRIVEN_TIME_FOR_SERVICE = "Distance Driven Time For Service"
    SERVICE_WARNING_REGULAR_MAINTENANCE_OVERDUE_FOR_SERVICE = "Regular Maintenance Overdue For Service"
    SERVICE_WARNING_DISTANCE_DRIVEN_OVERDUE_FOR_SERVICE = "Distance Driven Overdue For Service"


@dataclass(frozen=True)
class CarBaseInformation:
    _received_timestamp: datetime


@dataclass(frozen=True)
class CarBatteryInformationData:
    voltage: int | None
    capacity: int | None
    modules: int | None

    # Examples: "78 kWh", "78.3 kWh", "78.3 KWH"
    _CAPACITY_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*(?:kwh|kWh|KWH)", re.IGNORECASE)

    # Examples: "400V", "400 V"
    _VOLTAGE_PATTERN = re.compile(r"(\d+)\s*V", re.IGNORECASE)

    # Examples: "27 modules", "27 Modules"
    _MODULES_PATTERN = re.compile(r"(\d+)\s*modules?", re.IGNORECASE)

    @classmethod
    def from_battery_str(cls, battery_information: str) -> Self:
        if match := cls._CAPACITY_PATTERN.search(battery_information):
            capacity = int(match.group(1))
        else:
            capacity = None

        if match := cls._VOLTAGE_PATTERN.search(battery_information):
            voltage = int(match.group(1))
        else:
            voltage = None

        if match := cls._MODULES_PATTERN.search(battery_information):
            modules = int(match.group(1))
        else:
            modules = None

        return cls(voltage=voltage, capacity=capacity, modules=modules)


@dataclass(frozen=True)
class CarInformationData(CarBaseInformation):
    vin: str | None
    internal_vehicle_identifier: str | None
    registration_no: str | None
    registration_date: date | None
    factory_complete_date: date | None
    model_name: str | None
    image_url: str | None
    battery: str | None
    torque: str | None
    software_version: str | None
    software_version_timestamp: datetime | None

    _TORQUE_PATTERN = re.compile(r"(\d+)(?:\s*Nm|\s*N·m|\s*N⋅m)", re.IGNORECASE)

    @cached_property
    def battery_information(self) -> CarBatteryInformationData | None:
        return CarBatteryInformationData.from_battery_str(self.battery) if self.battery else None

    @cached_property
    def torque_nm(self) -> int | None:
        if self.torque and (match := self._TORQUE_PATTERN.search(self.torque)):
            return int(match.group(1))
        return None

    @classmethod
    def from_dict(cls, data: GqlDict) -> Self:
        if not isinstance(data, dict):
            raise TypeError

        return cls(
            vin=get_field_name_str("vin", data),
            internal_vehicle_identifier=get_field_name_str("internalVehicleIdentifier", data),
            registration_no=get_field_name_str("registrationNo", data),
            registration_date=get_field_name_date("registrationDate", data),
            factory_complete_date=get_field_name_date("factoryCompleteDate", data),
            model_name=get_field_name_str("content/model/name", data),
            image_url=get_field_name_str("content/images/studio/url", data),
            battery=get_field_name_str("content/specification/battery", data),
            torque=get_field_name_str("content/specification/torque", data),
            software_version=get_field_name_str("software/version", data),
            software_version_timestamp=get_field_name_datetime("software/versionTimestamp", data),
            _received_timestamp=datetime.now(tz=timezone.utc),
        )


@dataclass(frozen=True)
class CarOdometerData(CarBaseInformation):
    average_speed_km_per_hour: int | None
    odometer_meters: int | None
    trip_meter_automatic_km: float | None
    trip_meter_manual_km: float | None
    event_updated_timestamp: datetime | None

    @classmethod
    def from_dict(cls, data: GqlDict) -> Self:
        if not isinstance(data, dict):
            raise TypeError

        return cls(
            average_speed_km_per_hour=get_field_name_int("averageSpeedKmPerHour", data),
            odometer_meters=get_field_name_int("odometerMeters", data),
            trip_meter_automatic_km=get_field_name_float("tripMeterAutomaticKm", data),
            trip_meter_manual_km=get_field_name_float("tripMeterManualKm", data),
            event_updated_timestamp=get_field_name_datetime("eventUpdatedTimestamp/iso", data),
            _received_timestamp=datetime.now(tz=timezone.utc),
        )


@dataclass(frozen=True)
class CarBatteryData(CarBaseInformation):
    average_energy_consumption_kwh_per_100km: float | None
    battery_charge_level_percentage: int | None
    charger_connection_status: ChargingConnectionStatus
    charging_current_amps: int
    charging_power_watts: int
    charging_status: ChargingStatus
    estimated_charging_time_minutes_to_target_distance: int | None
    estimated_charging_time_to_full_minutes: int | None
    estimated_distance_to_empty_km: int | None
    event_updated_timestamp: datetime | None

    @property
    def estimated_full_charge_range_km(self) -> float | None:
        """
        Calculate the estimated range at 100% charge based on current charge level and range.

        Formula: (current_range / current_percentage) * 100
        Example: If battery is at 50% with 150km range, full range would be (150/50)*100 = 300km
        """
        if (
            self.battery_charge_level_percentage is not None
            and self.estimated_distance_to_empty_km is not None
            and 0 < self.battery_charge_level_percentage <= 100
            and self.estimated_distance_to_empty_km >= 0
        ):
            return round(
                self.estimated_distance_to_empty_km / self.battery_charge_level_percentage * 100,
                2,
            )

    @property
    def estimated_fully_charged(self) -> datetime | None:
        """Calculate estimated completion time based on remaining charge time.

        Returns None if:
        - Charging time is not available
        - Vehicle is not actively charging
        - Battery is already fully charged
        """
        if (
            self.estimated_charging_time_to_full_minutes
            and self.estimated_charging_time_to_full_minutes > 0
            and self.battery_charge_level_percentage is not None
            and self.battery_charge_level_percentage < 100
        ):
            return datetime.now(tz=timezone.utc).replace(second=0, microsecond=0) + timedelta(
                minutes=self.estimated_charging_time_to_full_minutes
            )
        return None

    @classmethod
    def from_dict(cls, data: GqlDict) -> Self:
        if not isinstance(data, dict):
            raise TypeError

        charger_connection_status = ChargingConnectionStatus.get(
            data["chargerConnectionStatus"],
            ChargingConnectionStatus.CHARGER_CONNECTION_STATUS_UNSPECIFIED,
        )

        charging_status = ChargingStatus.get(
            data["chargingStatus"],
            ChargingStatus.CHARGING_STATUS_UNSPECIFIED,
        )

        return cls(
            average_energy_consumption_kwh_per_100km=get_field_name_float("averageEnergyConsumptionKwhPer100Km", data),
            battery_charge_level_percentage=get_field_name_int("batteryChargeLevelPercentage", data),
            charger_connection_status=charger_connection_status,
            charging_current_amps=get_field_name_int("chargingCurrentAmps", data) or 0,
            charging_power_watts=get_field_name_int("chargingPowerWatts", data) or 0,
            charging_status=charging_status,
            estimated_charging_time_minutes_to_target_distance=get_field_name_int(
                "estimatedChargingTimeMinutesToTargetDistance", data
            ),
            estimated_charging_time_to_full_minutes=get_field_name_int("estimatedChargingTimeToFullMinutes", data),
            estimated_distance_to_empty_km=get_field_name_int("estimatedDistanceToEmptyKm", data),
            event_updated_timestamp=get_field_name_datetime("eventUpdatedTimestamp/iso", data),
            _received_timestamp=datetime.now(tz=timezone.utc),
        )


@dataclass(frozen=True)
class CarHealthData(CarBaseInformation):
    brake_fluid_level_warning: BrakeFluidLevelWarning
    days_to_service: int | None
    distance_to_service_km: int | None
    engine_coolant_level_warning: EngineCoolantLevelWarning
    oil_level_warning: OilLevelWarning
    service_warning: ServiceWarning
    event_updated_timestamp: datetime | None

    @classmethod
    def from_dict(cls, data: GqlDict) -> Self:
        if not isinstance(data, dict):
            raise TypeError

        brake_fluid_level_warning = BrakeFluidLevelWarning.get(
            data["brakeFluidLevelWarning"],
            BrakeFluidLevelWarning.BRAKE_FLUID_LEVEL_WARNING_UNSPECIFIED,
        )
        engine_coolant_level_warning = EngineCoolantLevelWarning.get(
            data["engineCoolantLevelWarning"],
            EngineCoolantLevelWarning.ENGINE_COOLANT_LEVEL_WARNING_UNSPECIFIED,
        )
        oil_level_warning = OilLevelWarning.get(
            data["oilLevelWarning"],
            OilLevelWarning.OIL_LEVEL_WARNING_UNSPECIFIED,
        )
        service_warning = ServiceWarning.get(
            data["serviceWarning"],
            ServiceWarning.SERVICE_WARNING_UNSPECIFIED,
        )

        return cls(
            brake_fluid_level_warning=brake_fluid_level_warning,
            days_to_service=get_field_name_int("daysToService", data),
            distance_to_service_km=get_field_name_int("distanceToServiceKm", data),
            engine_coolant_level_warning=engine_coolant_level_warning,
            oil_level_warning=oil_level_warning,
            service_warning=service_warning,
            event_updated_timestamp=get_field_name_datetime("eventUpdatedTimestamp/iso", data),
            _received_timestamp=datetime.now(tz=timezone.utc),
        )


@dataclass(frozen=True)
class CarTelematicsData(CarBaseInformation):
    health: CarHealthData | None
    battery: CarBatteryData | None
    odometer: CarOdometerData | None

    @classmethod
    def from_dict(cls, data: GqlDict) -> Self:
        if not isinstance(data, dict):
            raise TypeError

        health = data.get("health")
        battery = data.get("battery")
        odometer = data.get("odometer")

        return cls(
            health=(CarHealthData.from_dict(health) if isinstance(health, dict) else None),
            battery=(CarBatteryData.from_dict(battery) if isinstance(battery, dict) else None),
            odometer=(CarOdometerData.from_dict(odometer) if isinstance(odometer, dict) else None),
            _received_timestamp=datetime.now(tz=timezone.utc),
        )
