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
    API_MYSTAR_PUBLIC_API_KEY,
    API_MYSTAR_PUBLIC_URL,
    API_MYSTAR_V2_URL,
    CAR_IMAGES_DATA,
    CAR_INFO_DATA,
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
    QUERY_GET_CONSUMER_CARS_V2_VERBOSE,
    QUERY_TELEMATICS_V2,
    get_gql_client,
    get_gql_session,
)
from .models import CarImagesData, CarInformationData, CarTelematicsData

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

    async def async_init(self, verbose: bool = False) -> None:
        """Initialize the Polestar API."""

        await self.auth.async_init()
        await self.auth.get_token()

        if self.auth.access_token is None:
            raise PolestarAuthException(f"No access token for {self.username}")

        self.gql_session_private = await get_gql_session(self.gql_client_private)
        self.gql_session_public = await get_gql_session(self.gql_client_public)

        if not (car_data := await self._get_all_vehicles_data(verbose=verbose)):
            self.logger.warning("No cars found for %s", self.username)
            return

        for data in car_data:
            vin = data["vin"]
            if self.configured_vins and vin not in self.configured_vins:
                continue
            self.data_by_vin[vin][CAR_INFO_DATA] = data
            self.data_by_vin[vin][CAR_IMAGES_DATA] = await self._get_car_images(vin)
            self.available_vins.add(vin)
            self.logger.debug("API setup for VIN %s", vin)

        if self.configured_vins and (missing_vins := self.configured_vins - self.available_vins):
            self.logger.warning("Could not found configured VINs %s", missing_vins)

    async def async_logout(self) -> None:
        """Log out from Polestar API."""
        await self.auth.async_logout()

    def get_status_code(self) -> int | None:
        """Return HTTP-like status code"""
        return self.latest_call_code

    def get_available_vins(self) -> list[str]:
        """Get list of all available VINs"""
        return list(self.available_vins)

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

    async def update_latest_data(
        self,
        vin: str,
        update_vehicle: bool = False,
        update_telematics: bool = True,
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

                t2 = time.perf_counter()
                self.logger.debug("Update for VIN %s took %.3f seconds", vin, t2 - t1)

            except Exception as exc:
                self.latest_call_code = 500
                raise exc

    async def _update_vehicle_data(self, vin: str) -> None:
        """Get the latest vehicle data from the Polestar API."""

        for data in await self._get_all_vehicles_data():
            if data["vin"] == vin:
                self.logger.debug("Received vehicle data: %s", data)
                self.data_by_vin[vin][CAR_INFO_DATA] = data
                return

        self.logger.warning("VIN %s not found", vin)

    async def _update_telematics_data(self, vin: str) -> None:
        """Get the latest telematics data from the Polestar API."""

        result = await self._query_graph_ql(
            query=QUERY_TELEMATICS_V2,
            variable_values={"vins": [vin]},
        )
        res = self.data_by_vin[vin][TELEMATICS_DATA] = result[TELEMATICS_DATA]

        self.logger.debug("Received telematics data: %s", res)

    async def _get_all_vehicles_data(self, verbose: bool = False) -> list[dict[str, Any]]:
        """Get the all vehicle data from the Polestar API."""

        result = await self._query_graph_ql(
            query=(QUERY_GET_CONSUMER_CARS_V2_VERBOSE if verbose else QUERY_GET_CONSUMER_CARS_V2),
            variable_values={"locale": "en_GB"},
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
