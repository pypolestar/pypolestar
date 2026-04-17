import backoff
import httpx
from gql import gql
from gql.client import AsyncClientSession, Client
from gql.transport.exceptions import TransportError, TransportQueryError
from gql.transport.httpx import HTTPXAsyncTransport

from .const import GRAPHQL_CONNECT_RETRIES, GRAPHQL_EXECUTE_RETRIES, HTTPX_TIMEOUT


class _HTTPXAsyncTransport(HTTPXAsyncTransport):
    """GraphQL HTTPXAsyncTransport with pre-existing httpx client"""

    def __init__(self, *args, **kwargs):
        client = kwargs.pop("client")
        super().__init__(*args, **kwargs)
        self.client = client

    async def connect(self):
        pass

    async def close(self):
        pass


def get_gql_client(client: httpx.AsyncClient, url: str) -> Client:
    """Get GraphQL Client using existing httpx AsyncClient"""
    transport = _HTTPXAsyncTransport(url=url, client=client)
    return Client(
        transport=transport,
        fetch_schema_from_transport=False,
        execute_timeout=HTTPX_TIMEOUT,
    )


async def get_gql_session(client: Client) -> AsyncClientSession:
    """Get GraphQL Session with automatic retries"""
    retry_connect = backoff.on_exception(
        wait_gen=backoff.expo,
        exception=(TransportError, httpx.TransportError),
        max_tries=GRAPHQL_CONNECT_RETRIES,
    )
    retry_execute = backoff.on_exception(
        wait_gen=backoff.expo,
        exception=(TransportError,),
        max_tries=GRAPHQL_EXECUTE_RETRIES,
        giveup=lambda e: isinstance(e, TransportQueryError),
    )
    return await client.connect_async(
        reconnecting=True,
        retry_connect=retry_connect,
        retry_execute=retry_execute,
    )


QUERY_GET_CONSUMER_CARS_V2 = gql(
    """
    query GetConsumerCarsV2 {
        getConsumerCarsV2 {
            vin
            internalVehicleIdentifier
            registrationNo
            modelYear
            modelName
        }
    }
    """
)

QUERY_TELEMATICS_V2 = gql(
    """
    query CarTelematicsV2($vins: [String!]!) {
        carTelematicsV2(vins: $vins) {
            health {
                vin
                brakeFluidLevelWarning
                daysToService
                distanceToServiceKm
                engineCoolantLevelWarning
                oilLevelWarning
                serviceWarning
                timestamp { seconds nanos }
            }
            battery {
                vin
                batteryChargeLevelPercentage
                chargingStatus
                estimatedChargingTimeToFullMinutes
                estimatedDistanceToEmptyKm
                timestamp { seconds nanos }
            }
            odometer {
                vin
                odometerMeters
                timestamp { seconds nanos }
            }
        }
    }
    """
)
