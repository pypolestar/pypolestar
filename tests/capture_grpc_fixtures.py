#!/usr/bin/env python3
"""Capture and anonymize real gRPC responses for use as test fixtures.

Run with:
    POLESTAR_USERNAME=... POLESTAR_PASSWORD=... POLESTAR_VIN=... \
        python tests/capture_grpc_fixtures.py

This script:
  1. Authenticates against the real Polestar API
  2. Calls GetLatestBattery and GetTargetSoc
  3. Anonymizes identifying fields (vin, ids, source, timestamps) IN MEMORY
     before anything is written to disk
  4. Writes the anonymized re-serialized protobuf bytes to tests/data/

The captured .bin files are safe to commit: no real VIN, user id, or
request id is present in them. Battery telemetry values (SOC, range,
power) are preserved so the fixtures still represent realistic data.
"""

import asyncio
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pypolestar.auth import PolestarAuth  # noqa: E402
from pypolestar.grpc_client import PolestarGrpcClient  # noqa: E402
from pypolestar.proto import battery_service_pb2, chronos_request_pb2, target_soc_pb2  # noqa: E402

# Fixed anonymized values used in place of real identifiers.
ANON_VIN = "YSMYKEAE7RB000000"
ANON_ID = "00000000-0000-0000-0000-000000000000"
ANON_SOURCE = "test"
# 2024-01-01 00:00:00 UTC — fixed timestamp so the fixture is reproducible.
ANON_TIMESTAMP_SECONDS = 1704067200

DATA_DIR = Path(__file__).resolve().parent / "data"


def anonymize_battery_response(response: battery_service_pb2.GetBatteryResponse) -> None:
    """Strip identifiers from a GetBatteryResponse in place."""
    response.id = ANON_ID
    response.vin = ANON_VIN
    if response.HasField("battery") and response.battery.HasField("timestamp"):
        response.battery.timestamp.seconds = ANON_TIMESTAMP_SECONDS
        response.battery.timestamp.nanos = 0


def anonymize_target_soc_response(response: target_soc_pb2.GetTargetSocResponse) -> None:
    """Strip identifiers from a GetTargetSocResponse in place."""
    response.id = ANON_ID
    response.vin = ANON_VIN
    if response.HasField("target_soc"):
        _anonymize_target_soc(response.target_soc)
    if response.HasField("pending_target_soc"):
        _anonymize_target_soc(response.pending_target_soc)
    if response.HasField("updated_at"):
        response.updated_at = ANON_TIMESTAMP_SECONDS


def _anonymize_target_soc(ts: target_soc_pb2.TargetSoc) -> None:
    ts.id = ANON_ID
    ts.source = ANON_SOURCE
    ts.updated_at = ANON_TIMESTAMP_SECONDS


async def capture() -> None:
    username = os.environ["POLESTAR_USERNAME"]
    password = os.environ["POLESTAR_PASSWORD"]
    vin = os.environ["POLESTAR_VIN"]

    import httpx

    async with httpx.AsyncClient() as http_client:
        auth = PolestarAuth(username, password, http_client)
        await auth.async_init()
        await auth.get_token()
        assert auth.access_token, "failed to obtain access token"

        grpc_client = PolestarGrpcClient()
        await grpc_client.connect()
        try:
            battery_response = await _get_latest_battery(grpc_client, vin, auth.access_token)
            target_soc_response = await _get_target_soc(grpc_client, vin, auth.access_token)
        finally:
            await grpc_client.close()

    anonymize_battery_response(battery_response)
    anonymize_target_soc_response(target_soc_response)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    battery_path = DATA_DIR / "grpc_battery_response.bin"
    target_soc_path = DATA_DIR / "grpc_target_soc_response.bin"
    battery_path.write_bytes(battery_response.SerializeToString())
    target_soc_path.write_bytes(target_soc_response.SerializeToString())

    print(f"wrote {battery_path}")
    print(f"wrote {target_soc_path}")
    print()
    print("GetBatteryResponse (anonymized):")
    print(battery_response)
    print("GetTargetSocResponse (anonymized):")
    print(target_soc_response)


async def _get_latest_battery(
    client: PolestarGrpcClient, vin: str, access_token: str
) -> battery_service_pb2.GetBatteryResponse:
    request = battery_service_pb2.GetBatteryRequest(id=str(uuid.uuid4()), vin=vin)
    return await client.c3_channel.unary_unary(
        "/services.vehiclestates.battery.BatteryService/GetLatestBattery",
        request_serializer=battery_service_pb2.GetBatteryRequest.SerializeToString,
        response_deserializer=battery_service_pb2.GetBatteryResponse.FromString,
    )(request, metadata=client._metadata(access_token, vin), timeout=30)


async def _get_target_soc(
    client: PolestarGrpcClient, vin: str, access_token: str
) -> target_soc_pb2.GetTargetSocResponse:
    chronos_req = chronos_request_pb2.ChronosRequest(id=str(uuid.uuid4()), vin=vin, source="mobile")
    request = target_soc_pb2.GetTargetSocRequest(request=chronos_req)
    call = client.pccs_channel.unary_stream(
        "/pccs.chronos.services.v1.TargetSocService/GetTargetSoc",
        request_serializer=target_soc_pb2.GetTargetSocRequest.SerializeToString,
        response_deserializer=target_soc_pb2.GetTargetSocResponse.FromString,
    )(request, metadata=client._metadata(access_token, vin), timeout=30)
    async for msg in call:
        return msg
    raise RuntimeError("empty GetTargetSoc stream")


if __name__ == "__main__":
    asyncio.run(capture())
