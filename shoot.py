#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Оркестратор: pid -> идентификация -> активация -> скриншот -> контроль.

Перенос идеи l2_shoot.py (raise+focus, проверка размера кадра, OCR-маркер,
ретраи) на нативный Wayland: никакого X11/Xwayland, только KWin scripting,
Spectacle и tesseract.

Использование:
  shoot.py --pid 22927 -o shot.png
  shoot.py --pid 22927 --title "make_scheme" -o shot.png
  shoot.py --pid 22927 --expect-w 1200 --expect-h 700 --marker "Kate" -o shot.png
  shoot.py --pid 22927 --no-size-check -o shot.png
  shoot.py --fullscreen -o screen.png      # фолбэк: весь экран без активации

Алгоритм:
  1. Идентификация окна по pid (+ подстроке заголовка) через KWin scripting.
  2. Цикл до N попыток: активация окна (логика win_activate), пауза, снимок
     активного окна spectacle -b -n -a, контроль размера кадра.
  3. При совпадении размера проверяется OCR-маркер (если задан), результат
     копируется в выходной файл.

Контроль размера: spectacle снимает в физических пикселях и включает рамку
и тень окна, а windowList() отдаёт логические координаты, поэтому точный
размер кадра напрямую из геометрии не выводится. По умолчанию ожидаемый
размер калибруется по первому кадру после успешной активации и проверяется
на последующих попытках (ловит ситуацию «сняли не то окно или весь экран»).
Явные --expect-w/--expect-h задают эталон в физических пикселях.

Код завершения:
  0 — снимок получен, размер совпал, маркер найден (либо маркер не задан);
  2 — размер совпал, но маркер в тексте окна не найден (снимок сохранён);
  1 — окно не найдено / снимок нужного размера не получен / инфраструктура.
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kwinlib  # noqa: E402

DEFAULT_OUT = os.path.join(kwinlib.TMP_ROOT, "_shoot.png")
TMP_SHOT = os.path.join(kwinlib.TMP_ROOT, "_shoot_tmp.png")


def main():
    ap = argparse.ArgumentParser(
        prog="shoot.py",
        description="Снимок окна по pid с активацией, контролем размера и OCR-маркера.",
    )
    ap.add_argument(
        "--pid", type=int, default=None, help="pid процесса, чьё окно нужно снять"
    )
    ap.add_argument(
        "--title", default=None, help="подстрока заголовка для уточнения выбора окна"
    )
    ap.add_argument(
        "--expect-w",
        type=int,
        default=None,
        help="ожидаемая ширина кадра в физических пикселях",
    )
    ap.add_argument(
        "--expect-h",
        type=int,
        default=None,
        help="ожидаемая высота кадра в физических пикселях",
    )
    ap.add_argument(
        "--no-size-check", action="store_true", help="не проверять размер снимка"
    )
    ap.add_argument(
        "--marker",
        default=None,
        help="подстрока, которую должен содержать текст окна (OCR)",
    )
    ap.add_argument(
        "-o",
        "--output",
        default=DEFAULT_OUT,
        help="выходной PNG (по умолчанию %s)" % DEFAULT_OUT,
    )
    ap.add_argument(
        "--retries", type=int, default=6, help="число попыток (по умолчанию 6)"
    )
    ap.add_argument(
        "--wait-ms",
        type=int,
        default=800,
        help="пауза после активации перед снимком, мс",
    )
    ap.add_argument(
        "--timeout", type=float, default=8.0, help="таймаут ответа скрипта KWin, секунд"
    )
    ap.add_argument(
        "--lang", default="rus+eng", help="языки OCR (по умолчанию rus+eng)"
    )
    ap.add_argument(
        "--fullscreen",
        action="store_true",
        help="фолбэк: снять весь экран без активации окна",
    )
    args = ap.parse_args()

    if args.fullscreen:
        return fullscreen_flow(args)

    if args.pid is None:
        print("укажите --pid (или --fullscreen)", file=sys.stderr)
        sys.exit(1)

    # --- 1. Идентификация --------------------------------------------------
    windows, errs = kwinlib.list_windows(timeout=args.timeout)
    if errs:
        for e in errs:
            print("ошибка:", e, file=sys.stderr)
        sys.exit(1)
    targets = [w for w in windows if w["pid"] == args.pid]
    if args.title:
        needle = args.title.lower()
        targets = [w for w in targets if needle in w["caption"].lower()]
    if not targets:
        print("окно pid=%d не найдено в windowList()" % args.pid, file=sys.stderr)
        sys.exit(1)
    target = targets[0]
    print(
        "целевое окно pid=%d: «%s» [%s] %sx%s"
        % (
            target["pid"],
            target["caption"],
            target["resourceClass"],
            target["width"],
            target["height"],
        )
    )

    if args.expect_w is not None and args.expect_h is not None:
        fixed_expected = (args.expect_w, args.expect_h)
        size_mode = "off" if args.no_size_check else "fixed"
    elif args.no_size_check:
        fixed_expected = None
        size_mode = "off"
    else:
        fixed_expected = None
        size_mode = "calibrate"
    if size_mode == "off":
        mode_desc = "выключен (--no-size-check)"
    elif size_mode == "fixed":
        mode_desc = "эталон %dx%d" % fixed_expected
    else:
        mode_desc = "калибровка по первому кадру активного окна"
    print("контроль размера: %s" % mode_desc)

    # --- 2+3. Цикл: активация, снимок, контроль ---------------------------
    calibrated = None
    for attempt in range(1, args.retries + 1):
        act, aerrs = kwinlib.activate_by_pid(target["pid"], timeout=args.timeout)
        if aerrs:
            for e in aerrs:
                print("ошибка:", e, file=sys.stderr)
            sys.exit(1)
        if not act.get("found"):
            print(
                "окно pid=%d исчезло из windowList()" % target["pid"], file=sys.stderr
            )
            sys.exit(1)
        time.sleep(args.wait_ms / 1000.0)

        size, cerr = kwinlib.capture(TMP_SHOT, fullscreen=False)
        if cerr:
            print(
                "попытка %d: снимок не удался (%s: %s)"
                % (attempt, cerr[0], cerr[1] or ""),
                file=sys.stderr,
            )
            continue

        msg = "попытка %d: размер снимка %dx%d" % (
            attempt,
            size["width"],
            size["height"],
        )
        if size_mode == "calibrate":
            if calibrated is None:
                calibrated = (size["width"], size["height"])
                msg += " -> эталон %dx%d" % calibrated
            elif (size["width"], size["height"]) != calibrated:
                print(msg + " -> не совпало с эталоном, повтор")
                continue
            else:
                msg += " -> совпал с эталоном"
        elif size_mode == "fixed":
            if (size["width"], size["height"]) != fixed_expected:
                print(msg + " -> не совпало с ожидаемым, повтор")
                continue
            msg += " -> совпал"
        if args.marker:
            text = kwinlib.ocr_text(TMP_SHOT, lang=args.lang, psm=6)
            mark_ok = args.marker.lower() in text.lower()
            msg += ", маркер = %s" % mark_ok
        else:
            mark_ok = True
        print(msg)

        kwinlib.copy_file(TMP_SHOT, args.output)
        print("сохранено: %s" % os.path.abspath(args.output))
        sys.exit(0 if mark_ok else 2)

    print("ни одна попытка не дала снимок нужного размера", file=sys.stderr)
    sys.exit(1)


def fullscreen_flow(args):
    """Фолбэк-ветка: весь экран без поиска и активации окна."""
    fixed = (
        (args.expect_w, args.expect_h)
        if args.expect_w is not None and args.expect_h is not None
        else None
    )
    for attempt in range(1, args.retries + 1):
        time.sleep(0.3)
        size, cerr = kwinlib.capture(TMP_SHOT, fullscreen=True)
        if cerr:
            print(
                "попытка %d: снимок не удался (%s: %s)"
                % (attempt, cerr[0], cerr[1] or ""),
                file=sys.stderr,
            )
            continue
        msg = "попытка %d: размер снимка экрана %dx%d" % (
            attempt,
            size["width"],
            size["height"],
        )
        if fixed and (size["width"], size["height"]) != fixed:
            print(msg + " -> не совпало, повтор")
            continue
        if args.marker:
            text = kwinlib.ocr_text(TMP_SHOT, lang=args.lang, psm=6)
            mark_ok = args.marker.lower() in text.lower()
            msg += ", маркер = %s" % mark_ok
        else:
            mark_ok = True
        print(msg)
        kwinlib.copy_file(TMP_SHOT, args.output)
        print("сохранено: %s" % os.path.abspath(args.output))
        sys.exit(0 if mark_ok else 2)
    print("не удалось сделать снимок экрана", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
