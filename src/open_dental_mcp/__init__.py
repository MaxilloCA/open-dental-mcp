"""Open Dental MCP — MCP server."""
from .client import OpenDentalAPIError, OpenDentalAuthError, OpenDentalClient
from .server import main, mcp

__version__ = "0.1.0"
__all__ = [
    "OpenDentalAPIError",
    "OpenDentalAuthError",
    "OpenDentalClient",
    "main",
    "mcp",
]