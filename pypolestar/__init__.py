"""Python Polestar API"""

from importlib.metadata import PackageNotFoundError, version

from .api import PolestarApi

try:
    __version__ = version("pypolestar")
except PackageNotFoundError:
    __version__ = "0.0.0"

__all__ = ["PolestarApi"]
