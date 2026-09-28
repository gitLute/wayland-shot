#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""End-to-end тест MCP-сервера wayland-shot через stdio-клиент.

Запуск:
    /home/lute/.local/bin/MCP/PytnonVenv/bin/python e2e_mcp.py

Проверяет: список инструментов -> list_windows -> find_window ->
capture_window (реальный снимок) -> чтение ресурса screenshot:// ->
ocr_text по пути.
"""

import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = [
    "/home/lute/.local/bin/MCP/PytnonVenv/bin/wayland-shot-mcp-server",
    "--stdio",
]


async def main():
    server_params = StdioServerParameters(
        command=SERVER[0], args=SERVER[1:], env=dict(os.environ)
    )
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            print("TOOLS:", ", ".join(names))

            res = await session.call_tool("list_windows", {})
            windows = json.loads(res.content[0].text) if res.content else {}
            print("list_windows -> count=%s" % windows.get("count"))

            res = await session.call_tool("find_window", {"title": "Kate"})
            found = json.loads(res.content[0].text) if res.content else {}
            print("find_window(Kate) -> count=%s" % found.get("count"))

            res = await session.call_tool(
                "capture_window",
                {"title": "Kate", "size_check": "calibrate"},
            )
            cap = json.loads(res.content[0].text) if res.content else {}
            print(
                "capture_window -> ok=%s %sx%s marker=%s uri=%s"
                % (
                    cap.get("ok"),
                    cap.get("width"),
                    cap.get("height"),
                    cap.get("marker_found"),
                    cap.get("resource_uri"),
                )
            )

            uri = cap.get("resource_uri")
            if uri:
                rres = await session.read_resource(uri)
                blob = rres.contents[0].blob if rres.contents else None
                if blob:
                    if isinstance(blob, str):
                        import base64

                        blob = base64.b64decode(blob)
                    header = bytes(blob[:8])
                    print(
                        "read_resource(%s) -> %d байт, PNG=%s"
                        % (uri, len(blob), header == b"\x89PNG\r\n\x1a\n")
                    )
                else:
                    print("read_resource(%s) -> нет blob" % uri)

            lres = await session.read_resource("screenshots://list")
            if lres.contents:
                print("screenshots://list ->\n%s" % lres.contents[0].text[:400])

            res = await session.call_tool(
                "ocr_text", {"image_path": cap.get("path", "")}
            )
            ocr = json.loads(res.content[0].text) if res.content else {}
            print("ocr_text -> символов=%d" % len(ocr.get("text", "")))


if __name__ == "__main__":
    asyncio.run(main())
