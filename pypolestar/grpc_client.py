"""gRPC client for Polestar Connected Car Services (PCCS).

This module communicates with the Volvo/Polestar gRPC API (cnepmob.volvocars.com)
to retrieve data not available through the GraphQL API, including:
- Charger connection status (connected/disconnected)
- Charging power, current, voltage
- Target SOC (charge limit)

Protocol definitions reconstructed from Polestar Android app v5.5.0.
"""

import logging
import uuid
from datetime import datetime, timezone

import grpc
import grpc.aio
import httpx

from .enum import BrakeFluidLevelWarning, EngineCoolantLevelWarning, OilLevelWarning, ServiceWarning
from .grpc_models import (
    AlarmStatus,
    AvailabilityStatus,
    ChargeTargetLevelSettingType,
    ChargingConnectionStatus,
    ChargingStatus,
    ChargingType,
    ClimateRunningStatus,
    ExteriorLightWarning,
    GrpcAmpLimitData,
    GrpcAvailabilityData,
    GrpcBatteryData,
    GrpcChargeScheduleData,
    GrpcClimateData,
    GrpcExteriorData,
    GrpcHealthData,
    GrpcLocationData,
    GrpcMyCarsData,
    GrpcOdometerData,
    GrpcPreCleaningData,
    GrpcTargetSocData,
    HeatingIntensity,
    LockStatus,
    LowVoltageBatteryWarning,
    MainClimateRunningStatus,
    OpenStatus,
    PreCleaningRunningStatus,
    TyrePressureWarning,
    UnavailableReason,
    UsageMode,
    Ventilation,
    WasherFluidLevelWarning,
)
from .proto import (
    polestar_amplimit_pb2,
    polestar_availability_pb2,
    polestar_availability_service_pb2,
    polestar_battery_pb2,
    polestar_battery_service_pb2,
    polestar_chargetimer_pb2,
    polestar_chargetimer_service_pb2,
    polestar_chronos_request_pb2,
    polestar_exterior_pb2,
    polestar_exterior_service_pb2,
    polestar_health_pb2,
    polestar_health_service_pb2,
    polestar_location_pb2,
    polestar_location_service_pb2,
    polestar_mycars_pb2,
    polestar_mycars_service_pb2,
    polestar_odometer_pb2,
    polestar_odometer_service_pb2,
    polestar_parkingclimatization_pb2,
    polestar_parkingclimatization_service_pb2,
    polestar_precleaning_pb2,
    polestar_precleaning_service_pb2,
    polestar_target_soc_pb2,
)

_LOGGER = logging.getLogger(__name__)

# Discovery endpoint for C3 gRPC host (returns dynamic host/port)
C3_DISCOVERY_URL = "https://cnepmob.volvocars.com"
# Target SOC/charge timers come from Polestar's own PCCS platform
GRPC_PCCS_HOST = "api.pccs-prod.plstr.io"
GRPC_PORT = 443
GRPC_TIMEOUT = 30

# gRPC status codes that mean "this vehicle will never serve this data".
#
# Not all vehicles are provisioned in every backend: the Polestar 2 is a Volvo
# CMA-platform car and is not registered in Polestar's own PCCS/chronos
# platform, so TargetSocService returns PERMISSION_DENIED for it even though
# the access token is perfectly valid (an unauthenticated call is rejected
# earlier, at the gateway, with UNAUTHENTICATED). Retrying such a call on every
# poll only produces log noise, so we remember the VIN and stop asking.
UNSUPPORTED_STATUS_CODES = frozenset(
    {
        grpc.StatusCode.PERMISSION_DENIED,
        grpc.StatusCode.UNIMPLEMENTED,
        grpc.StatusCode.NOT_FOUND,
    }
)


def _connection_status(value: int) -> ChargingConnectionStatus:
    name = polestar_battery_pb2.ChargerConnectionStatus.Name(value)
    return ChargingConnectionStatus.get(name, ChargingConnectionStatus.CHARGER_CONNECTION_STATUS_UNSPECIFIED)


def _charging_status(value: int) -> ChargingStatus:
    name = polestar_battery_pb2.ChargingStatus.Name(value)
    return ChargingStatus.get(name, ChargingStatus.CHARGING_STATUS_UNSPECIFIED)


def _charging_type(value: int) -> ChargingType:
    name = polestar_battery_pb2.ChargingType.Name(value)
    return ChargingType.get(name, ChargingType.CHARGING_TYPE_UNSPECIFIED)


def _target_soc_setting_type(value: int) -> ChargeTargetLevelSettingType:
    name = polestar_target_soc_pb2.ChargeTargetLevelSettingType.Name(value)
    return ChargeTargetLevelSettingType.get(
        name, ChargeTargetLevelSettingType.CHARGE_TARGET_LEVEL_SETTING_TYPE_UNSPECIFIED
    )


def _lock_status(value: int) -> LockStatus:
    name = polestar_exterior_pb2.LockStatus.Name(value)
    return LockStatus.get(name, LockStatus.LOCK_STATUS_UNSPECIFIED)


def _open_status(value: int) -> OpenStatus:
    name = polestar_exterior_pb2.OpenStatus.Name(value)
    return OpenStatus.get(name, OpenStatus.OPEN_STATUS_UNSPECIFIED)


def _alarm_status(value: int) -> AlarmStatus:
    name = polestar_exterior_pb2.AlarmStatus.Name(value)
    return AlarmStatus.get(name, AlarmStatus.ALARM_STATUS_UNSPECIFIED)


def _service_warning(value: int) -> ServiceWarning:
    name = polestar_health_pb2.ServiceWarning.Name(value)
    return ServiceWarning.get(name, ServiceWarning.SERVICE_WARNING_UNSPECIFIED)


def _brake_fluid_level_warning(value: int) -> BrakeFluidLevelWarning:
    name = polestar_health_pb2.BrakeFluidLevelWarning.Name(value)
    return BrakeFluidLevelWarning.get(name, BrakeFluidLevelWarning.BRAKE_FLUID_LEVEL_WARNING_UNSPECIFIED)


def _engine_coolant_level_warning(value: int) -> EngineCoolantLevelWarning:
    name = polestar_health_pb2.EngineCoolantLevelWarning.Name(value)
    return EngineCoolantLevelWarning.get(name, EngineCoolantLevelWarning.ENGINE_COOLANT_LEVEL_WARNING_UNSPECIFIED)


def _oil_level_warning(value: int) -> OilLevelWarning:
    name = polestar_health_pb2.OilLevelWarning.Name(value)
    return OilLevelWarning.get(name, OilLevelWarning.OIL_LEVEL_WARNING_UNSPECIFIED)


def _climate_running_status(value: int) -> ClimateRunningStatus:
    name = polestar_parkingclimatization_pb2.RunningStatus.Name(value)
    return ClimateRunningStatus.get(name, ClimateRunningStatus.RUNNING_STATUS_UNSPECIFIED)


def _main_climate_running_status(value: int) -> MainClimateRunningStatus:
    name = polestar_parkingclimatization_pb2.MainClimateRunningStatus.Name(value)
    return MainClimateRunningStatus.get(name, MainClimateRunningStatus.MAIN_CLIMATE_RUNNING_STATUS_UNSPECIFIED)


def _heating_intensity(value: int) -> HeatingIntensity:
    name = polestar_parkingclimatization_pb2.HeatingIntensity.Name(value)
    return HeatingIntensity.get(name, HeatingIntensity.HEATING_INTENSITY_UNSPECIFIED)


def _ventilation(value: int) -> Ventilation:
    name = polestar_parkingclimatization_pb2.Ventilation.Name(value)
    return Ventilation.get(name, Ventilation.VENTILATION_UNSPECIFIED)


def _availability_status(value: int) -> AvailabilityStatus:
    name = polestar_availability_pb2.AvailabilityStatus.Name(value)
    return AvailabilityStatus.get(name, AvailabilityStatus.AVAILABILITY_STATUS_UNSPECIFIED)


def _unavailable_reason(value: int) -> UnavailableReason:
    name = polestar_availability_pb2.UnavailableReason.Name(value)
    return UnavailableReason.get(name, UnavailableReason.UNAVAILABLE_REASON_UNSPECIFIED)


def _usage_mode(value: int) -> UsageMode:
    name = polestar_availability_pb2.UsageMode.Name(value)
    return UsageMode.get(name, UsageMode.USAGE_MODE_UNSPECIFIED)


def _precleaning_running_status(value: int) -> PreCleaningRunningStatus:
    name = polestar_precleaning_pb2.RunningStatus.Name(value)
    return PreCleaningRunningStatus.get(name, PreCleaningRunningStatus.RUNNING_STATUS_UNSPECIFIED)


def _timestamp(msg) -> datetime:
    """Convert any of the per-service Timestamp{seconds, nanos} messages.

    Callers guard with `parent.HasField("field")` first; presence for
    message-type fields is tracked by proto3 even though it isn't for plain
    scalars.
    """
    return datetime.fromtimestamp(
        msg.seconds + msg.nanos / 1_000_000_000,
        tz=timezone.utc,
    )


class PolestarGrpcClient:
    """Client for the Polestar gRPC API (cnepmob.volvocars.com)."""

    def __init__(self, client_session: httpx.AsyncClient, unique_id: str | None = None):
        self.client_session = client_session
        self.c3_channel: grpc.aio.Channel | None = None
        self.pccs_channel: grpc.aio.Channel | None = None
        self.unsupported_battery: set[str] = set()
        self.unsupported_target_soc: set[str] = set()
        self.unsupported_exterior: set[str] = set()
        self.unsupported_health: set[str] = set()
        self.unsupported_odometer: set[str] = set()
        self.unsupported_climate: set[str] = set()
        self.unsupported_availability: set[str] = set()
        self.unsupported_precleaning: set[str] = set()
        self.unsupported_location: set[str] = set()
        self.unsupported_mycars: set[str] = set()
        self.unsupported_amp_limit: set[str] = set()
        self.unsupported_charge_schedule: set[str] = set()
        self.logger = _LOGGER.getChild(unique_id) if unique_id else _LOGGER

    async def connect(self) -> None:
        """Connect to both gRPC servers."""
        creds = grpc.ssl_channel_credentials()

        # Discover C3 gRPC host dynamically
        c3_host, c3_port = await self._discover_c3_host()
        c3_target = f"{c3_host}:{c3_port}"
        self.c3_channel = grpc.aio.secure_channel(c3_target, creds)
        self.logger.debug("gRPC C3 channel created for %s", c3_target)

        pccs_target = f"{GRPC_PCCS_HOST}:{GRPC_PORT}"
        self.pccs_channel = grpc.aio.secure_channel(pccs_target, creds)
        self.logger.debug("gRPC PCCS channel created for %s", pccs_target)

        # Only now that fresh channels are in place is it worth re-evaluating
        # per-vehicle support; a failed reconnect keeps the old channels, and
        # with them what we already learned about these vehicles.
        self.unsupported_battery.clear()
        self.unsupported_target_soc.clear()
        self.unsupported_exterior.clear()
        self.unsupported_health.clear()
        self.unsupported_odometer.clear()
        self.unsupported_climate.clear()
        self.unsupported_availability.clear()
        self.unsupported_precleaning.clear()
        self.unsupported_location.clear()
        self.unsupported_mycars.clear()
        self.unsupported_amp_limit.clear()
        self.unsupported_charge_schedule.clear()

    async def _discover_c3_host(self) -> tuple[str, int]:
        """Discover the C3 gRPC host via the cnepmob discovery endpoint."""
        resp = await self.client_session.get(
            C3_DISCOVERY_URL,
            headers={"Accept": "application/volvo.cloud.cnepmob.v1+json"},
        )
        resp.raise_for_status()
        data = resp.json()
        c3 = data["c3"]
        host = c3["grpcHost"]
        port = c3["grpcPort"]
        self.logger.debug("C3 gRPC discovered: %s:%d", host, port)
        return host, port

    async def close(self) -> None:
        """Close the gRPC channels."""
        if self.c3_channel:
            await self.c3_channel.close()
            self.c3_channel = None
        if self.pccs_channel:
            await self.pccs_channel.close()
            self.pccs_channel = None

    def _metadata(self, access_token: str, vin: str) -> list[tuple[str, str]]:
        return [
            ("authorization", f"Bearer {access_token}"),
            ("vin", vin),
        ]

    def _mark_unsupported(self, unsupported: set[str], vin: str, what: str, exc: grpc.aio.AioRpcError) -> bool:
        """Remember that a vehicle does not provide this data, if the error says so permanently."""
        if exc.code() not in UNSUPPORTED_STATUS_CODES:
            return False
        unsupported.add(vin)
        self.logger.info(
            "gRPC %s not available for this vehicle (%s), not retrying",
            what,
            exc.code().name,
        )
        return True

    def is_battery_supported(self, vin: str) -> bool:
        """Whether the C3 battery service is known to serve data for this vehicle."""
        return vin not in self.unsupported_battery

    def is_target_soc_supported(self, vin: str) -> bool:
        """Whether the PCCS target SOC service is known to serve data for this vehicle."""
        return vin not in self.unsupported_target_soc

    def is_exterior_supported(self, vin: str) -> bool:
        """Whether the C3 exterior (doors/windows/locks) service is known to serve this vehicle."""
        return vin not in self.unsupported_exterior

    def is_health_supported(self, vin: str) -> bool:
        """Whether the C3 health (tyre pressure/light warnings) service is known to serve this vehicle."""
        return vin not in self.unsupported_health

    def is_odometer_supported(self, vin: str) -> bool:
        """Whether the C3 odometer (trip meters) service is known to serve this vehicle."""
        return vin not in self.unsupported_odometer

    def is_climate_supported(self, vin: str) -> bool:
        """Whether the C3 parking climatization service is known to serve this vehicle."""
        return vin not in self.unsupported_climate

    def is_availability_supported(self, vin: str) -> bool:
        """Whether the C3 availability service is known to serve this vehicle."""
        return vin not in self.unsupported_availability

    def is_precleaning_supported(self, vin: str) -> bool:
        """Whether the C3 pre-cleaning service is known to serve this vehicle."""
        return vin not in self.unsupported_precleaning

    def is_location_supported(self, vin: str) -> bool:
        """Whether the C3 location service is known to serve this vehicle."""
        return vin not in self.unsupported_location

    def is_mycars_supported(self, vin: str) -> bool:
        """Whether the C3 car_information.CarInformation/GetMyCars service is known to serve this vehicle."""
        return vin not in self.unsupported_mycars

    def is_amp_limit_supported(self, vin: str) -> bool:
        """Whether the PCCS amp limit service is known to serve this vehicle."""
        return vin not in self.unsupported_amp_limit

    def is_charge_schedule_supported(self, vin: str) -> bool:
        """Whether the PCCS global charge timer service is known to serve this vehicle."""
        return vin not in self.unsupported_charge_schedule

    async def get_battery(self, vin: str, access_token: str) -> GrpcBatteryData | None:
        """Get battery status including charger connection status via gRPC (C3/Volvo endpoint)."""
        if not self.c3_channel:
            raise RuntimeError("gRPC C3 channel not connected")

        if vin in self.unsupported_battery:
            return None

        request = polestar_battery_service_pb2.GetBatteryRequest(
            id=str(uuid.uuid4()),
            vin=vin,
        )

        try:
            # Battery service lives on C3 (cnepmob.volvocars.com) with shorter service path
            response = await self.c3_channel.unary_unary(
                "/services.vehiclestates.battery.BatteryService/GetLatestBattery",
                request_serializer=polestar_battery_service_pb2.GetBatteryRequest.SerializeToString,
                response_deserializer=polestar_battery_service_pb2.GetBatteryResponse.FromString,
            )(request, metadata=self._metadata(access_token, vin), timeout=GRPC_TIMEOUT)

            self.logger.debug("gRPC GetLatestBattery response: %s", response)

            if not response.HasField("battery"):
                self.logger.warning("gRPC GetLatestBattery: no battery field in response")
                return None

            return _parse_battery(response.battery)

        except grpc.aio.AioRpcError as exc:
            if self._mark_unsupported(self.unsupported_battery, vin, "battery data", exc):
                return None
            self.logger.error("gRPC GetLatestBattery failed: %s (code=%s)", exc.details(), exc.code())
            raise

    async def get_target_soc(self, vin: str, access_token: str) -> GrpcTargetSocData | None:
        """Get target SOC (charge limit) via gRPC (PCCS/Polestar endpoint)."""
        if not self.pccs_channel:
            raise RuntimeError("gRPC PCCS channel not connected")

        if vin in self.unsupported_target_soc:
            return None

        chronos_req = polestar_chronos_request_pb2.ChronosRequest(
            id=str(uuid.uuid4()),
            vin=vin,
            source="mobile",
        )
        request = polestar_target_soc_pb2.GetTargetSocRequest(request=chronos_req)

        try:
            # TargetSocService.GetTargetSoc is server-streaming; read first response
            call = self.pccs_channel.unary_stream(
                "/pccs.chronos.services.v1.TargetSocService/GetTargetSoc",
                request_serializer=polestar_target_soc_pb2.GetTargetSocRequest.SerializeToString,
                response_deserializer=polestar_target_soc_pb2.GetTargetSocResponse.FromString,
            )(request, metadata=self._metadata(access_token, vin), timeout=GRPC_TIMEOUT)

            response = None
            async for msg in call:
                response = msg
                break  # We only need the first response

            if response is None:
                self.logger.warning("gRPC GetTargetSoc: empty stream")
                return None

            self.logger.debug("gRPC GetTargetSoc response: %s", response)

            return _parse_target_soc(response)

        except grpc.aio.AioRpcError as exc:
            if self._mark_unsupported(self.unsupported_target_soc, vin, "target SOC", exc):
                return None
            self.logger.error("gRPC GetTargetSoc failed: %s (code=%s)", exc.details(), exc.code())
            raise

    async def get_exterior(self, vin: str, access_token: str) -> GrpcExteriorData | None:
        """Get doors/windows/locks status via gRPC (C3 endpoint, best-effort)."""
        if not self.c3_channel:
            raise RuntimeError("gRPC C3 channel not connected")

        if vin in self.unsupported_exterior:
            return None

        request = polestar_exterior_service_pb2.GetExteriorRequest(id=str(uuid.uuid4()), vin=vin)

        try:
            response = await self.c3_channel.unary_unary(
                "/services.vehiclestates.exterior.ExteriorService/GetLatestExterior",
                request_serializer=polestar_exterior_service_pb2.GetExteriorRequest.SerializeToString,
                response_deserializer=polestar_exterior_service_pb2.GetExteriorResponse.FromString,
            )(request, metadata=self._metadata(access_token, vin), timeout=GRPC_TIMEOUT)

            self.logger.debug("gRPC GetLatestExterior response: %s", response)

            if not response.HasField("exterior"):
                self.logger.warning("gRPC GetLatestExterior: no exterior field in response")
                return None

            return _parse_exterior(response.exterior)

        except grpc.aio.AioRpcError as exc:
            if self._mark_unsupported(self.unsupported_exterior, vin, "exterior data", exc):
                return None
            self.logger.error("gRPC GetLatestExterior failed: %s (code=%s)", exc.details(), exc.code())
            raise

    async def get_health(self, vin: str, access_token: str) -> GrpcHealthData | None:
        """Get per-tyre pressure and light warnings via gRPC (C3 endpoint, best-effort).

        Only a streaming method is confirmed for this service, so (like
        get_target_soc) only the first streamed message is read.
        """
        if not self.c3_channel:
            raise RuntimeError("gRPC C3 channel not connected")

        if vin in self.unsupported_health:
            return None

        request = polestar_health_service_pb2.GetHealthRequest(id=str(uuid.uuid4()), vin=vin)

        try:
            call = self.c3_channel.unary_stream(
                "/services.vehiclestates.health.HealthService/GetHealth",
                request_serializer=polestar_health_service_pb2.GetHealthRequest.SerializeToString,
                response_deserializer=polestar_health_service_pb2.GetHealthResponse.FromString,
            )(request, metadata=self._metadata(access_token, vin), timeout=GRPC_TIMEOUT)

            response = None
            async for msg in call:
                response = msg
                break

            if response is None or not response.HasField("health"):
                self.logger.warning("gRPC GetHealth: empty stream or no health field")
                return None

            self.logger.debug("gRPC GetHealth response: %s", response)

            return _parse_health(response.health)

        except grpc.aio.AioRpcError as exc:
            if self._mark_unsupported(self.unsupported_health, vin, "health data", exc):
                return None
            self.logger.error("gRPC GetHealth failed: %s (code=%s)", exc.details(), exc.code())
            raise

    async def get_odometer(self, vin: str, access_token: str) -> GrpcOdometerData | None:
        """Get trip meters and average speed via gRPC (C3 endpoint, best-effort).

        Only a streaming method is confirmed for this service; first message only.
        """
        if not self.c3_channel:
            raise RuntimeError("gRPC C3 channel not connected")

        if vin in self.unsupported_odometer:
            return None

        request = polestar_odometer_service_pb2.GetOdometerRequest(id=str(uuid.uuid4()), vin=vin)

        try:
            call = self.c3_channel.unary_stream(
                "/services.vehiclestates.odometer.OdometerService/GetOdometer",
                request_serializer=polestar_odometer_service_pb2.GetOdometerRequest.SerializeToString,
                response_deserializer=polestar_odometer_service_pb2.GetOdometerResponse.FromString,
            )(request, metadata=self._metadata(access_token, vin), timeout=GRPC_TIMEOUT)

            response = None
            async for msg in call:
                response = msg
                break

            if response is None or not response.HasField("odometer"):
                self.logger.warning("gRPC GetOdometer: empty stream or no odometer field")
                return None

            self.logger.debug("gRPC GetOdometer response: %s", response)

            return _parse_odometer(response.odometer)

        except grpc.aio.AioRpcError as exc:
            if self._mark_unsupported(self.unsupported_odometer, vin, "odometer data", exc):
                return None
            self.logger.error("gRPC GetOdometer failed: %s (code=%s)", exc.details(), exc.code())
            raise

    async def get_climate(self, vin: str, access_token: str) -> GrpcClimateData | None:
        """Get parking climatization status via gRPC (C3 endpoint, best-effort)."""
        if not self.c3_channel:
            raise RuntimeError("gRPC C3 channel not connected")

        if vin in self.unsupported_climate:
            return None

        request = polestar_parkingclimatization_service_pb2.GetParkingClimatizationRequest(
            id=str(uuid.uuid4()), vin=vin
        )

        try:
            response = await self.c3_channel.unary_unary(
                "/services.vehiclestates.parkingclimatization.ParkingClimatizationService/GetLatestParkingClimatization",
                request_serializer=(
                    polestar_parkingclimatization_service_pb2.GetParkingClimatizationRequest.SerializeToString
                ),
                response_deserializer=(
                    polestar_parkingclimatization_service_pb2.GetParkingClimatizationResponse.FromString
                ),
            )(request, metadata=self._metadata(access_token, vin), timeout=GRPC_TIMEOUT)

            self.logger.debug("gRPC GetLatestParkingClimatization response: %s", response)

            if not response.HasField("parking_climatization"):
                self.logger.warning("gRPC GetLatestParkingClimatization: no parking_climatization field")
                return None

            return _parse_climate(response.parking_climatization)

        except grpc.aio.AioRpcError as exc:
            if self._mark_unsupported(self.unsupported_climate, vin, "climate data", exc):
                return None
            self.logger.error("gRPC GetLatestParkingClimatization failed: %s (code=%s)", exc.details(), exc.code())
            raise

    async def get_availability(self, vin: str, access_token: str) -> GrpcAvailabilityData | None:
        """Get vehicle online/awake availability via gRPC (C3 endpoint, best-effort)."""
        if not self.c3_channel:
            raise RuntimeError("gRPC C3 channel not connected")

        if vin in self.unsupported_availability:
            return None

        request = polestar_availability_service_pb2.GetAvailabilityRequest(id=str(uuid.uuid4()), vin=vin)

        try:
            response = await self.c3_channel.unary_unary(
                "/services.vehiclestates.availability.AvailabilityService/GetLatestAvailability",
                request_serializer=polestar_availability_service_pb2.GetAvailabilityRequest.SerializeToString,
                response_deserializer=polestar_availability_service_pb2.GetAvailabilityResponse.FromString,
            )(request, metadata=self._metadata(access_token, vin), timeout=GRPC_TIMEOUT)

            self.logger.debug("gRPC GetLatestAvailability response: %s", response)

            if not response.HasField("availability"):
                self.logger.warning("gRPC GetLatestAvailability: no availability field")
                return None

            return _parse_availability(response.availability)

        except grpc.aio.AioRpcError as exc:
            if self._mark_unsupported(self.unsupported_availability, vin, "availability data", exc):
                return None
            self.logger.error("gRPC GetLatestAvailability failed: %s (code=%s)", exc.details(), exc.code())
            raise

    async def get_precleaning(self, vin: str, access_token: str) -> GrpcPreCleaningData | None:
        """Get cabin air pre-cleaning status via gRPC (C3 endpoint, best-effort).

        Only a streaming method is confirmed for this service; first message only.
        """
        if not self.c3_channel:
            raise RuntimeError("gRPC C3 channel not connected")

        if vin in self.unsupported_precleaning:
            return None

        request = polestar_precleaning_service_pb2.GetPreCleaningRequest(id=str(uuid.uuid4()), vin=vin)

        try:
            call = self.c3_channel.unary_stream(
                "/services.vehiclestates.precleaning.PreCleaningService/GetPreCleaning",
                request_serializer=polestar_precleaning_service_pb2.GetPreCleaningRequest.SerializeToString,
                response_deserializer=polestar_precleaning_service_pb2.GetPreCleaningResponse.FromString,
            )(request, metadata=self._metadata(access_token, vin), timeout=GRPC_TIMEOUT)

            response = None
            async for msg in call:
                response = msg
                break

            if response is None or not response.HasField("pre_cleaning"):
                self.logger.warning("gRPC GetPreCleaning: empty stream or no pre_cleaning field")
                return None

            self.logger.debug("gRPC GetPreCleaning response: %s", response)

            return _parse_precleaning(response.pre_cleaning)

        except grpc.aio.AioRpcError as exc:
            if self._mark_unsupported(self.unsupported_precleaning, vin, "pre-cleaning data", exc):
                return None
            self.logger.error("gRPC GetPreCleaning failed: %s (code=%s)", exc.details(), exc.code())
            raise

    async def get_location(self, vin: str, access_token: str) -> GrpcLocationData | None:
        """Get last known GPS location via gRPC (C3 endpoint, best-effort)."""
        if not self.c3_channel:
            raise RuntimeError("gRPC C3 channel not connected")

        if vin in self.unsupported_location:
            return None

        request = polestar_location_service_pb2.LastParkedLocationRequest(vin=vin)

        try:
            response = await self.c3_channel.unary_unary(
                "/dtlinternet.DtlInternetService/GetLastParkedLocation",
                request_serializer=polestar_location_service_pb2.LastParkedLocationRequest.SerializeToString,
                response_deserializer=polestar_location_pb2.LastParkedLocation.FromString,
            )(request, metadata=self._metadata(access_token, vin), timeout=GRPC_TIMEOUT)

            self.logger.debug("gRPC GetLastParkedLocation response: %s", response)

            if not response.HasField("location"):
                self.logger.warning("gRPC GetLastParkedLocation: no location field")
                return None

            return _parse_location(response)

        except grpc.aio.AioRpcError as exc:
            if self._mark_unsupported(self.unsupported_location, vin, "location data", exc):
                return None
            self.logger.error("gRPC GetLastParkedLocation failed: %s (code=%s)", exc.details(), exc.code())
            raise

    async def get_mycars(self, vin: str, access_token: str) -> GrpcMyCarsData | None:
        """Get vehicle identity + installed software version via gRPC (C3 endpoint).

        Unlike the other best-effort services here, this one was reverse-
        engineered directly against a real account by decoding the raw
        response, then live-tested end to end -- no external schema for it
        existed anywhere to cross-reference.
        """
        if not self.c3_channel:
            raise RuntimeError("gRPC C3 channel not connected")

        if vin in self.unsupported_mycars:
            return None

        request = polestar_mycars_service_pb2.GetMyCarsRequest(id=str(uuid.uuid4()), vin=vin)

        try:
            response = await self.c3_channel.unary_unary(
                "/car_information.CarInformation/GetMyCars",
                request_serializer=polestar_mycars_service_pb2.GetMyCarsRequest.SerializeToString,
                response_deserializer=polestar_mycars_pb2.GetMyCarsResponse.FromString,
            )(request, metadata=self._metadata(access_token, vin), timeout=GRPC_TIMEOUT)

            self.logger.debug("gRPC GetMyCars response: %s", response)

            matching = next((car for car in response.cars if car.details.vin == vin), None)
            if matching is None and len(response.cars) == 1:
                # Single-car accounts sometimes don't echo the vin back on
                # every nested field; fall back to the only entry present.
                matching = response.cars[0]
            if matching is None:
                self.logger.warning("gRPC GetMyCars: no matching car in response")
                return None

            return _parse_mycars(matching)

        except grpc.aio.AioRpcError as exc:
            if self._mark_unsupported(self.unsupported_mycars, vin, "mycars data", exc):
                return None
            self.logger.error("gRPC GetMyCars failed: %s (code=%s)", exc.details(), exc.code())
            raise

    async def get_amp_limit(self, vin: str, access_token: str) -> GrpcAmpLimitData | None:
        """Get charging current limit via gRPC (PCCS endpoint, best-effort).

        Live-tested: a real call received one valid message immediately, then
        held the connection open past a 15s deadline rather than closing --
        read the same way as get_target_soc, first message only.
        """
        if not self.pccs_channel:
            raise RuntimeError("gRPC PCCS channel not connected")

        if vin in self.unsupported_amp_limit:
            return None

        chronos_req = polestar_chronos_request_pb2.ChronosRequest(id=str(uuid.uuid4()), vin=vin, source="mobile")
        request = polestar_amplimit_pb2.GetAmpLimitRequest(request=chronos_req)

        try:
            call = self.pccs_channel.unary_stream(
                "/pccs.chronos.services.v1.AmpLimitService/GetAmpLimit",
                request_serializer=polestar_amplimit_pb2.GetAmpLimitRequest.SerializeToString,
                response_deserializer=polestar_amplimit_pb2.GetAmpLimitResponse.FromString,
            )(request, metadata=self._metadata(access_token, vin), timeout=GRPC_TIMEOUT)

            response = None
            async for msg in call:
                response = msg
                break

            if response is None:
                self.logger.warning("gRPC GetAmpLimit: empty stream")
                return None

            self.logger.debug("gRPC GetAmpLimit response: %s", response)

            return _parse_amp_limit(response)

        except grpc.aio.AioRpcError as exc:
            if self._mark_unsupported(self.unsupported_amp_limit, vin, "amp limit", exc):
                return None
            self.logger.error("gRPC GetAmpLimit failed: %s (code=%s)", exc.details(), exc.code())
            raise

    async def get_charge_schedule(self, vin: str, access_token: str) -> GrpcChargeScheduleData | None:
        """Get the overnight charging window via gRPC (PCCS endpoint, best-effort).

        Live-tested the same way as get_amp_limit: one message then a
        long-lived open stream, so only the first message is read.
        """
        if not self.pccs_channel:
            raise RuntimeError("gRPC PCCS channel not connected")

        if vin in self.unsupported_charge_schedule:
            return None

        chronos_req = polestar_chronos_request_pb2.ChronosRequest(id=str(uuid.uuid4()), vin=vin, source="mobile")
        request = polestar_chargetimer_service_pb2.GetGlobalChargeTimerStreamRequest(request=chronos_req)

        try:
            call = self.pccs_channel.unary_stream(
                "/pccs.chronos.services.v2.GlobalChargeTimerService/GetGlobalChargeTimerStream",
                request_serializer=polestar_chargetimer_service_pb2.GetGlobalChargeTimerStreamRequest.SerializeToString,
                response_deserializer=polestar_chargetimer_pb2.GetGlobalChargeTimerStreamResponse.FromString,
            )(request, metadata=self._metadata(access_token, vin), timeout=GRPC_TIMEOUT)

            response = None
            async for msg in call:
                response = msg
                break

            if response is None or not response.HasField("timer"):
                self.logger.warning("gRPC GetGlobalChargeTimerStream: empty stream or no timer field")
                return None

            self.logger.debug("gRPC GetGlobalChargeTimerStream response: %s", response)

            return _parse_charge_schedule(response)

        except grpc.aio.AioRpcError as exc:
            if self._mark_unsupported(self.unsupported_charge_schedule, vin, "charge schedule", exc):
                return None
            self.logger.error("gRPC GetGlobalChargeTimerStream failed: %s (code=%s)", exc.details(), exc.code())
            raise


def _parse_battery(b: polestar_battery_pb2.Battery) -> GrpcBatteryData:
    """Parse a Battery protobuf message into GrpcBatteryData."""
    ts: datetime | None = None
    if b.HasField("timestamp"):
        ts = datetime.fromtimestamp(b.timestamp.seconds, tz=timezone.utc)

    return GrpcBatteryData(
        charger_connection_status=_connection_status(b.charger_connection_status),
        charging_status=_charging_status(b.charging_status),
        battery_charge_level_percentage=b.battery_charge_level_percentage,
        estimated_distance_to_empty_km=b.estimated_distance_to_empty_km,
        estimated_charging_time_to_full_minutes=b.estimated_charging_time_to_full_minutes,
        charging_power_watts=b.charging_power_watts,
        charging_current_amps=b.charging_current_amps,
        charging_voltage_volts=b.charging_voltage_volts,
        charging_type=_charging_type(b.charging_type),
        average_energy_consumption_kwh_per_100km=b.average_energy_consumption_kwh_per_100_km,
        estimated_charging_time_minutes_to_target_distance=b.estimated_charging_time_minutes_to_target_distance,
        estimated_charging_time_minutes_to_minimum_soc=b.estimated_charging_time_minutes_to_minimum_soc,
        timestamp=ts,
    )


def _parse_target_soc(response: polestar_target_soc_pb2.GetTargetSocResponse) -> GrpcTargetSocData:
    """Parse a GetTargetSocResponse into GrpcTargetSocData."""
    target_level: int | None = None
    target_type = ChargeTargetLevelSettingType.CHARGE_TARGET_LEVEL_SETTING_TYPE_UNSPECIFIED
    pending_level: int | None = None
    pending_type = ChargeTargetLevelSettingType.CHARGE_TARGET_LEVEL_SETTING_TYPE_UNSPECIFIED

    if response.HasField("target_soc"):
        ts = response.target_soc
        target_level = ts.battery_charge_target_level
        target_type = _target_soc_setting_type(ts.charge_target_level_setting_type)

    if response.HasField("pending_target_soc"):
        pts = response.pending_target_soc
        pending_level = pts.battery_charge_target_level
        pending_type = _target_soc_setting_type(pts.charge_target_level_setting_type)

    return GrpcTargetSocData(
        battery_charge_target_level=target_level,
        charge_target_level_setting_type=target_type,
        pending_battery_charge_target_level=pending_level,
        pending_charge_target_level_setting_type=pending_type,
    )


def _parse_exterior(e: polestar_exterior_pb2.Exterior) -> GrpcExteriorData:
    """Parse an Exterior protobuf message into GrpcExteriorData."""
    return GrpcExteriorData(
        central_lock=_lock_status(e.central_lock),
        tailgate_lock=_lock_status(e.tailgate_lock),
        front_left_door=_open_status(e.front_left_door),
        front_right_door=_open_status(e.front_right_door),
        rear_left_door=_open_status(e.rear_left_door),
        rear_right_door=_open_status(e.rear_right_door),
        front_left_window=_open_status(e.front_left_window),
        front_right_window=_open_status(e.front_right_window),
        rear_left_window=_open_status(e.rear_left_window),
        rear_right_window=_open_status(e.rear_right_window),
        hood=_open_status(e.hood),
        tailgate=_open_status(e.tailgate),
        tank_lid=_open_status(e.tank_lid),
        sunroof=_open_status(e.sunroof),
        alarm=_alarm_status(e.alarm),
        timestamp=_timestamp(e.timestamp) if e.HasField("timestamp") else None,
    )


# (light name, protobuf field name) pairs -- kept in one place so
# _parse_health and any future consumer stay in sync.
_HEALTH_LIGHT_WARNING_FIELDS = (
    ("brake_light_left", "brake_light_left_warning"),
    ("brake_light_center", "brake_light_center_warning"),
    ("brake_light_right", "brake_light_right_warning"),
    ("fog_light_front", "fog_light_front_warning"),
    ("fog_light_rear", "fog_light_rear_warning"),
    ("position_light_front_left", "position_light_front_left_warning"),
    ("position_light_front_right", "position_light_front_right_warning"),
    ("position_light_rear_left", "position_light_rear_left_warning"),
    ("position_light_rear_right", "position_light_rear_right_warning"),
    ("high_beam_left", "high_beam_left_warning"),
    ("high_beam_right", "high_beam_right_warning"),
    ("low_beam_left", "low_beam_left_warning"),
    ("low_beam_right", "low_beam_right_warning"),
    ("daytime_running_light_left", "daytime_running_light_left_warning"),
    ("daytime_running_light_right", "daytime_running_light_right_warning"),
    ("turn_indication_front_left", "turn_indication_front_left_warning"),
    ("turn_indication_front_right", "turn_indication_front_right_warning"),
    ("turn_indication_rear_left", "turn_indication_rear_left_warning"),
    ("turn_indication_rear_right", "turn_indication_rear_right_warning"),
    ("registration_plate_light", "registration_plate_light_warning"),
    ("side_mark_lights", "side_mark_lights_warning"),
)


def _exterior_light_warning(value: int) -> ExteriorLightWarning:
    name = polestar_health_pb2.ExteriorLightWarning.Name(value)
    return ExteriorLightWarning.get(name, ExteriorLightWarning.EXTERIOR_LIGHT_WARNING_UNSPECIFIED)


def _parse_health(h: polestar_health_pb2.Health) -> GrpcHealthData:
    """Parse a Health protobuf message into GrpcHealthData."""

    def _tyre_pressure_warning(value: int) -> TyrePressureWarning:
        name = polestar_health_pb2.TyrePressureWarning.Name(value)
        return TyrePressureWarning.get(name, TyrePressureWarning.TYRE_PRESSURE_WARNING_UNSPECIFIED)

    return GrpcHealthData(
        days_to_service=h.days_to_service,
        distance_to_service_km=h.distance_to_service_km,
        engine_hours_to_service=h.engine_hours_to_service,
        service_warning=_service_warning(h.service_warning),
        brake_fluid_level_warning=_brake_fluid_level_warning(h.brake_fluid_level_warning),
        engine_coolant_level_warning=_engine_coolant_level_warning(h.engine_coolant_level_warning),
        oil_level_warning=_oil_level_warning(h.oil_level_warning),
        washer_fluid_level_warning=WasherFluidLevelWarning.get(
            polestar_health_pb2.WasherFluidLevelWarning.Name(h.washer_fluid_level_warning),
            WasherFluidLevelWarning.WASHER_FLUID_LEVEL_WARNING_UNSPECIFIED,
        ),
        low_voltage_battery_warning=LowVoltageBatteryWarning.get(
            polestar_health_pb2.LowVoltageBatteryWarning.Name(h.low_voltage_battery_warning),
            LowVoltageBatteryWarning.LOW_VOLTAGE_BATTERY_WARNING_UNSPECIFIED,
        ),
        front_left_tyre_pressure_warning=_tyre_pressure_warning(h.front_left_tyre_pressure_warning),
        front_right_tyre_pressure_warning=_tyre_pressure_warning(h.front_right_tyre_pressure_warning),
        rear_left_tyre_pressure_warning=_tyre_pressure_warning(h.rear_left_tyre_pressure_warning),
        rear_right_tyre_pressure_warning=_tyre_pressure_warning(h.rear_right_tyre_pressure_warning),
        front_left_tyre_pressure_kpa=h.front_left_tyre_pressure_kpa,
        front_right_tyre_pressure_kpa=h.front_right_tyre_pressure_kpa,
        rear_left_tyre_pressure_kpa=h.rear_left_tyre_pressure_kpa,
        rear_right_tyre_pressure_kpa=h.rear_right_tyre_pressure_kpa,
        front_tyres_reference_pressure_kpa=h.front_tyres_reference_pressure_kpa,
        rear_tyres_reference_pressure_kpa=h.rear_tyres_reference_pressure_kpa,
        exterior_light_warnings={
            name: _exterior_light_warning(getattr(h, field)) for name, field in _HEALTH_LIGHT_WARNING_FIELDS
        },
        timestamp=_timestamp(h.timestamp) if h.HasField("timestamp") else None,
    )


def _parse_odometer(o: polestar_odometer_pb2.Odometer) -> GrpcOdometerData:
    """Parse an Odometer protobuf message into GrpcOdometerData."""
    return GrpcOdometerData(
        odometer_meters=o.odometer_meters,
        trip_meter_manual_km=o.trip_meter_manual_km,
        trip_meter_automatic_km=o.trip_meter_automatic_km,
        trip_meter_since_charge_km=o.trip_meter_since_charge_km,
        average_speed_km_per_hour=o.average_speed_km_per_hour,
        average_speed_km_per_hour_automatic=o.average_speed_km_per_hour_automatic,
        average_speed_km_per_hour_since_charge=o.average_speed_km_per_hour_since_charge,
        timestamp=_timestamp(o.timestamp) if o.HasField("timestamp") else None,
    )


def _parse_climate(c: polestar_parkingclimatization_pb2.ParkingClimatization) -> GrpcClimateData:
    """Parse a ParkingClimatization protobuf message into GrpcClimateData."""
    return GrpcClimateData(
        running_status=_climate_running_status(c.running_status),
        main_climate_running_status=_main_climate_running_status(c.main_climate_running_status),
        ventilation=_ventilation(c.ventilation),
        runtime_left_minutes=c.runtime_left_minutes,
        current_compartment_temperature_celsius=c.current_compartment_temperature_celsius,
        requested_compartment_temperature_celsius=c.requested_compartment_temperature_celsius,
        requested_front_left_seat=_heating_intensity(c.requested_front_left_seat),
        requested_front_right_seat=_heating_intensity(c.requested_front_right_seat),
        requested_rear_left_seat=_heating_intensity(c.requested_rear_left_seat),
        requested_rear_right_seat=_heating_intensity(c.requested_rear_right_seat),
        requested_steering_wheel_heating=_heating_intensity(c.requested_steering_wheel_heating),
        started_at=_timestamp(c.started_at) if c.HasField("started_at") else None,
        ending_at=_timestamp(c.ending_at) if c.HasField("ending_at") else None,
        timestamp=_timestamp(c.timestamp) if c.HasField("timestamp") else None,
    )


def _parse_availability(a: polestar_availability_pb2.Availability) -> GrpcAvailabilityData:
    """Parse an Availability protobuf message into GrpcAvailabilityData."""
    return GrpcAvailabilityData(
        availability_status=_availability_status(a.availability_status),
        unavailable_reason=_unavailable_reason(a.unavailable_reason),
        usage_mode=_usage_mode(a.usage_mode),
        timestamp=_timestamp(a.timestamp) if a.HasField("timestamp") else None,
    )


def _parse_precleaning(p: polestar_precleaning_pb2.PreCleaning) -> GrpcPreCleaningData:
    """Parse a PreCleaning protobuf message into GrpcPreCleaningData."""
    return GrpcPreCleaningData(
        running_status=_precleaning_running_status(p.running_status),
        runtime_left_minutes=p.runtime_left_minutes,
        measured_air_quality_index=p.measured_air_quality_index,
        measured_particulate_matter_25=p.measured_particulate_matter_25,
        last_cycle_valid=p.last_cycle_valid,
        started_at=_timestamp(p.started_at) if p.HasField("started_at") else None,
        ending_at=_timestamp(p.ending_at) if p.HasField("ending_at") else None,
        timestamp=_timestamp(p.timestamp) if p.HasField("timestamp") else None,
    )


def _parse_location(response: polestar_location_pb2.LastParkedLocation) -> GrpcLocationData:
    """Parse a LastParkedLocation protobuf message into GrpcLocationData."""
    location = response.location
    return GrpcLocationData(
        latitude=location.latitude,
        longitude=location.longitude,
        stale=response.stale,
        timestamp=_timestamp(location.timestamp) if location.HasField("timestamp") else None,
    )


def _timestamp_millis(value: int) -> datetime | None:
    """Convert an epoch-milliseconds int64 (this family's other timestamp shape,
    seen in AmpLimitReading/ChronosRequest-adjacent messages instead of the
    {seconds, nanos} Timestamp message used elsewhere)."""
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc) if value else None


def _parse_mycars(car: polestar_mycars_pb2.MyCarEntry) -> GrpcMyCarsData:
    """Parse a MyCarEntry protobuf message into GrpcMyCarsData."""
    details = car.details
    return GrpcMyCarsData(
        vin=details.vin or None,
        model_name=details.model_name or None,
        model_year=details.model_year or None,
        installed_software_version=details.installed_software_version or None,
        market=details.market or None,
        registration_no=car.registration_no or None,
    )


def _parse_amp_limit(response: polestar_amplimit_pb2.GetAmpLimitResponse) -> GrpcAmpLimitData:
    """Parse a GetAmpLimitResponse protobuf message into GrpcAmpLimitData."""
    value = response.amp_limit.value if response.HasField("amp_limit") else None
    pending_value = response.pending_amp_limit.value if response.HasField("pending_amp_limit") else None
    return GrpcAmpLimitData(
        value=value,
        pending_value=pending_value,
        updated_at=_timestamp_millis(response.updated_at),
    )


def _parse_charge_schedule(
    response: polestar_chargetimer_pb2.GetGlobalChargeTimerStreamResponse,
) -> GrpcChargeScheduleData:
    """Parse a GetGlobalChargeTimerStreamResponse protobuf message into GrpcChargeScheduleData."""
    timer = response.timer
    return GrpcChargeScheduleData(
        start_hour=timer.start.hour if timer.HasField("start") else None,
        end_hour=timer.end.hour if timer.HasField("end") else None,
        updated_at=_timestamp_millis(response.updated_at),
    )
