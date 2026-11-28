"""Asynchronous Python client for the Polestar API.""" ""

import asyncio
import logging
import time
from collections import defaultdict
from typing import Any

import httpx
from gql.client import AsyncClientSession
from gql.transport.exceptions import TransportQueryError
from graphql import DocumentNode

from .auth import PolestarAuth
from .const import (
    API_MYSTAR_LOCALE,
    API_MYSTAR_PUBLIC_API_KEY,
    API_MYSTAR_PUBLIC_URL,
    API_MYSTAR_V2_URL,
    CAR_IMAGES_DATA,
    CAR_INFO_DATA,
    GRPC_AMP_LIMIT_DATA,
    GRPC_AVAILABILITY_DATA,
    GRPC_BATTERY_DATA,
    GRPC_CHARGE_SCHEDULE_DATA,
    GRPC_CLIMATE_DATA,
    GRPC_EXTERIOR_DATA,
    GRPC_HEALTH_DATA,
    GRPC_LOCATION_DATA,
    GRPC_MYCARS_DATA,
    GRPC_ODOMETER_DATA,
    GRPC_PRECLEANING_DATA,
    GRPC_TARGET_SOC_DATA,
    TELEMATICS_DATA,
)
from .exceptions import (
    PolestarApiException,
    PolestarAuthException,
    PolestarNoDataException,
    PolestarNotAuthorizedException,
)
from .graphql import (
    QUERY_GET_CAR_IMAGES,
    QUERY_GET_CONSUMER_CARS_V2,
    QUERY_TELEMATICS_V2,
    get_gql_client,
    get_gql_session,
)
from .grpc_client import PolestarGrpcClient
from .grpc_models import (
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
)
from .models import CarDataCollection, CarImagesData, CarInformationData, CarTelematicsData

_LOGGER = logging.getLogger(__name__)


class PolestarApi:
    """Main class for handling connections with the Polestar API."""

    def __init__(
        self,
        username: str,
        password: str,
        client_session: httpx.AsyncClient | None = None,
        vins: list[str] | None = None,
        unique_id: str | None = None,
        public_api_key: str | None = None,
        enable_grpc: bool = True,
    ) -> None:
        """Initialize the Polestar API."""

        self.client_session = client_session or httpx.AsyncClient()
        self.username = username
        self.auth = PolestarAuth(username, password, self.client_session, unique_id)
        self.updating_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.latest_call_code: int | None = None
        self.data_by_vin: dict[str, dict[str, Any]] = defaultdict(dict)
        self.configured_vins = set(vins) if vins else None
        self.available_vins: set[str] = set()
        self.logger = _LOGGER.getChild(unique_id) if unique_id else _LOGGER

        self.api_url_private = API_MYSTAR_V2_URL
        self.api_url_public = API_MYSTAR_PUBLIC_URL

        self.public_api_key = public_api_key or API_MYSTAR_PUBLIC_API_KEY

        self.gql_client_private = get_gql_client(url=self.api_url_private, client=self.client_session)
        self.gql_client_public = get_gql_client(url=self.api_url_public, client=self.client_session)

        self.gql_session_private: AsyncClientSession | None = None
        self.gql_session_public: AsyncClientSession | None = None

        self.grpc_client = (
            PolestarGrpcClient(client_session=self.client_session, unique_id=unique_id) if enable_grpc else None
        )

    async def async_init(self, verbose: bool = False) -> None:
        """Initialize the Polestar API."""

        if verbose:
            self.logger.warning("Verbose mode no longer supported, ignoring verbose=True")

        await self.auth.async_init()
        await self.auth.get_token()

        if self.auth.access_token is None:
            raise PolestarAuthException(f"No access token for {self.username}")

        self.gql_session_private = await get_gql_session(self.gql_client_private)
        self.gql_session_public = await get_gql_session(self.gql_client_public)

        if self.grpc_client:
            try:
                await self.grpc_client.connect()
                self.logger.debug("gRPC client connected")
            except Exception as exc:
                self.logger.warning("gRPC client connection failed (non-fatal): %s", exc)

        if not (car_data := await self._get_all_vehicles_data()):
            self.logger.warning("No cars found for %s", self.username)
            return

        for data in car_data:
            vin = data["vin"]
            if self.configured_vins and vin not in self.configured_vins:
                continue
            self.data_by_vin[vin][CAR_INFO_DATA] = data
            try:
                self.data_by_vin[vin][CAR_IMAGES_DATA] = await self._get_car_images(vin)
            except Exception as exc:
                self.logger.warning("Failed to get car images for VIN %s: %s", vin, exc)
            self.available_vins.add(vin)
            self.logger.debug("API setup for VIN %s", vin)

        if self.configured_vins and (missing_vins := self.configured_vins - self.available_vins):
            self.logger.warning("Could not found configured VINs %s", missing_vins)

    async def async_logout(self) -> None:
        """Log out from Polestar API."""
        if self.grpc_client:
            await self.grpc_client.close()
        await self.auth.async_logout()

    def get_status_code(self) -> int | None:
        """Return HTTP-like status code"""
        return self.latest_call_code

    def get_available_vins(self) -> list[str]:
        """Get list of all available VINs"""
        return list(self.available_vins)

    def get_data(self, vin: str) -> CarDataCollection:
        """Get the data collection for the specified VIN. Raises KeyError if VIN not available."""

        if vin not in self.available_vins:
            raise KeyError(f"VIN {vin} not available")

        return CarDataCollection(
            car_information=self.get_car_information(vin),
            car_telematics=self.get_car_telematics(vin),
            car_images=self.get_car_images(vin),
            battery_data=self.get_grpc_battery(vin),
            target_soc=self.get_grpc_target_soc(vin),
            grpc_exterior=self.get_grpc_exterior(vin),
            grpc_health=self.get_grpc_health(vin),
            grpc_odometer=self.get_grpc_odometer(vin),
            grpc_climate=self.get_grpc_climate(vin),
            grpc_availability=self.get_grpc_availability(vin),
            grpc_precleaning=self.get_grpc_precleaning(vin),
            grpc_location=self.get_grpc_location(vin),
            grpc_mycars=self.get_grpc_mycars(vin),
            grpc_amp_limit=self.get_grpc_amp_limit(vin),
            grpc_charge_schedule=self.get_grpc_charge_schedule(vin),
        )

    def get_car_information(self, vin: str) -> CarInformationData | None:
        """
        Get car information for the specified VIN.

        Args:
            vin: The vehicle identification number
        Returns:
            CarInformationData if data exists, None otherwise
        Raises:
            KeyError: If the VIN doesn't exist
            ValueError: If data conversion fails
        """

        self._ensure_data_for_vin(vin)

        if data := self.data_by_vin[vin].get(CAR_INFO_DATA):
            try:
                return CarInformationData.from_dict(data)
            except Exception as exc:
                raise ValueError("Failed to convert car information data") from exc

    def get_car_telematics(self, vin: str) -> CarTelematicsData | None:
        """
        Get car telematics for the specified VIN.

        Args:
            vin: The vehicle identification number
        Returns:
            CarTelematicsData if data exists, None otherwise
        Raises:
            KeyError: If the VIN doesn't exist
            ValueError: If data conversion fails
        """

        self._ensure_data_for_vin(vin)

        if data := self.data_by_vin[vin].get(TELEMATICS_DATA):
            try:
                return CarTelematicsData.from_dict(data, vin)
            except Exception as exc:
                raise ValueError("Failed to convert car telematics data") from exc

    def get_car_images(self, vin: str) -> CarImagesData | None:
        """
        Get car images for the specified VIN.

        Args:
            vin: The vehicle identification number
        Returns:
            CarImagesData if data exists, None otherwise
        Raises:
            KeyError: If the VIN doesn't exist
            ValueError: If data conversion fails
        """

        self._ensure_data_for_vin(vin)

        if data := self.data_by_vin[vin].get(CAR_IMAGES_DATA):
            try:
                return CarImagesData.from_dict(data)
            except Exception as exc:
                raise ValueError("Failed to convert car images data") from exc

    def get_grpc_battery(self, vin: str) -> GrpcBatteryData | None:
        """Get battery data from gRPC API (includes charger connection status, power, etc.)."""
        self._ensure_data_for_vin(vin)
        return self.data_by_vin[vin].get(GRPC_BATTERY_DATA)

    def get_grpc_target_soc(self, vin: str) -> GrpcTargetSocData | None:
        """Get target SOC (charge limit) from gRPC API."""
        self._ensure_data_for_vin(vin)
        return self.data_by_vin[vin].get(GRPC_TARGET_SOC_DATA)

    def is_grpc_battery_supported(self, vin: str) -> bool:
        """Whether the vehicle serves battery data via gRPC (false once the backend has refused it)."""
        return self.grpc_client.is_battery_supported(vin) if self.grpc_client else False

    def is_grpc_target_soc_supported(self, vin: str) -> bool:
        """Whether the vehicle serves target SOC via gRPC.

        Not every vehicle is provisioned in Polestar's PCCS platform -- notably the
        Polestar 2 -- and those return PERMISSION_DENIED. This turns false after the
        first such refusal, so consumers can drop the entity instead of showing it
        permanently unknown.
        """
        return self.grpc_client.is_target_soc_supported(vin) if self.grpc_client else False

    def get_grpc_exterior(self, vin: str) -> GrpcExteriorData | None:
        """Get doors/windows/locks status from gRPC API (best-effort)."""
        self._ensure_data_for_vin(vin)
        return self.data_by_vin[vin].get(GRPC_EXTERIOR_DATA)

    def is_grpc_exterior_supported(self, vin: str) -> bool:
        """Whether the vehicle serves exterior data via gRPC."""
        return self.grpc_client.is_exterior_supported(vin) if self.grpc_client else False

    def get_grpc_health(self, vin: str) -> GrpcHealthData | None:
        """Get per-tyre pressure and light warnings from gRPC API (best-effort)."""
        self._ensure_data_for_vin(vin)
        return self.data_by_vin[vin].get(GRPC_HEALTH_DATA)

    def is_grpc_health_supported(self, vin: str) -> bool:
        """Whether the vehicle serves health data via gRPC."""
        return self.grpc_client.is_health_supported(vin) if self.grpc_client else False

    def get_grpc_odometer(self, vin: str) -> GrpcOdometerData | None:
        """Get trip meters and average speed from gRPC API (best-effort)."""
        self._ensure_data_for_vin(vin)
        return self.data_by_vin[vin].get(GRPC_ODOMETER_DATA)

    def is_grpc_odometer_supported(self, vin: str) -> bool:
        """Whether the vehicle serves odometer data via gRPC."""
        return self.grpc_client.is_odometer_supported(vin) if self.grpc_client else False

    def get_grpc_climate(self, vin: str) -> GrpcClimateData | None:
        """Get parking climatization status from gRPC API (best-effort)."""
        self._ensure_data_for_vin(vin)
        return self.data_by_vin[vin].get(GRPC_CLIMATE_DATA)

    def is_grpc_climate_supported(self, vin: str) -> bool:
        """Whether the vehicle serves climate data via gRPC."""
        return self.grpc_client.is_climate_supported(vin) if self.grpc_client else False

    def get_grpc_availability(self, vin: str) -> GrpcAvailabilityData | None:
        """Get vehicle online/awake availability from gRPC API (best-effort)."""
        self._ensure_data_for_vin(vin)
        return self.data_by_vin[vin].get(GRPC_AVAILABILITY_DATA)

    def is_grpc_availability_supported(self, vin: str) -> bool:
        """Whether the vehicle serves availability data via gRPC."""
        return self.grpc_client.is_availability_supported(vin) if self.grpc_client else False

    def get_grpc_precleaning(self, vin: str) -> GrpcPreCleaningData | None:
        """Get cabin air pre-cleaning status from gRPC API (best-effort)."""
        self._ensure_data_for_vin(vin)
        return self.data_by_vin[vin].get(GRPC_PRECLEANING_DATA)

    def is_grpc_precleaning_supported(self, vin: str) -> bool:
        """Whether the vehicle serves pre-cleaning data via gRPC."""
        return self.grpc_client.is_precleaning_supported(vin) if self.grpc_client else False

    def get_grpc_location(self, vin: str) -> GrpcLocationData | None:
        """Get last known GPS location from gRPC API (best-effort)."""
        self._ensure_data_for_vin(vin)
        return self.data_by_vin[vin].get(GRPC_LOCATION_DATA)

    def is_grpc_location_supported(self, vin: str) -> bool:
        """Whether the vehicle serves location data via gRPC."""
        return self.grpc_client.is_location_supported(vin) if self.grpc_client else False

    def get_grpc_mycars(self, vin: str) -> GrpcMyCarsData | None:
        """Get vehicle identity + installed software version from gRPC API.

        Reverse-engineered directly against a real account and live-tested
        end to end (unlike most other gRPC methods here, this one's schema
        was not cross-referenced from another project).
        """
        self._ensure_data_for_vin(vin)
        return self.data_by_vin[vin].get(GRPC_MYCARS_DATA)

    def is_grpc_mycars_supported(self, vin: str) -> bool:
        """Whether the vehicle serves data via gRPC car_information.CarInformation/GetMyCars."""
        return self.grpc_client.is_mycars_supported(vin) if self.grpc_client else False

    def get_grpc_amp_limit(self, vin: str) -> GrpcAmpLimitData | None:
        """Get charging current limit from gRPC API (best-effort)."""
        self._ensure_data_for_vin(vin)
        return self.data_by_vin[vin].get(GRPC_AMP_LIMIT_DATA)

    def is_grpc_amp_limit_supported(self, vin: str) -> bool:
        """Whether the vehicle serves amp limit data via gRPC."""
        return self.grpc_client.is_amp_limit_supported(vin) if self.grpc_client else False

    def get_grpc_charge_schedule(self, vin: str) -> GrpcChargeScheduleData | None:
        """Get the overnight charging window from gRPC API (best-effort)."""
        self._ensure_data_for_vin(vin)
        return self.data_by_vin[vin].get(GRPC_CHARGE_SCHEDULE_DATA)

    def is_grpc_charge_schedule_supported(self, vin: str) -> bool:
        """Whether the vehicle serves a charge schedule via gRPC."""
        return self.grpc_client.is_charge_schedule_supported(vin) if self.grpc_client else False

    async def update_latest_data(
        self,
        vin: str,
        update_vehicle: bool = False,
        update_telematics: bool = True,
        update_grpc: bool = True,
    ) -> None:
        """Get the latest data from the Polestar API."""

        self._ensure_data_for_vin(vin)

        if self.updating_locks[vin].locked():
            self.logger.debug("Skipping update for VIN %s, already in progress", vin)
            return

        async with self.updating_locks[vin]:
            try:
                await self.auth.get_token()

                self.logger.debug("Starting update for VIN %s", vin)
                t1 = time.perf_counter()

                if update_vehicle:
                    await self._update_vehicle_data(vin)
                if update_telematics:
                    await self._update_telematics_data(vin)
                if update_grpc and self.grpc_client and (self.grpc_client.c3_channel or self.grpc_client.pccs_channel):
                    await self._update_grpc_data(vin)

                t2 = time.perf_counter()
                self.logger.debug("Update for VIN %s took %.3f seconds", vin, t2 - t1)

            except Exception as exc:
                self.latest_call_code = 500
                raise exc

    async def _update_vehicle_data(self, vin: str) -> None:
        """Get the latest vehicle data from the Polestar API."""

        self.logger.debug("Updating vehicle data for VIN %s", vin)

        for data in await self._get_all_vehicles_data():
            if data["vin"] == vin:
                self.logger.debug("Received vehicle data: %s", data)
                self.data_by_vin[vin][CAR_INFO_DATA] = data
                return

        self.logger.warning("VIN %s not found", vin)

    async def _update_telematics_data(self, vin: str) -> None:
        """Get the latest telematics data from the Polestar API."""

        self.logger.debug("Updating telematics data for VIN %s", vin)

        result = await self._query_graph_ql(
            query=QUERY_TELEMATICS_V2,
            variable_values={"vins": [vin]},
        )
        res = self.data_by_vin[vin][TELEMATICS_DATA] = result[TELEMATICS_DATA]

        self.logger.debug("Received telematics data: %s", res)

    async def _update_grpc_data(self, vin: str) -> None:
        """Get battery and target SOC data via gRPC."""

        self.logger.debug("Updating gRPC data for VIN %s", vin)

        if not self.auth.access_token:
            self.logger.warning("No access token for gRPC")
            return

        if not self.grpc_client:
            self.logger.warning("gRPC client not initialized")
            return

        try:
            battery = await self.grpc_client.get_battery(vin, self.auth.access_token)
            self.data_by_vin[vin][GRPC_BATTERY_DATA] = battery
            self.logger.debug("gRPC battery data: %s", battery)
        except Exception as exc:
            self.logger.warning("gRPC battery fetch failed: %s", exc)

        try:
            target_soc = await self.grpc_client.get_target_soc(vin, self.auth.access_token)
            self.data_by_vin[vin][GRPC_TARGET_SOC_DATA] = target_soc
            self.logger.debug("gRPC target SOC data: %s", target_soc)
        except Exception as exc:
            self.logger.warning("gRPC target SOC fetch failed: %s", exc)

        # Everything below is best-effort: each call is independently
        # non-fatal so one unsupported/broken service can't take down the
        # others or the rest of the update.
        for key, getter, label in (
            (GRPC_EXTERIOR_DATA, self.grpc_client.get_exterior, "exterior"),
            (GRPC_HEALTH_DATA, self.grpc_client.get_health, "health"),
            (GRPC_ODOMETER_DATA, self.grpc_client.get_odometer, "odometer"),
            (GRPC_CLIMATE_DATA, self.grpc_client.get_climate, "climate"),
            (GRPC_AVAILABILITY_DATA, self.grpc_client.get_availability, "availability"),
            (GRPC_PRECLEANING_DATA, self.grpc_client.get_precleaning, "pre-cleaning"),
            (GRPC_LOCATION_DATA, self.grpc_client.get_location, "location"),
            (GRPC_MYCARS_DATA, self.grpc_client.get_mycars, "mycars"),
            (GRPC_AMP_LIMIT_DATA, self.grpc_client.get_amp_limit, "amp limit"),
            (GRPC_CHARGE_SCHEDULE_DATA, self.grpc_client.get_charge_schedule, "charge schedule"),
        ):
            try:
                result = await getter(vin, self.auth.access_token)
                self.data_by_vin[vin][key] = result
                self.logger.debug("gRPC %s data: %s", label, result)
            except Exception as exc:
                self.logger.warning("gRPC %s fetch failed: %s", label, exc)

    async def _get_all_vehicles_data(self) -> list[dict[str, Any]]:
        """Get the all vehicle data from the Polestar API."""

        result = await self._query_graph_ql(
            query=QUERY_GET_CONSUMER_CARS_V2,
            variable_values={"locale": API_MYSTAR_LOCALE},
        )

        if result[CAR_INFO_DATA] is None or len(result[CAR_INFO_DATA]) == 0:
            self.logger.exception("No cars found in account")
            raise PolestarNoDataException("No cars found in account")

        return result[CAR_INFO_DATA]

    async def _get_car_images(self, vin: str) -> dict[str, Any]:
        """Get the car images data from the Polestar API."""

        pno34 = self.data_by_vin[vin][CAR_INFO_DATA]["pno34"]
        structure_week = self.data_by_vin[vin][CAR_INFO_DATA]["structureWeek"]
        model_year = self.data_by_vin[vin][CAR_INFO_DATA]["modelYear"]

        result = await self._query_graph_ql(
            query=QUERY_GET_CAR_IMAGES,
            variable_values={
                "pno34": pno34,
                "structureWeek": structure_week,
                "modelYear": model_year,
                "locale": API_MYSTAR_LOCALE,
            },
            public_api=True,
        )

        if not result.get(CAR_IMAGES_DATA):
            self.logger.exception("No car images found")
            raise PolestarNoDataException("No car images found")

        return result[CAR_IMAGES_DATA]

    def _ensure_data_for_vin(self, vin: str) -> None:
        """Ensure we have data for given VIN"""

        if vin not in self.available_vins:
            raise KeyError(f"VIN {vin} not available")

        if vin not in self.data_by_vin:
            raise KeyError(f"No data for VIN {vin}")

    async def _query_graph_ql(
        self,
        query: DocumentNode,
        operation_name: str | None = None,
        variable_values: dict[str, Any] | None = None,
        public_api: bool = False,
    ):
        """Execute a GraphQL query against the Polestar API."""

        if public_api:
            gql_session = self.gql_session_public
            headers = {"x-api-key": self.public_api_key}
        else:
            gql_session = self.gql_session_private
            headers = {"Authorization": f"Bearer {self.auth.access_token}"}

        if gql_session is None:
            raise RuntimeError("GraphQL not connected")

        try:
            result = await gql_session.execute(
                query,
                operation_name=operation_name,
                variable_values=variable_values,
                extra_args={"headers": headers},
            )
        except TransportQueryError as exc:
            self.logger.debug("GraphQL TransportQueryError: %s", str(exc))
            if exc.errors and exc.errors[0].get("extensions", {}).get("code") == "UNAUTHENTICATED":
                self.latest_call_code = 401
                raise PolestarNotAuthorizedException(exc.errors[0]["message"]) from exc
            self.latest_call_code = 500
            raise PolestarApiException from exc
        except Exception as exc:
            self.logger.debug("GraphQL Exception: %s", str(exc))
            raise exc

        self.logger.debug("GraphQL Result: %s", result)
        self.latest_call_code = 200

        return result
