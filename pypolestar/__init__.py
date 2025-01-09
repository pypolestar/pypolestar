"""Python Polestar API"""

from importlib.metadata import version

from .api import PolestarApi

__version__ = version("pypolestar")
__all__ = ["PolestarApi"]
