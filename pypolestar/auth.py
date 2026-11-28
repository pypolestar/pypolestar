import asyncio
import base64
import hashlib
import html
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from json import JSONDecodeError
from typing import NoReturn
from urllib.parse import urljoin, urlparse

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from .const import (
    HTTPX_TIMEOUT,
    OIDC_CLIENT_ID,
    OIDC_COOKIES,
    OIDC_PROVIDER_BASE_URL,
    OIDC_REDIRECT_URI,
    OIDC_SCOPE,
    TOKEN_REFRESH_WINDOW_MIN,
)
from .exceptions import (
    PolestarAuthException,
    PolestarAuthFailedException,
    PolestarAuthMfaRequired,
    PolestarAuthUnavailable,
)

_LOGGER = logging.getLogger(__name__)

# The Keycloak login page embeds a kcContext object with the form target in
# "loginAction" and the page type in "pageId" (e.g. "login", or an OTP step).
KEYCLOAK_LOGIN_ACTION_RE = re.compile(r'"loginAction":\s*"([^"]+)"')
KEYCLOAK_PAGE_ID_RE = re.compile(r'"pageId":\s*"([^"]+)"')
KEYCLOAK_INVALID_CREDENTIALS = "Invalid username or password"
KEYCLOAK_COOKIE_DOMAIN = "auth.polestar.com"

REDIRECT_STATUS_CODES = (301, 302, 303, 307, 308)
MAX_LOGIN_REDIRECTS = 10


def b64urlencode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _is_redirect_uri(url: str) -> bool:
    """Return True if url is the OIDC redirect URI (any query string allowed)."""
    parsed = urlparse(url)
    expected = urlparse(OIDC_REDIRECT_URI)
    return (parsed.scheme, parsed.netloc, parsed.path) == (expected.scheme, expected.netloc, expected.path)


def _upgrade_to_https(url: str) -> str:
    """Use HTTPS for Polestar hosts, which redirect plain HTTP to HTTPS anyway.

    Polestar ID can return a plain http:// Location during login; following it
    as-is would send the request (and any non-secure cookies) unencrypted.
    """
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if parsed.scheme == "http" and (host == "polestar.com" or host.endswith(".polestar.com")):
        return parsed._replace(scheme="https").geturl()
    return url


def _unescape_js_url(value: str) -> str:
    """Undo the JS/HTML escaping Keycloak applies to URLs in kcContext."""
    return html.unescape(value.replace("\\/", "/"))


class OidcConfiguration(BaseModel):
    issuer: str
    token_endpoint: str
    authorization_endpoint: str

    model_config = ConfigDict(frozen=True)


class PolestarAuth:
    """base class for Polestar authentication."""

    def __init__(
        self,
        username: str,
        password: str,
        client_session: httpx.AsyncClient,
        unique_id: str | None = None,
    ) -> None:
        """Initialize the Polestar authentication."""
        self.username = username
        self.password = password
        self.client_session = client_session

        self.access_token: str | None = None
        self.id_token: str | None = None
        self.refresh_token: str | None = None
        self.token_lifetime: int | None = None
        self.token_expiry: datetime | None = None

        self.oidc_configuration: OidcConfiguration | None = None
        self.oidc_provider = OIDC_PROVIDER_BASE_URL
        self.oidc_code_verifier: str | None = None
        self.oidc_state: str | None = None

        self.latest_call_code: int | None = None
        self.logger = _LOGGER.getChild(unique_id) if unique_id else _LOGGER

        self.token_lock = asyncio.Lock()

    async def async_init(self) -> None:
        await self.update_oidc_configuration()

    async def async_logout(self) -> None:
        self.logger.debug("Logout")

        domain = urlparse(OIDC_PROVIDER_BASE_URL).hostname
        for name in OIDC_COOKIES:
            self.logger.debug("Delete cookie %s in domain %s", name, domain)
            self.client_session.cookies.delete(name=name, domain=domain)

        for cookie in list(self.client_session.cookies.jar):
            if cookie.domain.lstrip(".") == KEYCLOAK_COOKIE_DOMAIN:
                self.logger.debug("Delete cookie %s in domain %s", cookie.name, cookie.domain)
                self.client_session.cookies.delete(name=cookie.name, domain=cookie.domain, path=cookie.path)

        self.access_token = None
        self.id_token = None
        self.refresh_token = None
        self.token_lifetime = None
        self.token_expiry = None

        self.oidc_code_verifier = None
        self.oidc_state = None

    def get_status_code(self) -> int | None:
        """Return HTTP-like status code"""
        return self.latest_call_code

    async def update_oidc_configuration(self) -> None:
        try:
            result = await self.client_session.get(urljoin(OIDC_PROVIDER_BASE_URL, "/.well-known/openid-configuration"))
            result.raise_for_status()
        except httpx.HTTPStatusError as exc:
            self.logger.warning(
                "Failed to retrieve OIDC configuration: %s, status code: %d",
                exc.response.reason_phrase,
                exc.response.status_code,
            )
            raise PolestarAuthUnavailable(
                message="Unable to get OIDC configuration", error_code=exc.response.status_code
            ) from exc
        try:
            self.oidc_configuration = OidcConfiguration.model_validate(result.json())
        except (ValidationError, JSONDecodeError) as exc:
            raise PolestarAuthException("Invalid OIDC configuration") from exc

    def need_token_refresh(self) -> bool:
        """Return True if token needs refresh"""
        if self.token_expiry is None:
            raise PolestarAuthException("No token expiry found")
        refresh_window = min([(self.token_lifetime or 0) / 2, TOKEN_REFRESH_WINDOW_MIN])
        expires_in = (self.token_expiry - datetime.now(tz=timezone.utc)).total_seconds()
        if expires_in < refresh_window:
            self.logger.debug("Token expires in %d seconds, time to refresh", expires_in)
            return True
        return False

    def is_token_valid(self) -> bool:
        return (
            self.access_token is not None
            and self.token_expiry is not None
            and self.token_expiry > datetime.now(tz=timezone.utc)
        )

    async def get_token(self, force: bool = False) -> None:
        """Ensure we have a valid access token (still valid, refreshed or initial)."""

        async with self.token_lock:
            if not force and self.token_expiry and self.need_token_refresh():
                force = True

            if (
                not force
                and self.access_token is not None
                and self.token_expiry
                and self.token_expiry > datetime.now(tz=timezone.utc)
            ):
                self.logger.debug("Token still valid until %s", self.token_expiry)
                return

            if self.refresh_token:
                try:
                    await self._token_refresh()
                    self.logger.debug("Token refreshed")
                    return
                except Exception as exc:
                    self.logger.warning("Failed to refresh token, retry with code", exc_info=exc)

            try:
                await self._authorization_code()
                self.logger.debug("Initial token acquired")
                return
            except PolestarAuthFailedException as exc:
                await self.async_logout()
                raise exc
            except Exception as exc:
                await self.async_logout()
                raise PolestarAuthException("Unable to acquire initial token") from exc

    def _parse_token_response(self, response: httpx.Response) -> None:
        """Parse response from token endpoint and update token state."""

        self.latest_call_code = response.status_code

        payload = response.json()

        if "error" in payload:
            self.logger.error("Token error: %s", payload)
            raise PolestarAuthException("Token error", response.status_code)

        try:
            self.access_token = payload["access_token"]
            self.refresh_token = payload["refresh_token"]
            if token_lifetime := payload["expires_in"]:
                self.token_lifetime = token_lifetime
                self.token_expiry = datetime.now(tz=timezone.utc) + timedelta(seconds=token_lifetime)
            else:
                self.token_lifetime = None
                self.token_expiry = None
        except KeyError as exc:
            self.logger.error("Token response missing key: %s", exc)
            raise PolestarAuthException("Token response missing key") from exc

        self.logger.debug("Access token updated, valid until %s", self.token_expiry)

    async def _authorization_code(self) -> None:
        """Get initial token via authorization code."""

        if (code := await self._get_code()) is None:
            raise PolestarAuthException("Unable to get code")

        token_request = {
            "grant_type": "authorization_code",
            "client_id": OIDC_CLIENT_ID,
            "code": code,
            "redirect_uri": OIDC_REDIRECT_URI,
            **({"code_verifier": self.oidc_code_verifier} if self.oidc_code_verifier else {}),
        }

        self.logger.debug("Call token endpoint with grant_type=%s", token_request["grant_type"])

        if not self.oidc_configuration:
            raise PolestarAuthException(message="No OIDC configuration")

        response = await self.client_session.post(
            self.oidc_configuration.token_endpoint,
            data=token_request,
            timeout=HTTPX_TIMEOUT,
        )
        response.raise_for_status()
        self._parse_token_response(response)

    async def _token_refresh(self) -> None:
        """Refresh existing token."""

        token_request = {
            "grant_type": "refresh_token",
            "client_id": OIDC_CLIENT_ID,
            "refresh_token": self.refresh_token,
        }

        self.logger.debug("Call token endpoint with grant_type=%s", token_request["grant_type"])

        if not self.oidc_configuration:
            raise PolestarAuthException(message="No OIDC configuration")

        response = await self.client_session.post(
            self.oidc_configuration.token_endpoint,
            data=token_request,
            timeout=HTTPX_TIMEOUT,
        )
        response.raise_for_status()
        self._parse_token_response(response)

    async def _get_code(self) -> str | None:
        login_page = await self._get_login_page()

        # An existing identity provider session skips the login form
        if (code := self._get_redirect_code(login_page)) is not None:
            self.logger.debug("Login form skipped, code received directly")
            return code

        if match := KEYCLOAK_LOGIN_ACTION_RE.search(login_page.text):
            login_action = urljoin(str(login_page.url), _unescape_js_url(match.group(1)))
            return await self._get_code_keycloak(login_action)

        resume_path = self._get_resume_path(login_page)

        params = self.get_params()
        data = {"pf.username": self.username, "pf.pass": self.password}
        result = await self.client_session.post(
            urljoin(OIDC_PROVIDER_BASE_URL, resume_path),
            params=params,
            data=data,
        )

        if result.status_code not in [302, 303]:
            self.latest_call_code = result.status_code
            if 'authMessage: "ERR001"' in result.text:
                raise PolestarAuthFailedException("Authentication error (ERR001), invalid username/password")
            raise PolestarAuthException("Error getting code", result.status_code)

        # 3xx must have a next request (from "Location")
        if result.next_request is None:
            raise PolestarAuthException("Missing next request in 3xx response")

        # get the realUrl
        url = result.url
        code = result.next_request.url.params.get("code")
        uid = result.next_request.url.params.get("uid")

        # handle missing code (e.g., accepting terms and conditions)
        if code is None and uid:
            self.logger.debug("Code missing; submit confirmation for uid=%s and retry", uid)
            params = self.get_params()
            data = {"pf.submit": True, "subject": uid}
            result = await self.client_session.post(
                urljoin(OIDC_PROVIDER_BASE_URL, resume_path),
                params=params,
                data=data,
            )
            url = result.url
            code = result.next_request.url.params.get("code")

        # sign-in-callback
        result = await self.client_session.get(result.next_request.url, timeout=HTTPX_TIMEOUT)
        self.latest_call_code = result.status_code

        if result.status_code != 200:
            self.logger.error("Auth Code Error: %s", result)
            raise PolestarAuthException("Error getting code callback", result.status_code)

        result = await self.client_session.get(url)

        return code

    async def _get_login_page(self) -> httpx.Response:
        """Start the authorization flow and return the login page (or the final redirect)."""

        if not self.oidc_configuration:
            raise PolestarAuthException(message="No OIDC configuration")

        self.oidc_state = self.get_state()
        params = self.get_params()

        result = await self.client_session.get(
            self.oidc_configuration.authorization_endpoint,
            params=params,
            timeout=HTTPX_TIMEOUT,
            follow_redirects=False,
        )
        return await self._follow_login_redirects(result)

    async def _follow_login_redirects(self, result: httpx.Response) -> httpx.Response:
        """Follow redirects until a page is returned or the redirect URI is reached.

        The redirect URI itself is not fetched; the code is read from the Location header.
        """

        for fetches in range(MAX_LOGIN_REDIRECTS + 1):
            self.latest_call_code = result.status_code
            if result.status_code not in REDIRECT_STATUS_CODES:
                return result
            location = urljoin(str(result.url), result.headers.get("location", ""))
            if _is_redirect_uri(location) or urlparse(location).scheme not in ("http", "https"):
                return result
            if fetches == MAX_LOGIN_REDIRECTS:
                break
            location = _upgrade_to_https(location)
            result = await self.client_session.get(location, timeout=HTTPX_TIMEOUT, follow_redirects=False)

        raise PolestarAuthException("Too many redirects during login")

    @staticmethod
    def _get_redirect_code(result: httpx.Response) -> str | None:
        """Return the authorization code if the response redirects to the redirect URI."""

        if result.status_code not in REDIRECT_STATUS_CODES:
            return None
        location = urljoin(str(result.url), result.headers.get("location", ""))
        if not _is_redirect_uri(location):
            return None
        return httpx.URL(location).params.get("code")

    async def _get_code_keycloak(self, login_action: str) -> str:
        """Log in on the Keycloak login page and return the authorization code.

        Since November 2026, Polestar ID hands the login over to a Keycloak realm
        on auth.polestar.com, which redirects back via Polestar ID to the redirect URI.
        """

        self.logger.debug("Keycloak login page detected")
        result = await self.client_session.post(
            login_action,
            data={"username": self.username, "password": self.password, "credentialId": ""},
            timeout=HTTPX_TIMEOUT,
            follow_redirects=False,
        )
        self.latest_call_code = result.status_code

        if result.status_code not in REDIRECT_STATUS_CODES:
            self._raise_for_keycloak_page(result, "Keycloak login failed")

        result = await self._follow_login_redirects(result)
        if (code := self._get_redirect_code(result)) is None:
            # e.g. a redirect to a separate page asking for a one-time code
            self._raise_for_keycloak_page(result, "No authorization code after Keycloak login")
        return code

    @staticmethod
    def _raise_for_keycloak_page(result: httpx.Response, message: str) -> NoReturn:
        """Raise the most specific exception for a Keycloak page returned instead of a code."""

        page_id = match.group(1) if (match := KEYCLOAK_PAGE_ID_RE.search(result.text)) else None
        if KEYCLOAK_INVALID_CREDENTIALS in result.text:
            raise PolestarAuthFailedException("Authentication error, invalid username/password")
        if page_id and page_id != "login":
            raise PolestarAuthMfaRequired(
                f"Polestar ID requires an additional login step ({page_id}), e.g. a one-time code, "
                "which is not supported"
            )
        raise PolestarAuthException(f"{message} (page {page_id or 'unknown'})", result.status_code)

    def _get_resume_path(self, result: httpx.Response) -> str:
        """Get Resume Path from the legacy PingFederate login page."""

        if match := re.search(r'(?:url|action):\s*"(.+)"', result.text):
            resume_path = match.group(1)
            self.logger.debug("Returning resume path: %s", resume_path)
            return resume_path

        self.logger.error("Error: %s", result.text)
        raise PolestarAuthException("Error getting resume path", result.status_code)

    @staticmethod
    def get_state() -> str:
        return b64urlencode(os.urandom(32))

    @staticmethod
    def get_code_verifier() -> str:
        return b64urlencode(os.urandom(32))

    def get_code_challenge(self) -> str:
        if self.oidc_code_verifier is None:
            self.oidc_code_verifier = self.get_code_verifier()
        m = hashlib.sha256()
        m.update(self.oidc_code_verifier.encode())
        return b64urlencode(m.digest())

    def get_params(self) -> dict[str, str | None]:
        return {
            "client_id": OIDC_CLIENT_ID,
            "redirect_uri": OIDC_REDIRECT_URI,
            "response_type": "code",
            "scope": OIDC_SCOPE,
            "state": self.oidc_state,
            "code_challenge": self.get_code_challenge(),
            "code_challenge_method": "S256",
            "response_mode": "query",
        }
