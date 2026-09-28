#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Скриншот в нативном Wayland через Spectacle.

Использование:
  shot.py -o win.png                  — снимок активного окна
  shot.py --active -o win.png         — то же самое явно
  shot.py --fullscreen -o screen.png  — снимок всего рабочего стола (фолбэк)
  shot.py -o win.png --delay-ms 400   — задержка перед снимком

Реализация: spectacle -b -n -a -o FILE (активное окно) либо
spectacle -b -n -f -o FILE (весь экран). После съёмки файл проверяется
через identify; в stdout печатается геометрия WxH+X+Y.

Код завершения:
  0 — снимок создан и проверен;
  1 — снимок не удался (нет файла, нулевой размер, ошибка spectacle).
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kwinlib  # noqa: E402

DEFAULT_OUT = os.path.join(kwinlib.TMP_ROOT, "_shot.png")


def main():
    ap = argparse.ArgumentParser(
        prog="shot.py",
        description="Скриншот активного окна или экрана через spectacle.",
    )
    ap.add_argument(
        "-o",
        "--output",
        default=DEFAULT_OUT,
        help="путь к PNG-файлу (по умолчанию %s)" % DEFAULT_OUT,
    )
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument(
        "--active", action="store_true", help="снимок активного окна (по умолчанию)"
    )
    mode.add_argument(
        "--fullscreen", action="store_true", help="снимок всего рабочего стола"
    )
    ap.add_argument(
        "--delay-ms",
        type=int,
        default=0,
        help="задержка перед снимком, мс (по умолчанию 0)",
    )
    args = ap.parse_args()

    size, err = kwinlib.capture(
        args.output, fullscreen=args.fullscreen, delay_ms=args.delay_ms
    )
    if err:
        print("снимок не удался (%s): %s" % (err[0], err[1] or ""), file=sys.stderr)
        sys.exit(1)
    abs_out = os.path.abspath(args.output)
    print(
        "сохранено: %s  %sx%s+%s+%s"
        % (abs_out, size["width"], size["height"], size["x"], size["y"])
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
