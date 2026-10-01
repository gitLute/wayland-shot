#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Проверка инструмента verify_ocr на других окнах.

Снимает окна Dolphin / Alacritty / Kate через capture_window и прогоняет
verify_ocr: позитивные проверки (ожидаемый текст есть), негативную
(ожидаемого текста нет), передачу через image_path и через screenshot_uri
(значение resource_uri из результата capture_window).

Запуск:
    /home/lute/.local/bin/MCP/PytnonVenv/bin/python scripts/e2e_verify.py
"""

import asyncio
import json
import os

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = [
    "/home/lute/.local/bin/MCP/PytnonVenv/bin/wayland-shot-mcp-server",
    "--stdio",
]


def call_text(res):
    return res.content[0].text if res.content else "?"


async def check(session, name, value, expected, want=True, key="image_path"):
    """Проверка verify_ocr; key выбирает способ передачи изображения."""
    res = await session.call_tool(
        "verify_ocr",
        {key: value, "expected": expected},
    )
    data = json.loads(call_text(res))
    ok = data.get("found") is want
    print(
        "%-28s expected=%-18s found=%s expect=%s -> %s"
        % (name, expected, data.get("found"), want, "OK" if ok else "FAIL")
    )
    if data.get("context") and data.get("found"):
        print("    контекст: …%s…" % data["context"][:120])
    return ok


async def main():
    server_params = StdioServerParameters(
        command=SERVER[0], args=SERVER[1:], env=dict(os.environ)
    )
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            print("TOOLS:", ", ".join(sorted(names)))
            print("verify_ocr в списке:", "verify_ocr" in names)

            cases = [
                ("Dolphin", {"title": "Dolphin"}, "PytnonVenv"),
                ("Alacritty", {"resource_class": "alacritty"}, "darktide"),
                ("Kate", {"title": "Kate"}, "opencode"),
            ]
            saved = {}
            for name, filt, expected in cases:
                res = await session.call_tool("capture_window", filt)
                cap = json.loads(call_text(res))
                saved[name] = cap
                print(
                    "capture_window(%s) -> %sx%s uri=%s"
                    % (
                        name,
                        cap.get("width"),
                        cap.get("height"),
                        cap.get("resource_uri"),
                    )
                )

            results = []
            for name, filt, expected in cases:
                cap = saved.get(name) or {}
                results.append(
                    await check(
                        session,
                        "%s (по пути)" % name,
                        cap.get("path", ""),
                        expected,
                    )
                )
                uri = cap.get("resource_uri")
                if uri:
                    results.append(
                        await check(
                            session,
                            "%s (по ресурсу)" % name,
                            uri,
                            expected,
                        )
                    )
                    # новый способ: resource_uri из capture_* -> screenshot_uri
                    results.append(
                        await check(
                            session,
                            "%s (по screenshot_uri)" % name,
                            uri,
                            expected,
                            key="screenshot_uri",
                        )
                    )

            # негативный случай: такого текста в кадре Kate нет
            cap = saved.get("Kate") or {}
            results.append(
                await check(
                    session,
                    "Kate (негатив)",
                    cap.get("path", ""),
                    "zzzz_nonexistent",
                    want=False,
                )
            )

            print("ИТОГ: %d/%d проверок прошло" % (sum(results), len(results)))
            return 1 if not all(results) else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
