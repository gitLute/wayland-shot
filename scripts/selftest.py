#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Smoke-тест библиотеки kwinlib в живой сессии KDE Plasma (Wayland).

Запуск:
    /home/lute/.local/bin/MCP/PytnonVenv/bin/python scripts/selftest.py [--capture]

Проверяет: список окон -> поиск по заголовку -> активацию -> снимок ->
OCR. Без --capture делается только чтение (список/поиск/активное окно),
чтобы не двигать фокус и не создавать файлы.
"""

import argparse
import json
import os
import sys

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
)
from wayland_shot import kwinlib  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Smoke-тест wayland-shot kwinlib.")
    ap.add_argument("--capture", action="store_true", help="дополнительно снять окно")
    ap.add_argument(
        "--title", default="Kate", help="заголовок для поиска (по умолчанию Kate)"
    )
    ap.add_argument(
        "--out",
        default="/tmp/opencode/wayland-shot/selftest.png",
        help="куда писать снимок",
    )
    args = ap.parse_args()

    results = {}
    fail = 0

    def check(name, ok, detail):
        nonlocal fail
        results[name] = {"ok": bool(ok), "detail": detail}
        print("%s %s: %s" % ("OK " if ok else "FAIL", name, detail))
        if not ok:
            fail += 1

    windows, errs = kwinlib.list_windows()
    check(
        "list_windows",
        not errs,
        "ошибок: %s" % errs if errs else "окон: %d" % len(windows),
    )
    if errs:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        sys.exit(1)

    sel = kwinlib.filter_windows(windows, title=args.title)
    check(
        "find_by_title",
        bool(sel),
        "по заголовку «%s»: %d окно(а)" % (args.title, len(sel)),
    )

    info, ierrs = kwinlib.active_window_info()
    check(
        "active_window",
        not ierrs,
        "pid=%s «%s»" % (info.get("pid"), info.get("caption")),
    )

    if args.capture:
        if sel:
            pid = sel[0]["pid"]
            act, aerrs = kwinlib.activate_by_pid(pid)
            check("activate", act.get("found") and not aerrs, "pid=%d" % pid)
            size, cerr = kwinlib.capture(args.out, fullscreen=False)
            check(
                "capture_window",
                not cerr and size,
                "размер: %sx%s" % (size["width"], size["height"]) if size else cerr,
            )
            text = kwinlib.ocr_text(args.out)
            check("ocr_text", bool(text.strip()), "символов: %d" % len(text))
            words = kwinlib.ocr_words(args.out)
            check("ocr_words", True, "слов: %d" % len(words))
            if words:
                print("  пример слова:", words[0])
        else:
            print("--capture пропущен: окно по заголовку не найдено")

    print(json.dumps(results, ensure_ascii=False, indent=2))
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
