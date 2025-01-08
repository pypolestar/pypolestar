"""Python Polestar API"""

from importlib.metadata import version

from .polestar import PolestarApi

__version__ = version("pypolestar")
__all__ = ["PolestarApi"]
