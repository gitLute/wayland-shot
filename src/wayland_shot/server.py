#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MCP-сервер wayland-shot: скриншоты в KDE Plasma (Wayland) через LLM.

Полноценный сервер Model Context Protocol поверх наработки
MCP_DEV/wayland-shot (KWin scripting + Spectacle + tesseract).
Позволяет агентам:

  - перечислять и искать окна (list_windows, find_window, get_active_window);
  - активировать окно (focus + raise) с проверкой и ретраями (activate_window);
  - снимать конкретное окно с контролем размера/OCR-маркера (capture_window);
  - снимать активное окно или весь экран (capture_active_window, capture_screen);
  - распознавать текст на снимке (ocr_text, ocr_words, verify_ocr);
  - получать снимки как бинарные ресурсы (screenshot://<имя файла>).

Инструменты OCR принимают изображение двумя способами: screenshot_uri —
значение поля resource_uri из результата capture_*, либо image_path — путь
к файлу. Достаточно одного из двух.

Снимки сохраняются в каталог WAYLAND_SHOT_OUTPUT (по умолчанию
~/Pictures/wayland-shot). Временные JS-скрипты для KWin — в WSHOT_TMP
(по умолчанию /tmp/opencode/wayland-shot).
"""

import argparse
import os
import re
import time
from datetime import datetime
from pathlib import Path

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from . import kwinlib

SERVER_NAME = "wayland-shot"
SERVER_VERSION = "0.1.0"

# Каталог снимков по умолчанию; переопределяется через WAYLAND_SHOT_OUTPUT.
OUTPUT_DIR = Path(
    os.environ.get("WAYLAND_SHOT_OUTPUT", "~/Pictures/wayland-shot")
).expanduser()

# Временный файл для промежуточных кадров (перезаписывается на каждой попытке).
_TMP_SHOT = os.path.join(kwinlib.TMP_ROOT, "_mcp_shot.png")

# Допустимые имена файлов для ресурса screenshot:// (без разделителей путей).
_NAME_RE = re.compile(r"^[\w\-. ]+\.(?:png|jpe?g|webp)$", re.IGNORECASE)

instructions = (
    "Сервер управляет окнами KDE Plasma (Wayland) и создаёт скриншоты. "
    "Типовой сценарий: find_window (по pid или части заголовка) -> "
    "capture_window -> ocr_text. Результат capture_* содержит поле resource_uri "
    "со значением screenshot://<имя файла>; это значение передаётся в ocr_text "
    "как screenshot_uri (или как image_path — имя параметра значения не меняет). "
    "Снимки также отдаются как ресурсы screenshot://<имя файла>. "
    "Работает только в KDE Plasma 6 на Wayland. Заголовки и текст можно "
    "передавать на русском — в JS-скрипты они не попадают."
)

mcp = FastMCP(
    SERVER_NAME,
    instructions=instructions,
)


# ---------------------------------------------------------------------------
# Хелперы.
# ---------------------------------------------------------------------------


def _capture(tmp_path, fullscreen=False, delay_ms=0):
    """Обёртка над kwinlib.capture, ошибки — в ToolError."""
    size, err = kwinlib.capture(tmp_path, fullscreen=fullscreen, delay_ms=delay_ms)
    if err:
        raise ToolError("снимок не удался (%s): %s" % (err[0], err[1] or ""))
    return size


async def _list_windows_or_raise(ctx, timeout=8.0):
    """Список окон KWin; инфраструктурные ошибки превращает в ToolError."""
    windows, errs = kwinlib.list_windows(timeout=timeout)
    if errs:
        text = "; ".join(errs)
        await ctx.error("KWin: %s" % text)
        raise ToolError("инфраструктура недоступна: %s" % text)
    return windows


def _resolve_output(path=None, tag="shot"):
    """Абсолютный путь выходного файла: явный path либо новый файл в OUTPUT_DIR."""
    if path:
        p = Path(path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        return str(p)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    return str(OUTPUT_DIR / ("%s_%s.png" % (tag, ts)))


def _resource_uri(abs_path):
    """screenshot://<имя> для файла внутри OUTPUT_DIR, иначе '<нет ресурса>'."""
    try:
        rel = Path(abs_path).resolve().relative_to(OUTPUT_DIR.resolve())
    except ValueError:
        return None
    return "screenshot://%s" % rel


def _resolve_image_path(spec):
    """Преобразует аргумент в абсолютный путь изображения.

    Принимает обычный путь к файлу и ресурс screenshot://<имя> (файл из
    WAYLAND_SHOT_OUTPUT). Возвращает (abs_path|None, error|None).
    """
    if spec.startswith("screenshot://"):
        name = spec[len("screenshot://") :]
        if not _NAME_RE.fullmatch(name):
            return None, "недопустимое имя ресурса: %r" % spec
        path = OUTPUT_DIR / name
        return (
            (str(path), None)
            if path.exists()
            else (None, "снимок не найден: %s" % path)
        )
    path = Path(spec).expanduser()
    if not path.exists():
        return None, "файл не найден: %s" % spec
    return str(path), None


# ---------------------------------------------------------------------------
# Инструменты: окна.
# ---------------------------------------------------------------------------


@mcp.tool(
    description=(
        "Полный список окон KDE Plasma (Wayland): заголовок, pid, класс, "
        "геометрия (логические координаты), флаги активного и свёрнутого окна. "
        "Полезно для обзора перед выбором окна для снимка."
    )
)
async def list_windows(ctx: Context) -> dict:
    """Все окна сессии с геометрией и статусом."""
    windows = await _list_windows_or_raise(ctx)
    await ctx.info("окон найдено: %d" % len(windows))
    return {"count": len(windows), "windows": windows}


@mcp.tool(
    description=(
        "Поиск окон по pid, по подстроке заголовка и/или по подстроке ресурсного "
        "класса (например 'firefox', 'kate', 'alacritty'). Фильтры можно комбинировать; "
        "все сравнения без учёта регистра. Возвращает найденные окна с геометрией."
    )
)
async def find_window(
    ctx: Context,
    pid: int | None = None,
    title: str | None = None,
    resource_class: str | None = None,
    active_only: bool = False,
) -> dict:
    """Поиск окна по pid / заголовку / классу."""
    windows = await _list_windows_or_raise(ctx)
    sel = kwinlib.filter_windows(
        windows,
        pid=pid,
        title=title,
        resource_class=resource_class,
        active_only=active_only,
    )
    await ctx.info("совпадений: %d" % len(sel))
    return {"count": len(sel), "windows": sel}


@mcp.tool(
    description=(
        "Информация о текущем активном окне: pid и заголовок. "
        "Работает через workspace.activeWindow."
    )
)
async def get_active_window(ctx: Context) -> dict:
    """Активное окно сессии."""
    info, errs = kwinlib.active_window_info()
    if errs:
        raise ToolError("; ".join(errs))
    await ctx.info("активное окно: %s" % info.get("caption", "—"))
    return {"window": info if info.get("pid", -1) != -1 else None}


@mcp.tool(
    description=(
        "Активирует окно (фокус + поднятие наверх) по pid или подстроке заголовка. "
        "После каждой попытки проверяет workspace.activeWindow и повторяет при "
        "несовпадении. retries — число попыток, wait_ms — пауза перед проверкой."
    )
)
async def activate_window(
    ctx: Context,
    pid: int | None = None,
    title: str | None = None,
    resource_class: str | None = None,
    retries: int = 3,
    wait_ms: int = 800,
) -> dict:
    """Фокус + поднятие окна с проверкой активного окна."""
    if pid is None and not title and not resource_class:
        raise ToolError("укажите pid, title или resource_class")
    windows = await _list_windows_or_raise(ctx)
    sel = kwinlib.filter_windows(
        windows, pid=pid, title=title, resource_class=resource_class
    )
    if not sel:
        raise ToolError("окно не найдено по заданным фильтрам")
    target = sel[0]
    target_pid = target["pid"]

    for attempt in range(1, retries + 1):
        await ctx.report_progress(
            attempt, retries, "активация окна pid=%d" % target_pid
        )
        act, aerrs = kwinlib.activate_by_pid(target_pid)
        if aerrs:
            raise ToolError("; ".join(aerrs))
        if not act.get("found"):
            raise ToolError("окно pid=%d исчезло из windowList()" % target_pid)
        time.sleep(wait_ms / 1000.0)
        aw, werrs = kwinlib.active_window_info()
        if werrs:
            raise ToolError("; ".join(werrs))
        active_pid = aw.get("pid", -1)
        if active_pid == target_pid:
            await ctx.info(
                "окно pid=%d активировано: «%s»" % (target_pid, act.get("caption", ""))
            )
            return {
                "ok": True,
                "pid": target_pid,
                "caption": act.get("caption", ""),
                "attempts": attempt,
            }
        await ctx.warning(
            "попытка %d: активное окно pid=%d, ожидалось %d"
            % (attempt, active_pid, target_pid)
        )

    raise ToolError("не удалось активировать окно pid=%d" % target_pid)


# ---------------------------------------------------------------------------
# Инструменты: снимки.
# ---------------------------------------------------------------------------


@mcp.tool(
    description=(
        "Главный инструмент: снимок конкретного окна. Окно ищется по pid/заголовку/классу, "
        "активируется и снимается через Spectacle (-a). "
        "size_check: 'calibrate' — эталон размера берётся из первого кадра и сверяется на "
        "последующих (ловит «сняли не то окно»), 'fixed' — ожидаемый размер из expect_w/"
        "expect_h (физические пиксели), 'off' — без контроля. marker — подстрока, которую "
        "должен содержать текст окна (OCR, lang). Возвращает путь к PNG, размер и "
        "resource_uri для чтения картинки; resource_uri передаётся в ocr_text и "
        "verify_ocr как screenshot_uri."
    )
)
async def capture_window(
    ctx: Context,
    pid: int | None = None,
    title: str | None = None,
    resource_class: str | None = None,
    output: str | None = None,
    retries: int = 6,
    wait_ms: int = 800,
    size_check: str = "calibrate",
    expect_w: int | None = None,
    expect_h: int | None = None,
    marker: str | None = None,
    lang: str = "rus+eng",
) -> dict:
    """Снимок конкретного окна: поиск -> активация -> кадр -> контроль -> сохранение."""
    if pid is None and not title and not resource_class:
        raise ToolError("укажите pid, title или resource_class")
    if size_check not in ("calibrate", "fixed", "off"):
        raise ToolError("size_check должен быть 'calibrate', 'fixed' или 'off'")
    if size_check == "fixed" and (expect_w is None or expect_h is None):
        raise ToolError("для size_check='fixed' задайте expect_w и expect_h")

    windows = await _list_windows_or_raise(ctx)
    sel = kwinlib.filter_windows(
        windows, pid=pid, title=title, resource_class=resource_class
    )
    if not sel:
        raise ToolError("окно не найдено по заданным фильтрам")
    target = sel[0]
    await ctx.info(
        "целевое окно pid=%d: «%s» [%s] %dx%d"
        % (
            target["pid"],
            target["caption"],
            target.get("resourceClass", ""),
            target.get("width", 0),
            target.get("height", 0),
        )
    )

    fixed_expected = (expect_w, expect_h) if size_check == "fixed" else None
    final_path = _resolve_output(output, tag="win")
    calibrated = None
    last = None

    for attempt in range(1, retries + 1):
        await ctx.report_progress(
            attempt, retries, "попытка %d: активация и снимок окна" % attempt
        )
        act, aerrs = kwinlib.activate_by_pid(target["pid"])
        if aerrs:
            raise ToolError("; ".join(aerrs))
        if not act.get("found"):
            raise ToolError("окно pid=%d исчезло из windowList()" % target["pid"])
        time.sleep(wait_ms / 1000.0)

        size = _capture(_TMP_SHOT, fullscreen=False)
        last = (size["width"], size["height"])
        msg = "попытка %d: размер кадра %dx%d" % (attempt, last[0], last[1])

        if size_check == "calibrate":
            if calibrated is None:
                calibrated = last
                msg += " -> эталон %dx%d" % calibrated
            elif last != calibrated:
                msg += " -> не совпало с эталоном %dx%d, повтор" % calibrated
                await ctx.warning(msg)
                continue
            else:
                msg += " -> совпал с эталоном"
        elif size_check == "fixed":
            if last != fixed_expected:
                msg += " -> не совпало с ожидаемым %dx%d, повтор" % fixed_expected
                await ctx.warning(msg)
                continue
            msg += " -> совпал с ожидаемым"

        marker_ok = True
        if marker:
            text = kwinlib.ocr_text(_TMP_SHOT, lang=lang, psm=6)
            marker_ok = marker.lower() in text.lower()
            msg += ", маркер «%s»: %s" % (
                marker,
                "найден" if marker_ok else "не найден",
            )

        kwinlib.copy_file(_TMP_SHOT, final_path)
        uri = _resource_uri(final_path)
        await ctx.info("сохранено: %s" % final_path)
        return {
            "ok": True,
            "path": os.path.abspath(final_path),
            "resource_uri": uri,
            "width": last[0],
            "height": last[1],
            "attempts": attempt,
            "marker_found": marker_ok,
            "note": (
                "снимок сохранён, но маркер в тексте окна не найден"
                if not marker_ok
                else None
            ),
        }

    raise ToolError(
        "ни одна из %d попыток не дала кадр нужного размера (последний: %dx%d)"
        % (retries, last[0] if last else 0, last[1] if last else 0)
    )


@mcp.tool(
    description=(
        "Снимок текущего активного окна через Spectacle (-a), без активации. "
        "Возвращает путь к PNG, размер и resource_uri; resource_uri передаётся в ocr_text "
        "и verify_ocr как screenshot_uri. output — необязательный путь; "
        "по умолчанию файл создаётся в WAYLAND_SHOT_OUTPUT."
    )
)
async def capture_active_window(ctx: Context, output: str | None = None) -> dict:
    """Снимок активного (сфокусированного) окна."""
    out = _resolve_output(output, tag="window")
    size = _capture(out, fullscreen=False)
    uri = _resource_uri(out)
    await ctx.info("сохранено: %s (%dx%d)" % (out, size["width"], size["height"]))
    return {
        "ok": True,
        "path": os.path.abspath(out),
        "resource_uri": uri,
        "width": size["width"],
        "height": size["height"],
    }


@mcp.tool(
    description=(
        "Снимок всего рабочего стола через Spectacle (-f). Возвращает путь к PNG, "
        "размер и resource_uri; resource_uri передаётся в ocr_text и verify_ocr как "
        "screenshot_uri. output — необязательный путь."
    )
)
async def capture_screen(ctx: Context, output: str | None = None) -> dict:
    """Снимок всего экрана (фолбэк, если окно снять нельзя)."""
    out = _resolve_output(output, tag="screen")
    size = _capture(out, fullscreen=True)
    uri = _resource_uri(out)
    await ctx.info("сохранено: %s (%dx%d)" % (out, size["width"], size["height"]))
    return {
        "ok": True,
        "path": os.path.abspath(out),
        "resource_uri": uri,
        "width": size["width"],
        "height": size["height"],
    }


# ---------------------------------------------------------------------------
# Инструменты: OCR.
# ---------------------------------------------------------------------------


def _require_image(image_path=None, screenshot_uri=None):
    """Резолвит путь к изображению из image_path или screenshot_uri.

    Оба аргумента необязательны по отдельности, но хотя бы один должен быть
    задан. screenshot_uri принимает значение поля resource_uri из результата
    capture_* без преобразований; image_path — путь к файлу (допускается и
    ресурс screenshot://<имя>). Если заданы оба, они должны указывать на один
    файл: сравниваются разрешённые пути, а не исходные строки, поэтому запись
    «ресурс» и «путь» считаются одним и тем же.
    """
    if not screenshot_uri and not image_path:
        raise ToolError(
            "укажите screenshot_uri (из результата capture_*) или image_path"
        )

    if screenshot_uri and image_path:
        by_path, err_path = _resolve_image_path(image_path)
        by_uri, err_uri = _resolve_image_path(screenshot_uri)
        if err_path or err_uri:
            raise ToolError("; ".join(e for e in (err_path, err_uri) if e))
        if os.path.abspath(by_path) != os.path.abspath(by_uri):
            raise ToolError(
                "screenshot_uri и image_path указывают на разные файлы: %s и %s; "
                "оставьте что-то одно" % (by_uri, by_path)
            )
        return os.path.abspath(by_path)

    abs_path, err = _resolve_image_path(screenshot_uri or image_path)
    if abs_path is None:
        raise ToolError(err)
    return abs_path


@mcp.tool(
    description=(
        "Распознаёт текст на изображении через tesseract (по умолчанию psm 6, языки "
        "rus+eng). screenshot_uri — значение поля resource_uri из результата "
        "capture_* (screenshot://<имя>), передаётся без преобразований; image_path — "
        "абсолютный путь к PNG/JPG. Достаточно одного из двух. lang — языки из "
        "'tesseract --list-langs'."
    )
)
async def ocr_text(
    ctx: Context,
    image_path: str | None = None,
    lang: str = "rus+eng",
    screenshot_uri: str | None = None,
) -> dict:
    """Текст изображения целиком."""
    abs_path = _require_image(image_path, screenshot_uri)
    text, err = kwinlib.ocr_text_full(abs_path, lang=lang, psm=6)
    if err:
        raise ToolError("tesseract (%s): %s" % (lang, err))
    await ctx.info("распознано символов: %d" % len(text))
    return {"language": lang, "text": text}


@mcp.tool(
    description=(
        "Распознаёт слова на изображении с координатами (tesseract --psm 11 tsv): "
        "слово, уверенность 0-100, left/top/width/height в пикселях снимка. "
        "min_conf отсекает неуверенные слова. screenshot_uri — значение resource_uri "
        "из результата capture_*; image_path — путь к файлу. Достаточно одного из "
        "двух. Координаты можно использовать для последующих действий по снимку."
    )
)
async def ocr_words(
    ctx: Context,
    image_path: str | None = None,
    lang: str = "rus+eng",
    min_conf: float = 0.0,
    screenshot_uri: str | None = None,
) -> dict:
    """Слова с координатами из изображения."""
    abs_path = _require_image(image_path, screenshot_uri)
    words, err = kwinlib.ocr_words_full(abs_path, lang=lang, psm=11, min_conf=min_conf)
    if err:
        raise ToolError("tesseract (%s): %s" % (lang, err))
    data = [
        {
            "word": w[0],
            "confidence": w[1],
            "left": w[2],
            "top": w[3],
            "width": w[4],
            "height": w[5],
        }
        for w in words
    ]
    await ctx.info("слов распознано: %d" % len(data))
    return {"language": lang, "count": len(data), "words": data}


@mcp.tool(
    description=(
        "OCR-проверка: распознаёт текст снимка (psm 6) и определяет, содержит ли он "
        "ожидаемую подстроку expected (без учёта регистра). screenshot_uri — значение "
        "resource_uri из результата capture_*; image_path — путь к файлу. Достаточно "
        "одного из двух. Возвращает found, число вхождений и фрагмент "
        "текста вокруг первого совпадения. Удобно для assert-сценариев: после "
        "capture_window проверить, что в кадре действительно нужный контент."
    )
)
async def verify_ocr(
    ctx: Context,
    expected: str,
    image_path: str | None = None,
    lang: str = "rus+eng",
    screenshot_uri: str | None = None,
) -> dict:
    """Проверка, что текст на снимке содержит ожидаемую подстроку."""
    if not expected or not expected.strip():
        raise ToolError("укажите непустую expected")
    abs_path = _require_image(image_path, screenshot_uri)
    text, err = kwinlib.ocr_text_full(abs_path, lang=lang, psm=6)
    if err:
        raise ToolError("tesseract (%s): %s" % (lang, err))
    needle = expected.strip().lower()
    hay = text.lower()
    found = needle in hay
    occurrences = hay.count(needle)
    context = None
    if found:
        pos = hay.find(needle)
        start = max(0, pos - 90)
        end = min(len(text), pos + len(needle) + 90)
        context = text[start:end].replace("\n", " ")
    await ctx.info(
        "маркер «%s»: %s (%d вхожд.)"
        % (expected, "найден" if found else "не найден", occurrences)
    )
    return {
        "found": found,
        "expected": expected.strip(),
        "language": lang,
        "occurrences": occurrences,
        "context": context,
        "image_path": abs_path,
    }


# ---------------------------------------------------------------------------
# Ресурсы: снимки.
# ---------------------------------------------------------------------------


@mcp.resource(
    "screenshot://{name}",
    name="screenshot",
    mime_type="image/png",
    description=(
        "PNG-снимок из WAYLAND_SHOT_OUTPUT по имени файла (поле resource_uri "
        "результата capture_*). Имя без разделителей путей."
    ),
)
async def screenshot_resource(name: str) -> bytes:
    """Содержимое PNG-файла снимка по имени (только из WAYLAND_SHOT_OUTPUT)."""
    if not _NAME_RE.fullmatch(name):
        raise ToolError("недопустимое имя файла: %r" % name)
    path = OUTPUT_DIR / name
    if not path.exists():
        raise ToolError("снимок не найден: %s (ищите в %s)" % (name, OUTPUT_DIR))
    return path.read_bytes()


@mcp.resource(
    "screenshots://list",
    name="screenshots-list",
    mime_type="text/plain",
    description="Список файлов снимков в WAYLAND_SHOT_OUTPUT (по одному на строку).",
)
async def screenshots_list() -> str:
    """Список сохранённых снимков."""
    if not OUTPUT_DIR.exists():
        return "(каталог снимков пуст: %s)" % OUTPUT_DIR
    names = sorted(p.name for p in OUTPUT_DIR.iterdir() if p.is_file())
    if not names:
        return "(каталог снимков пуст: %s)" % OUTPUT_DIR
    return "\n".join(names)


# ---------------------------------------------------------------------------
# Точка входа.
# ---------------------------------------------------------------------------


def main(argv=None):
    """Запуск MCP-сервера (по умолчанию транспорт stdio)."""
    ap = argparse.ArgumentParser(
        prog="wayland-shot-mcp-server",
        description="MCP-сервер скриншотов KDE Plasma (Wayland).",
    )
    ap.add_argument(
        "--stdio",
        action="store_true",
        help="транспорт stdio (установлен по умолчанию; флаг для совместимости)",
    )
    ap.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="транспорт MCP (по умолчанию stdio)",
    )
    args = ap.parse_args(argv)

    kwinlib.ensure_tmp()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.transport == "sse":
        print(
            "SSE-транспорт: http://127.0.0.1:8765/sse "
            "(точный адрес зависит от запуска mcp.run)",
            file=os.sys.stderr,
        )
        mcp.run(transport="sse")
    else:
        # Сторож родителя: если сервис opencode, породивший этот процесс,
        # завершится, сервер закроется сам и не останется «сиротой».
        kwinlib.spawn_parent_watchdog()
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
