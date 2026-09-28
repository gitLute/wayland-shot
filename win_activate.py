#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Активация окна (поднять наверх и дать фокус) в нативном Wayland.

Использование:
  win_activate.py --pid 22927            — активировать окно процесса
  win_activate.py --title "Kate"         — активировать по подстроке заголовка
  win_activate.py --index 2              — по индексу в windowList() (отладка)
  win_activate.py --pid 22927 --wait-ms 1000

Управление фокусом делается в KWin scripting в порядке убывания надёжности
для разных версий KWin: requestActivate() -> activate() -> присваивание
workspace.activeWindow -> workspace.raiseWindow(). После каждой попытки
активное окно проверяется отдельным скриптом (workspace.activeWindow); при
несовпадении попытка повторяется (--retries).

В JS шаблон подставляется только число pid — кириллический заголовок или
любой пользовательский текст в JS не попадает (окно находится по числовому
pid, который для --title разрешается в Python).

Код завершения:
  0 — окно активировано и проверено;
  1 — окно не найдено / не удалось активировать;
  2 — инфраструктура недоступна.
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kwinlib  # noqa: E402


def resolve_pid(args, timeout):
    """Возвращает pid целевого окна или None."""
    if args.pid is not None:
        return args.pid
    windows, errs = kwinlib.list_windows(timeout=timeout)
    if errs:
        for e in errs:
            print("ошибка:", e, file=sys.stderr)
        sys.exit(2)
    if args.index is not None:
        if 0 <= args.index < len(windows):
            return windows[args.index]["pid"]
        print(
            "индекс %d вне диапазона (окон: %d)" % (args.index, len(windows)),
            file=sys.stderr,
        )
        return None
    if args.title is not None:
        needle = args.title.lower()
        for w in windows:
            if needle in w["caption"].lower():
                return w["pid"]
        return None
    return None


def main():
    ap = argparse.ArgumentParser(
        prog="win_activate.py",
        description="Активация окна KDE Plasma (Wayland) через KWin scripting.",
    )
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--pid", type=int, default=None, help="pid окна-процесса")
    group.add_argument("--title", default=None, help="подстрока заголовка окна")
    group.add_argument(
        "--index", type=int, default=None, help="индекс окна в списке windowList()"
    )
    ap.add_argument(
        "--retries",
        type=int,
        default=3,
        help="число попыток активации (по умолчанию 3)",
    )
    ap.add_argument(
        "--wait-ms",
        type=int,
        default=800,
        help="пауза после активации перед проверкой, мс",
    )
    ap.add_argument(
        "--timeout",
        type=float,
        default=8.0,
        help="таймаут ожидания ответа скрипта KWin, секунд",
    )
    args = ap.parse_args()

    pid = resolve_pid(args, args.timeout)
    if pid is None:
        print("целевое окно не найдено", file=sys.stderr)
        sys.exit(1)

    for attempt in range(1, args.retries + 1):
        act, aerrs = kwinlib.activate_by_pid(pid, timeout=args.timeout)
        if aerrs:
            for e in aerrs:
                print("ошибка:", e, file=sys.stderr)
            sys.exit(2)
        if not act.get("found"):
            print("окно pid=%d отсутствует в windowList()" % pid, file=sys.stderr)
            sys.exit(1)
        time.sleep(args.wait_ms / 1000.0)
        aw, werrs = kwinlib.active_window_info(timeout=args.timeout)
        if werrs:
            for e in werrs:
                print("ошибка:", e, file=sys.stderr)
            sys.exit(2)
        active_pid = aw.get("pid", -1)
        if active_pid == pid:
            print("окно pid=%d активировано: «%s»" % (pid, act.get("caption", "")))
            sys.exit(0)
        print(
            "попытка %d: активное окно pid=%d, ожидалось %d — повтор"
            % (attempt, active_pid, pid)
        )

    print("не удалось активировать окно pid=%d" % pid, file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
