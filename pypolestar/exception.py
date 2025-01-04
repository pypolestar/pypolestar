"""Exceptions for Polestar API."""


class PolestarApiException(Exception):
    """Base class for exceptions in this module."""


class PolestarAuthException(PolestarApiException):
    """Base class for exceptions in Auth module."""

    error_code: int | None = None
    message: str | None = None

    def __init__(self, message: str, error_code: int | None = None) -> None:
        """Initialize the Polestar API."""
        super().__init__(message)
        self.error_code = error_code


class PolestarNotAuthorizedException(PolestarApiException):
    """Exception for unauthorized call."""


class PolestarNoDataException(PolestarApiException):
    """Exception for no data."""
