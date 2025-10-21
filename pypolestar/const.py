"""Constants for Polestar."""

CAR_INFO_DATA = "getConsumerCarsV2"
ODO_METER_DATA = "getOdometerData"
BATTERY_DATA = "getBatteryData"
TELEMATICS_DATA = "carTelematicsV2"
CAR_IMAGES = "getCarImages"

HTTPX_TIMEOUT = 30
TOKEN_REFRESH_WINDOW_MIN = 300

GRAPHQL_CONNECT_RETRIES = 5
GRAPHQL_EXECUTE_RETRIES = 3

OIDC_PROVIDER_BASE_URL = "https://polestarid.eu.polestar.com"
OIDC_CLIENT_ID = "l3oopkc_10"
OIDC_REDIRECT_URI = "https://www.polestar.com/sign-in-callback"
OIDC_SCOPE = "openid profile email customer:attributes"
OIDC_COOKIES = ["PF", "PF.PERSISTENT"]

API_MYSTAR_V2_URL = "https://pc-api.polestar.com/eu-north-1/mystar-v2/"
API_MYSTAR_PUBLIC_URL = "https://pc-api.polestar.com/eu-north-1/mystar-public/"
API_MYSTAR_PUBLIC_API_KEY = "da2-js63uvc7c5hwpdudt657d5lyou"
