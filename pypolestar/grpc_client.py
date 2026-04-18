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

from .grpc_models import (
    ChargeTargetLevelSettingType,
    ChargingConnectionStatus,
    ChargingStatus,
    ChargingType,
    GrpcBatteryData,
    GrpcTargetSocData,
)
from .proto import battery_pb2, battery_service_pb2, chronos_request_pb2, target_soc_pb2

_LOGGER = logging.getLogger(__name__)

# Discovery endpoint for C3 gRPC host (returns dynamic host/port)
C3_DISCOVERY_URL = "https://cnepmob.volvocars.com"
# Target SOC/charge timers come from Polestar's own PCCS platform
GRPC_PCCS_HOST = "api.pccs-prod.plstr.io"
GRPC_PORT = 443
GRPC_TIMEOUT = 30


def _connection_status(value: int) -> ChargingConnectionStatus:
    name = battery_pb2.ChargerConnectionStatus.Name(value)
    return ChargingConnectionStatus.get(name, ChargingConnectionStatus.CHARGER_CONNECTION_STATUS_UNSPECIFIED)


def _charging_status(value: int) -> ChargingStatus:
    name = battery_pb2.ChargingStatus.Name(value)
    return ChargingStatus.get(name, ChargingStatus.CHARGING_STATUS_UNSPECIFIED)


def _charging_type(value: int) -> ChargingType:
    name = battery_pb2.ChargingType.Name(value)
    return ChargingType.get(name, ChargingType.CHARGING_TYPE_UNSPECIFIED)


def _target_soc_setting_type(value: int) -> ChargeTargetLevelSettingType:
    name = target_soc_pb2.ChargeTargetLevelSettingType.Name(value)
    return ChargeTargetLevelSettingType.get(
        name, ChargeTargetLevelSettingType.CHARGE_TARGET_LEVEL_SETTING_TYPE_UNSPECIFIED
    )


class PolestarGrpcClient:
    """Client for the Polestar gRPC API (cnepmob.volvocars.com)."""

    def __init__(self, unique_id: str | None = None):
        self.c3_channel: grpc.aio.Channel | None = None
        self.pccs_channel: grpc.aio.Channel | None = None
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

    async def _discover_c3_host(self) -> tuple[str, int]:
        """Discover the C3 gRPC host via the cnepmob discovery endpoint."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
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

    async def get_battery(self, vin: str, access_token: str) -> GrpcBatteryData | None:
        """Get battery status including charger connection status via gRPC (C3/Volvo endpoint)."""
        if not self.c3_channel:
            raise RuntimeError("gRPC C3 channel not connected")

        request = battery_service_pb2.GetBatteryRequest(
            id=str(uuid.uuid4()),
            vin=vin,
        )

        try:
            # Battery service lives on C3 (cnepmob.volvocars.com) with shorter service path
            response = await self.c3_channel.unary_unary(
                "/services.vehiclestates.battery.BatteryService/GetLatestBattery",
                request_serializer=battery_service_pb2.GetBatteryRequest.SerializeToString,
                response_deserializer=battery_service_pb2.GetBatteryResponse.FromString,
            )(request, metadata=self._metadata(access_token, vin), timeout=GRPC_TIMEOUT)

            self.logger.debug("gRPC GetLatestBattery response: %s", response)

            if not response.HasField("battery"):
                self.logger.warning("gRPC GetLatestBattery: no battery field in response")
                return None

            return _parse_battery(response.battery)

        except grpc.aio.AioRpcError as exc:
            self.logger.error("gRPC GetLatestBattery failed: %s (code=%s)", exc.details(), exc.code())
            raise

    async def get_target_soc(self, vin: str, access_token: str) -> GrpcTargetSocData | None:
        """Get target SOC (charge limit) via gRPC (PCCS/Polestar endpoint)."""
        if not self.pccs_channel:
            raise RuntimeError("gRPC PCCS channel not connected")

        chronos_req = chronos_request_pb2.ChronosRequest(
            id=str(uuid.uuid4()),
            vin=vin,
            source="mobile",
        )
        request = target_soc_pb2.GetTargetSocRequest(request=chronos_req)

        try:
            # TargetSocService.GetTargetSoc is server-streaming; read first response
            call = self.pccs_channel.unary_stream(
                "/pccs.chronos.services.v1.TargetSocService/GetTargetSoc",
                request_serializer=target_soc_pb2.GetTargetSocRequest.SerializeToString,
                response_deserializer=target_soc_pb2.GetTargetSocResponse.FromString,
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
            self.logger.error("gRPC GetTargetSoc failed: %s (code=%s)", exc.details(), exc.code())
            raise


def _parse_battery(b: battery_pb2.Battery) -> GrpcBatteryData:
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


def _parse_target_soc(response: target_soc_pb2.GetTargetSocResponse) -> GrpcTargetSocData:
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
