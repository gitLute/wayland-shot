#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Общая библиотека: нативное Wayland-управление окнами через KWin scripting.

Портировано из наработки MCP_DEV/wayland-shot (kwinlib.py) без изменения
логики. KWin (KDE Plasma 6) не отдаёт список окон по D-Bus напрямую.
Нативный способ — скриптинг самого оконного менеджера через интерфейс
org.kde.kwin.Scripting:

    qdbus org.kde.KWin /Scripting org.kde.kwin.Scripting.loadScript <путь> <имя>
    qdbus org.kde.KWin /Scripting org.kde.kwin.Scripting.start
    qdbus org.kde.KWin /Scripting org.kde.kwin.Scripting.unloadScript <имя>

Методы loadScript/start ничего не возвращают, поэтому результат передаётся
из JS наружу каналом print() -> kDebug -> журнал kwin_wayland (journalctl).
Запускающий процесс собирает строки с маркером "KJS_RESULT <json>" и
опрашивает журнал с таймаутом.

Ограничение KWin 6 (движок QJSEngine): в JS-скрипте нет API записи файлов
(нет глобальных writeFile/process/Qt), поэтому JSON-результаты материализуются
в файл на стороне Python (write_result_file).

Безопасность секретов: библиотека не читает конфиги и переменные с ключами;
в JS-шаблоны подставляются только числа и ASCII-ключи — пользовательский
текст (в том числе кириллица) никогда не попадает в JS.
"""

import json
import os
import subprocess
import threading
import time

# PPID, зафиксированный при импорте модуля (родитель — сервис opencode,
# запустивший процесс). Захват на раннем этапе важен: тяжёлые импорты
# (numpy/scipy/sympy/matplotlib) могут идти дольше жизни родителя, и сторож
# должен знать исходного родителя, а не subreaper'а, к которому процесс
# будет переподчинён после смерти родителя.
_INIT_PPID = os.getppid()

# Маркер, по которому Python находит результат в журнале kwin_wayland.
MARKER = "KJS_RESULT"

# Рабочий каталог для временных файлов (JS-шаблонов и промежуточных снимков).
TMP_ROOT = os.environ.get("WSHOT_TMP", "/tmp/opencode/wayland-shot")

# Счётчик вызовов в пределах процесса: маркер уникален для каждого запуска,
# чтобы не подхватывать результат соседних запусков из журнала.
_SEQ = [0]


def spawn_parent_watchdog(interval=2.0):
    """Фоновый поток, завершающий процесс, если умер процесс-родитель.

    Локальные MCP-серверы opencode запускает как детей фонового сервиса
    (`opencode serve --service`). При штатном отключении транспорта сервер
    завершается сам (EOF на stdin), но если родитель погибает без закрытия
    каналов (kill -9, падение сервиса), процесс остаётся «сиротой» и висит
    в системе. Сторож следит за сменой PPID: осиротевший процесс
    переподчиняется init/subreaper'у, и его текущий PPID отличается от
    исходного — тогда сторожа завершают сервер принудительно.

    Запущенный вручную (родитель — init, PPID <= 1) сервер не трогаем.
    """
    initial = _INIT_PPID
    if initial <= 1:
        return

    def _watch():
        while True:
            time.sleep(interval)
            if os.getppid() != initial:
                os._exit(0)

    threading.Thread(target=_watch, daemon=True).start()


def _next_marker():
    _SEQ[0] += 1
    return "%s_%d_%d" % (MARKER, os.getpid(), _SEQ[0])


def ensure_tmp():
    """Создаёт временный каталог, если его нет, и возвращает его путь."""
    os.makedirs(TMP_ROOT, exist_ok=True)
    return TMP_ROOT


def ensure_session_env():
    """Достраивает окружение сессии, если клиент MCP его не передал.

    stdio-клиенты (mcp.client.stdio, opencode) запускают сервер с урезанным
    окружением (get_default_environment): без DBUS_SESSION_BUS_ADDRESS,
    WAYLAND_DISPLAY и XDG_RUNTIME_DIR. Все три адреса стандартны для
    systemd user-сессии и восстанавливаются: runtime-каталог — из
    /run/user/<uid> (фолбэк XDG_RUNTIME_DIR), шина — unix:path=<runtime>/bus,
    дисплей Wayland — wayland-0.
    """
    if not os.environ.get("XDG_RUNTIME_DIR"):
        try:
            candidate = "/run/user/%d" % os.getuid()
            if os.path.isdir(candidate):
                os.environ["XDG_RUNTIME_DIR"] = candidate
        except OSError:
            pass
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime and not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        os.environ["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=%s/bus" % runtime
    os.environ.setdefault("WAYLAND_DISPLAY", "wayland-0")
    os.environ.setdefault("XDG_SESSION_TYPE", "wayland")


def kwin_wayland_pid():
    """PID процесса kwin_wayland (не обёртки kwin_wayland_wrapper)."""
    try:
        out = subprocess.run(
            ["pgrep", "-x", "kwin_wayland"],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
    except Exception:
        return None
    for line in out.splitlines():
        s = line.strip()
        if s.isdigit():
            return int(s)
    return None


def kwin_ready():
    """Проверяет доступность D-Bus интерфейса скриптинга KWin."""
    ensure_session_env()
    try:
        r = subprocess.run(
            ["qdbus", "org.kde.KWin", "/Scripting", "org.freedesktop.DBus.Peer.Ping"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return r.returncode == 0
    except Exception:
        return False


def _journal_lines(pid, since_iso):
    """Строки журнала kwin_wayland за период начиная с since_iso."""
    try:
        r = subprocess.run(
            [
                "journalctl",
                "--user",
                "-b",
                "_PID=%d" % pid,
                "-o",
                "cat",
                "--since",
                since_iso,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return []
    if r.returncode != 0:
        return []
    return r.stdout.splitlines()


def run_kwin_script(js, base_name, timeout=8.0, lookback=2.0):
    """Запускает JS-скрипт в KWin и собирает строки с маркером KJS_RESULT.

    Возвращает кортеж (results, errs): results — список строк полезной
    нагрузки (JSON), errs — список текстовых ошибок.
    """
    pid = kwin_wayland_pid()
    if pid is None:
        return [], ["kwin_wayland не найден в списке процессов"]
    ensure_session_env()
    if not kwin_ready():
        return [], ["нет связи с org.kde.KWin через qdbus (нет Wayland-сессии KDE?)"]

    marker = _next_marker()
    js = js.replace("@@MARKER@@", marker)
    name = "%s_%d" % (base_name, os.getpid())
    js_path = os.path.join(ensure_tmp(), name + ".js")
    with open(js_path, "w", encoding="utf-8") as f:
        f.write(js)

    def dbus_call(method, *args):
        cmd = [
            "qdbus",
            "org.kde.KWin",
            "/Scripting",
            "org.kde.kwin.Scripting." + method,
        ] + list(args)
        subprocess.run(cmd, capture_output=True, text=True, timeout=15)

    # Журнал начинаем читать чуть раньше старта скрипта, чтобы не потерять
    # мгновенно напечатанные строки (журналирование идёт асинхронно).
    since = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - lookback))
    try:
        dbus_call("loadScript", js_path, name)
        dbus_call("start")
    except Exception as e:
        return [], ["не удалось запустить скрипт KWin: %s" % e]

    results = []
    errs = []
    seen = set()
    deadline = time.time() + timeout
    while time.time() < deadline:
        for line in _journal_lines(pid, since):
            if not line.startswith(marker) or line in seen:
                continue
            seen.add(line)
            payload = line[len(marker) :].strip()
            if payload.startswith("ERR "):
                errs.append(payload[4:])
            else:
                results.append(payload)
        if results or errs:
            break
        time.sleep(0.15)

    try:
        dbus_call("unloadScript", name)
    except Exception:
        pass
    try:
        os.remove(js_path)
    except OSError:
        pass

    if not results and not errs:
        errs.append("скрипт KWin не вернул результат за %.1f с" % timeout)
    return results, errs


def parse_results(results, errs):
    """Преобразует сырые строки результата в Python-объекты."""
    objs = []
    for line in results:
        if line.startswith("ERR "):
            errs.append(line[4:])
            continue
        try:
            objs.append(json.loads(line))
        except ValueError as e:
            errs.append("некорректный JSON из KWin: %s" % e)
    return objs, errs


# ---------------------------------------------------------------------------
# Шаблоны JS-скриптов KWin (только ASCII-текст; подстановки через @@ТОКЕН@@).
# ---------------------------------------------------------------------------

_JS_LIST_WINDOWS = """(function () {
  try {
    var list = workspace.windowList();
    var out = [];
    for (var i = 0; i < list.length; i++) {
      var w = list[i];
      var fg = null;
      try { fg = w.frameGeometry; } catch (e) {}
      var x = 0, y = 0, ww = 0, hh = 0, rnd = Math.round;
      if (fg) {
        x = rnd(fg.x) || 0; y = rnd(fg.y) || 0;
        ww = rnd(fg.width) || 0; hh = rnd(fg.height) || 0;
      } else {
        x = rnd(w.x) || 0; y = rnd(w.y) || 0;
        ww = rnd(w.width) || 0; hh = rnd(w.height) || 0;
      }
      out.push({
        caption: (w.caption || ""),
        pid: w.pid,
        resourceClass: (w.resourceClass || ""),
        x: x, y: y, width: ww, height: hh,
        active: !!w.active,
        minimized: !!w.minimized
      });
    }
    print("@@MARKER@@ " + JSON.stringify(out));
  } catch (e) {
    print("@@MARKER@@ ERR " + String(e));
  }
})();
"""

_JS_ACTIVATE_BY_PID = """(function () {
  try {
    var list = workspace.windowList();
    var found = false, cap = "";
    for (var i = 0; i < list.length; i++) {
      if (list[i].pid === @@PID@@) {
        var w = list[i];
        if (typeof w.requestActivate === "function") { try { w.requestActivate(); } catch (e) {} }
        if (typeof w.activate === "function") { try { w.activate(); } catch (e) {} }
        try { workspace.activeWindow = w; } catch (e) {}
        try { workspace.raiseWindow(w); } catch (e) {}
        cap = w.caption || "";
        found = true;
        break;
      }
    }
    print("@@MARKER@@ " + JSON.stringify({found: found, caption: cap}));
  } catch (e) {
    print("@@MARKER@@ ERR " + String(e));
  }
})();
"""

_JS_ACTIVE_WINDOW = """(function () {
  try {
    var aw = workspace.activeWindow;
    print("@@MARKER@@ " + JSON.stringify({
      pid: aw ? aw.pid : -1,
      caption: aw ? (aw.caption || "") : ""
    }));
  } catch (e) {
    print("@@MARKER@@ ERR " + String(e));
  }
})();
"""


def list_windows(timeout=8.0):
    """Все окна: список словарей caption/pid/resourceClass/геометрия. (list, errs)"""
    results, errs = run_kwin_script(_JS_LIST_WINDOWS, "kjs_list", timeout=timeout)
    objs, errs = parse_results(results, errs)
    windows = objs[0] if objs else []
    if not isinstance(windows, list):
        errs.append("ожидался JSON-массив окон, получено: %r" % (windows,))
        windows = []
    return windows, errs


def activate_by_pid(pid, timeout=8.0):
    """Активирует окно процесса pid. Возвращает (dict, errs)."""
    js = _JS_ACTIVATE_BY_PID.replace("@@PID@@", str(int(pid)))
    results, errs = run_kwin_script(js, "kjs_activate", timeout=timeout)
    objs, errs = parse_results(results, errs)
    return (objs[0] if objs else {}), errs


def active_window_info(timeout=8.0):
    """Информация об активном окне: {pid, caption}. Возвращает (dict, errs)."""
    results, errs = run_kwin_script(_JS_ACTIVE_WINDOW, "kjs_active", timeout=timeout)
    objs, errs = parse_results(results, errs)
    return (objs[0] if objs else {}), errs


def filter_windows(
    windows, pid=None, title=None, resource_class=None, active_only=False
):
    """Фильтрует список окон по pid / подстроке заголовка / ресурсному классу.

    Сравнение заголовка и класса — без учёта регистра. Если фильтров нет,
    возвращается исходный список.
    """
    sel = windows
    if pid is not None:
        sel = [w for w in sel if w.get("pid") == pid]
    if title:
        needle = title.lower()
        sel = [w for w in sel if needle in w.get("caption", "").lower()]
    if resource_class:
        needle = resource_class.lower()
        sel = [w for w in sel if needle in w.get("resourceClass", "").lower()]
    if active_only:
        sel = [w for w in sel if w.get("active")]
    return sel


# ---------------------------------------------------------------------------
# Съёмка и OCR.
# ---------------------------------------------------------------------------


def image_size(path):
    """Размер и смещение изображения через identify: WxH+X+Y или None."""
    r = subprocess.run(
        ["identify", "-format", "%w %h %[fx:page.x] %[fx:page.y]", path],
        capture_output=True,
        text=True,
        timeout=15,
    )
    parts = r.stdout.split()
    if len(parts) != 4:
        return None
    try:
        return {
            "width": int(parts[0]),
            "height": int(parts[1]),
            "x": int(parts[2]),
            "y": int(parts[3]),
        }
    except ValueError:
        return None


def capture(out_path, fullscreen=False, delay_ms=0):
    """Снимок через spectacle: активное окно (-a) либо весь экран (-f).

    Возвращает (size_dict|None, error_info|None).
    """
    ensure_session_env()
    parent = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(parent, exist_ok=True)
    cmd = ["spectacle", "-b", "-n"]
    cmd += ["-f"] if fullscreen else ["-a"]
    if delay_ms:
        cmd += ["-d", str(int(delay_ms))]
    cmd += ["-o", out_path]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except Exception as e:
        return None, ("запуск", str(e))
    if r.returncode != 0 or not os.path.exists(out_path):
        return None, ("spectacle", r.stderr.strip() or "код %d" % r.returncode)
    size = image_size(out_path)
    if not size or size["width"] == 0 or size["height"] == 0:
        return None, ("identify", "не удалось определить размер")
    return size, None


def ocr_text(path, lang="rus+eng", psm=6):
    """Текст из изображения через tesseract (stdout).

    Устаревшая обёртка: при ошибке tesseract возвращает пустую строку без
    объяснения. Для сервера используйте ocr_text_full.
    """
    text, _err = ocr_text_full(path, lang=lang, psm=psm)
    return text


def ocr_text_full(path, lang="rus+eng", psm=6):
    """Текст из изображения с диагностикой. Возвращает (text, error|None)."""
    try:
        r = subprocess.run(
            ["tesseract", path, "stdout", "-l", lang, "--psm", str(psm)],
            capture_output=True,
            timeout=60,
        )
    except Exception as e:
        return "", "запуск tesseract: %s" % e
    if r.returncode != 0:
        err = r.stderr.decode("utf-8", "replace").strip()
        return "", err or "tesseract вернул код %d" % r.returncode
    return r.stdout.decode("utf-8", "replace"), None


def ocr_words(path, lang="rus+eng", psm=11, min_conf=0):
    """Слова с координатами через tesseract --psm 11 tsv (список кортежей).

    Устаревшая обёртка: при ошибке возвращает []. Для сервера используйте
    ocr_words_full.
    """
    words, _err = ocr_words_full(path, lang=lang, psm=psm, min_conf=min_conf)
    return words


def ocr_words_full(path, lang="rus+eng", psm=11, min_conf=0):
    """Слова с координатами и диагностикой. Возвращает (words, error|None)."""
    try:
        r = subprocess.run(
            ["tesseract", path, "stdout", "-l", lang, "--psm", str(psm), "tsv"],
            capture_output=True,
            timeout=60,
        )
    except Exception as e:
        return [], "запуск tesseract: %s" % e
    if r.returncode != 0:
        err = r.stderr.decode("utf-8", "replace").strip()
        return [], err or "tesseract вернул код %d" % r.returncode
    lines = r.stdout.decode("utf-8", "replace").splitlines()
    if not lines:
        return [], None
    header = lines[0].split("\t")
    cols = {name: i for i, name in enumerate(header)}
    words = []
    for line in lines[1:]:
        parts = line.split("\t")
        if len(parts) < len(header):
            continue
        text = parts[cols["text"]]
        if not text.strip():
            continue
        try:
            conf = float(parts[cols["conf"]])
        except (ValueError, KeyError):
            continue
        if conf < min_conf:
            continue
        words.append(
            (
                text,
                conf,
                int(parts[cols["left"]]),
                int(parts[cols["top"]]),
                int(parts[cols["width"]]),
                int(parts[cols["height"]]),
            )
        )
    return words, None


def copy_file(src, dst):
    """Копирует файл (создавая родительский каталог)."""
    dst = os.path.abspath(dst)
    parent = os.path.dirname(dst)
    if parent:
        os.makedirs(parent, exist_ok=True)
    subprocess.run(["cp", "-f", src, dst], check=True)


def write_result_file(path, obj):
    """Записывает объект в JSON-файл (ensure_ascii=False). Возвращает путь."""
    path = os.path.abspath(path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    return path
