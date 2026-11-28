from enum import StrEnum
from typing import Any, Self


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
    CHARGING_STATUS_SMART_CHARGING_PAUSED = "Smart Charging Paused"


class BrakeFluidLevelWarning(StrEnumOptional):
    BRAKE_FLUID_LEVEL_WARNING_NO_WARNING = "No Warning"
    BRAKE_FLUID_LEVEL_WARNING_UNSPECIFIED = "Unspecified"
    BRAKE_FLUID_LEVEL_WARNING_TOO_LOW = "Too Low"
    # Only reported by the gRPC HealthService, not the GraphQL health query.
    BRAKE_FLUID_LEVEL_WARNING_CRITICALLY_LOW = "Critically Low"


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
    # Only reported by the gRPC HealthService, not the GraphQL health query
    # (combustion/hybrid "engine hours" variants and a generic unknown state).
    SERVICE_WARNING_UNKNOWN_WARNING = "Unknown Warning"
    SERVICE_WARNING_ENGINE_HOURS_ALMOST_TIME_FOR_SERVICE = "Engine Hours Almost Time For Service"
    SERVICE_WARNING_ENGINE_HOURS_TIME_FOR_SERVICE = "Engine Hours Time For Service"
    SERVICE_WARNING_ENGINE_HOURS_OVERDUE_FOR_SERVICE = "Engine Hours Overdue For Service"


class ChargingType(StrEnumOptional):
    CHARGING_TYPE_UNSPECIFIED = "Unspecified"
    CHARGING_TYPE_NONE = "None"
    CHARGING_TYPE_AC = "AC"
    CHARGING_TYPE_DC = "DC"
    CHARGING_TYPE_WIRELESS = "Wireless"


class ChargeTargetLevelSettingType(StrEnumOptional):
    CHARGE_TARGET_LEVEL_SETTING_TYPE_UNSPECIFIED = "Unspecified"
    DAILY = "Daily"
    LONG_TRIP = "Long Trip"
    CUSTOM = "Custom"
