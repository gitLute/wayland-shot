#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Поиск окон в нативном Wayland через KWin scripting (KDE Plasma).

Использование:
  win_id.py                      — список всех окон (JSON в stdout)
  win_id.py --list               — то же самое явно
  win_id.py --pid 3375           — только окно с указанным pid
  win_id.py --title "Kate"       — окна, в заголовке которых есть подстрока
  win_id.py --pid 22927 --title "make_scheme"   — комбинация фильтров
  win_id.py --list -o win.json   — дополнительно сохранить результат в файл

Поля каждой записи: caption, pid, resourceClass, x, y, width, height,
active, minimized. В stdout выводится JSON (для машинной обработки),
человеческая сводка — в stderr.

Как это работает: Python формирует JS-скрипт (только ASCII), передаёт его
в KWin через qdbus loadScript/start и собирает результат JSON из журнала
kwin_wayland (см. kwinlib.run_kwin_script).

Код завершения:
  0 — окна найдены;
  1 — совпадений нет;
  2 — инфраструктура недоступна (нет kwin_wayland / qdbus / журнала).
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kwinlib  # noqa: E402


def main():
    ap = argparse.ArgumentParser(
        prog="win_id.py",
        description="Идентификация окон KDE Plasma (Wayland) через KWin scripting.",
    )
    ap.add_argument(
        "--pid", type=int, default=None, help="искать окно процесса с этим pid"
    )
    ap.add_argument(
        "--title",
        default=None,
        help="искать окно по подстроке заголовка (без учёта регистра)",
    )
    ap.add_argument(
        "--list", action="store_true", help="вывести все окна (режим по умолчанию)"
    )
    ap.add_argument(
        "-o", "--output", default=None, help="JSON-файл для сохранения результата"
    )
    ap.add_argument(
        "--timeout",
        type=float,
        default=8.0,
        help="таймаут ожидания ответа скрипта KWin, секунд",
    )
    args = ap.parse_args()

    windows, errs = kwinlib.list_windows(timeout=args.timeout)
    if errs:
        for e in errs:
            print("ошибка:", e, file=sys.stderr)
        print("[]", file=sys.stdout)
        sys.exit(2)

    selected = windows
    if args.pid is not None:
        selected = [w for w in selected if w["pid"] == args.pid]
    if args.title:
        needle = args.title.lower()
        selected = [w for w in selected if needle in w["caption"].lower()]

    if args.output:
        kwinlib.write_result_file(args.output, selected)
        print("результат записан: %s" % os.path.abspath(args.output), file=sys.stderr)

    print(json.dumps(selected, ensure_ascii=False, indent=2))
    if selected:
        for w in selected:
            print(
                "окно: pid=%s «%s» [%s] %sx%s+%s+%s%s%s"
                % (
                    w["pid"],
                    w["caption"],
                    w["resourceClass"],
                    w["width"],
                    w["height"],
                    w["x"],
                    w["y"],
                    " активное" if w["active"] else "",
                    " свёрнуто" if w["minimized"] else "",
                ),
                file=sys.stderr,
            )
    sys.exit(0 if selected else 1)


if __name__ == "__main__":
    main()
