#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Проверка: сервер запущен клиентом без окружения (env=None, get_default_environment).

Симулирует запуск в opencode: клиент передаёт только whitelist-переменные.
Сервер должен сам восстановить DBUS_SESSION_BUS_ADDRESS из XDG_RUNTIME_DIR.
"""

import asyncio
import json

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = [
    "/home/lute/.local/bin/MCP/PytnonVenv/bin/wayland-shot-mcp-server",
    "--stdio",
]


async def main():
    server_params = StdioServerParameters(command=SERVER[0], args=SERVER[1:])
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            res = await session.call_tool("list_windows", {})
            text = res.content[0].text if res.content else "?"
            try:
                data = json.loads(text)
                print("list_windows(env=None) -> ok, count=%s" % data.get("count"))
            except Exception:
                print("list_windows(env=None) -> FAIL:", text[:300])


if __name__ == "__main__":
    asyncio.run(main())
