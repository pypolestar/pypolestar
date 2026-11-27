import asyncio
from collections.abc import Callable
from urllib.parse import parse_qs

import httpx
import pytest

from pypolestar.auth import OidcConfiguration, PolestarAuth, _upgrade_to_https
from pypolestar.const import OIDC_REDIRECT_URI
from pypolestar.exceptions import PolestarAuthFailedException, PolestarAuthMfaRequired

AUTHORIZATION_ENDPOINT = "https://polestarid.eu.polestar.com/as/authorization.oauth2"
TOKEN_ENDPOINT = "https://polestarid.eu.polestar.com/as/token.oauth2"
KEYCLOAK_AUTH_URL = "https://auth.polestar.com/realms/polestar/protocol/openid-connect/auth?client_id=x"
KEYCLOAK_LOGIN_ACTION = (
    "https://auth.polestar.com/realms/polestar/login-actions/authenticate?session_code=abc&tab_id=t1"
)
PINGFEDERATE_CALLBACK = "https://polestarid.eu.polestar.com/sp/eyJxyz/cb.openid?code=kc-code&state=s"

USERNAME = "user@example.com"
PASSWORD = "secret"


def keycloak_page(page_id: str = "login", login_action: str | None = None, message: str = "") -> str:
    """Minimal Keycloak login page with a kcContext object, escaped like the real page."""
    if login_action is None:
        login_action = KEYCLOAK_LOGIN_ACTION.replace("https://auth.polestar.com", "").replace("/", "\\/")
        login_action = login_action.replace("&", "&amp;")
    return f"""<html><script>
    const kcContext = {{
        "pageId": "{page_id}",
        "url": {{ "loginAction": "{login_action}" }},
        "message": "{message}"
    }};
    </script></html>"""


def redirect(location: str, status: int = 302) -> httpx.Response:
    return httpx.Response(status, headers={"location": location})


def make_auth(handler: Callable[[httpx.Request], httpx.Response]) -> PolestarAuth:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    auth = PolestarAuth(username=USERNAME, password=PASSWORD, client_session=client)
    auth.oidc_configuration = OidcConfiguration(
        issuer="https://polestarid.eu.polestar.com",
        token_endpoint=TOKEN_ENDPOINT,
        authorization_endpoint=AUTHORIZATION_ENDPOINT,
    )
    return auth


class KeycloakServer:
    """Fake Polestar ID + Keycloak, recording requests."""

    def __init__(self, login_response: httpx.Response | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.login_response = login_response or redirect(PINGFEDERATE_CALLBACK)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if url.startswith(AUTHORIZATION_ENDPOINT):
            return redirect(KEYCLOAK_AUTH_URL)
        if url == KEYCLOAK_AUTH_URL:
            return httpx.Response(200, text=keycloak_page())
        if url == KEYCLOAK_LOGIN_ACTION and request.method == "POST":
            return self.login_response
        if url == PINGFEDERATE_CALLBACK:
            return redirect(f"{OIDC_REDIRECT_URI}?code=final-code&state=s")
        if url == TOKEN_ENDPOINT:
            return httpx.Response(
                200,
                json={"access_token": "access", "refresh_token": "refresh", "expires_in": 3600},
            )
        raise AssertionError(f"Unexpected request {request.method} {url}")


def test_keycloak_login() -> None:
    server = KeycloakServer()
    auth = make_auth(server)

    code = asyncio.run(auth._get_code())

    assert code == "final-code"
    login_requests = [r for r in server.requests if r.method == "POST"]
    assert len(login_requests) == 1
    assert str(login_requests[0].url) == KEYCLOAK_LOGIN_ACTION
    form = parse_qs(login_requests[0].content.decode())
    assert form["username"] == [USERNAME]
    assert form["password"] == [PASSWORD]
    # the redirect URI itself is never fetched
    assert not any(str(r.url).startswith(OIDC_REDIRECT_URI) for r in server.requests)


def test_keycloak_get_token() -> None:
    auth = make_auth(KeycloakServer())

    asyncio.run(auth.get_token())

    assert auth.access_token == "access"
    assert auth.refresh_token == "refresh"
    assert auth.is_token_valid()


def test_http_redirect_upgraded_to_https() -> None:
    server = KeycloakServer()
    resume_http = "http://polestarid.eu.polestar.com/as/abc/resume/as/authorization.ping"
    resume_https = resume_http.replace("http://", "https://")

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == PINGFEDERATE_CALLBACK:
            server.requests.append(request)
            return redirect(resume_http)
        if url == resume_https:
            server.requests.append(request)
            return redirect(f"{OIDC_REDIRECT_URI}?code=final-code")
        return server(request)

    assert asyncio.run(make_auth(handler)._get_code()) == "final-code"
    assert all(r.url.scheme == "https" for r in server.requests)


def test_upgrade_to_https_only_for_polestar_hosts() -> None:
    assert _upgrade_to_https("http://polestarid.eu.polestar.com/x?a=1") == "https://polestarid.eu.polestar.com/x?a=1"
    assert _upgrade_to_https("http://polestar.com/") == "https://polestar.com/"
    assert _upgrade_to_https("http://example.com/x") == "http://example.com/x"
    assert _upgrade_to_https("http://notpolestar.com/x") == "http://notpolestar.com/x"


def test_keycloak_absolute_login_action() -> None:
    server = KeycloakServer()

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == KEYCLOAK_AUTH_URL:
            server.requests.append(request)
            return httpx.Response(200, text=keycloak_page(login_action=KEYCLOAK_LOGIN_ACTION.replace("&", "&amp;")))
        return server(request)

    assert asyncio.run(make_auth(handler)._get_code()) == "final-code"


def test_keycloak_invalid_credentials() -> None:
    page = keycloak_page(message="Invalid username or password.")
    auth = make_auth(KeycloakServer(login_response=httpx.Response(200, text=page)))

    with pytest.raises(PolestarAuthFailedException):
        asyncio.run(auth._get_code())


def test_keycloak_mfa_required() -> None:
    page = keycloak_page(page_id="login-otp")
    auth = make_auth(KeycloakServer(login_response=httpx.Response(200, text=page)))

    with pytest.raises(PolestarAuthMfaRequired):
        asyncio.run(auth._get_code())


def test_mfa_required_is_not_wrapped_by_get_token() -> None:
    page = keycloak_page(page_id="login-otp")
    auth = make_auth(KeycloakServer(login_response=httpx.Response(200, text=page)))

    with pytest.raises(PolestarAuthMfaRequired):
        asyncio.run(auth.get_token())


def test_existing_session_skips_login_form() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        url = str(request.url)
        if url.startswith(AUTHORIZATION_ENDPOINT):
            return redirect(KEYCLOAK_AUTH_URL)
        if url == KEYCLOAK_AUTH_URL:
            return redirect(PINGFEDERATE_CALLBACK)
        if url == PINGFEDERATE_CALLBACK:
            return redirect(f"{OIDC_REDIRECT_URI}?code=sso-code")
        raise AssertionError(f"Unexpected request {request.method} {url}")

    assert asyncio.run(make_auth(handler)._get_code()) == "sso-code"
    assert all(r.method == "GET" for r in requests)


def test_legacy_pingfederate_login() -> None:
    resume_path = "/as/abc123/resume/as/authorization.ping"
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        url = str(request.url)
        if url.startswith(AUTHORIZATION_ENDPOINT):
            return httpx.Response(200, text=f'<script>var x = {{ action: "{resume_path}" }};</script>')
        if request.url.path == resume_path and request.method == "POST":
            return redirect(f"{OIDC_REDIRECT_URI}?code=legacy-code")
        if url.startswith(OIDC_REDIRECT_URI):
            return httpx.Response(200, text="ok")
        if request.url.path == resume_path:
            return httpx.Response(200, text="ok")
        raise AssertionError(f"Unexpected request {request.method} {url}")

    assert asyncio.run(make_auth(handler)._get_code()) == "legacy-code"
    form = parse_qs(next(r for r in requests if r.method == "POST").content.decode())
    assert form["pf.username"] == [USERNAME]


def test_logout_clears_keycloak_cookies() -> None:
    auth = make_auth(KeycloakServer())
    auth.client_session.cookies.set("KEYCLOAK_IDENTITY", "x", domain="auth.polestar.com", path="/realms/polestar/")
    auth.client_session.cookies.set("AUTH_SESSION_ID", "y", domain=".auth.polestar.com", path="/")
    auth.client_session.cookies.set("other", "z", domain="www.polestar.com", path="/")

    asyncio.run(auth.async_logout())

    remaining = {cookie.name for cookie in auth.client_session.cookies.jar}
    assert remaining == {"other"}
