"""wayland-shot: MCP-сервер скриншотов для KDE Plasma (Wayland)."""

from .server import SERVER_NAME, SERVER_VERSION, main

__version__ = SERVER_VERSION
__all__ = ["SERVER_NAME", "SERVER_VERSION", "main"]
