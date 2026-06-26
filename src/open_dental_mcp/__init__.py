"""Open Dental MCP — public surface."""
__version__ = "0.2.0"

from .client import OpenDentalClient
from .exceptions import (
    OpenDentalAPIError,
    OpenDentalAuthError,
    OpenDentalConnectionError,
    OpenDentalError,
    OpenDentalNotFoundError,
    OpenDentalRateLimitError,
)
from .server import main, mcp

__all__ = [
    "OpenDentalAPIError",
    "OpenDentalAuthError",
    "OpenDentalClient",
    "OpenDentalConnectionError",
    "OpenDentalError",
    "OpenDentalNotFoundError",
    "OpenDentalRateLimitError",
    "main",
    "mcp",
]
