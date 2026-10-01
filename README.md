# wayland-shot-mcp-server

MCP-сервер (Model Context Protocol) для создания скриншотов в KDE Plasma 6
нативно для Wayland: без X11/Xwayland, только KWin scripting, Spectacle,
ImageMagick (identify) и tesseract. Позволяет LLM-агенту перечислять и
искать окна, активировать их, снимать конкретное окно или весь экран и
распознавать текст на снимке (OCR).

Сервер — переработка наработки `MCP_DEV/wayland-shot` (набор CLI-скриптов
`win_id.py` / `win_activate.py` / `shot.py` / `ocr.py` / `shoot.py`) в
один устанавливаемый пакет с консольным скриптом; оригинальная наработка
не изменялась.

## Установка

Проект устанавливается в общий Python-venv для MCP-серверов
`/home/lute/.local/bin/MCP/PytnonVenv` (Python 3.14, `mcp>=1.12`).

Сам venv создаётся один раз системным `python3` (проверено на 3.14.7,
внутри появляется `pip` 26.x):

```bash
python3 -m venv /home/lute/.local/bin/MCP/PytnonVenv
```

Каталог с исходниками `wayland-shot-mcp-server/` лежит **внутри** venv,
поэтому ключ `--clear` у `python3 -m venv` использовать нельзя: он удалит
содержимое каталога вместе с исходниками. Пересоздание venv на месте
безопаснее делать вручную — удалить `bin/` и `lib/` и повторить команду
выше.

Установка пакета в режиме редактирования (исходники подхватываются сразу):

```bash
/home/lute/.local/bin/MCP/PytnonVenv/bin/pip install -e \
  /home/lute/.local/bin/MCP/PytnonVenv/wayland-shot-mcp-server
```

После установки появляется консольный скрипт:

```bash
/home/lute/.local/bin/MCP/PytnonVenv/bin/wayland-shot-mcp-server --stdio
```

Пакет также можно запустить из исходников:

```bash
/home/lute/.local/bin/MCP/PytnonVenv/bin/python -m wayland_shot
```

Зависимости вне Python (уже есть в системе): `spectacle`, `identify`
(ImageMagick), `tesseract` (с языками `rus`, `eng`), `qdbus`, `journalctl`,
рабочая сессия KDE Plasma на Wayland.

## Подключение к opencode

В `~/.config/opencode/opencode.json` в секцию `mcp.servers` добавляется:

```json
"wayland-shot": {
  "type": "local",
  "command": [
    "/home/lute/.local/bin/MCP/PytnonVenv/bin/wayland-shot-mcp-server",
    "--stdio"
  ]
}
```

После перезапуска opencode сервер доступен как `wayland-shot`.

## Инструменты

| Инструмент | Назначение |
|---|---|
| `list_windows` | все окна сессии: заголовок, pid, класс, геометрия, флаги |
| `find_window` | поиск окна по pid / подстроке заголовка / классу (комбинируется) |
| `get_active_window` | пид и заголовок текущего активного окна |
| `activate_window` | фокус + поднятие окна с проверкой и ретраями |
| `capture_window` | снимок конкретного окна: поиск -> активация -> кадр -> контроль размера/OCR-маркера -> сохранение |
| `capture_active_window` | снимок текущего активного окна |
| `capture_screen` | снимок всего рабочего стола |
| `ocr_text` | текст изображения целиком (psm 6, rus+eng) |
| `ocr_words` | слова с координатами и уверенностью (psm 11 tsv) |
| `verify_ocr` | OCR-проверка: содержит ли снимок ожидаемую подстроку (assert-сценарии) |

### Ресурсы

| Ресурс | Назначение |
|---|---|
| `screenshot://<имя>` | PNG снимка (image/png) из каталога снимков по имени файла |
| `screenshots://list` | список сохранённых снимков |

Каждый инструмент съёмки возвращает `resource_uri` — ссылку на ресурс,
по которой клиент может прочитать саму картинку.

Инструменты `ocr_text`, `ocr_words` и `verify_ocr` принимают изображение
двумя способами, достаточно одного:

| Аргумент | Значение |
|---|---|
| `screenshot_uri` | значение поля `resource_uri` из результата `capture_*`, передаётся без преобразований |
| `image_path` | путь к файлу; допускается и ресурс `screenshot://<имя>` |

Если заданы оба с разными значениями, инструмент вернёт ошибку; с одинаковым
значением лишнего эффекта нет.

## Конфигурация

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `WAYLAND_SHOT_OUTPUT` | `~/Pictures/wayland-shot` | каталог сохранения снимков |
| `WSHOT_TMP` | `/tmp/opencode/wayland-shot` | временные JS-скрипты и промежуточные кадры |

## Как это работает

KWin не отдаёт список окон по D-Bus напрямую. Нативный способ — скриптинг
оконного менеджера через интерфейс `org.kde.kwin.Scripting`:

```
qdbus org.kde.KWin /Scripting org.kde.kwin.Scripting.loadScript <путь> <имя>
qdbus org.kde.KWin /Scripting org.kde.kwin.Scripting.start
qdbus org.kde.KWin /Scripting org.kde.kwin.Scripting.unloadScript <имя>
```

`loadScript`/`start` ничего не возвращают, поэтому JS-скрипт печатает
результат через `print()`, который KWin пишет в собственный журнал
(`journalctl --user ... _PID=<kwin_wayland>`), а Python опрашивает журнал
с таймаутом и собирает строки с маркером `KJS_RESULT <json>`. Маркер
уникален на каждый запуск (`KJS_RESULT_<pid>_<seq>`), что исключает подхват
«чужих» строк соседних вызовов. Код этой механики — в `kwinlib.py` (порт
из наработки без изменения логики).

**Важное ограничение KWin 6 (движок QJSEngine):** в JS-скриптах нет API
записи файлов (нет глобальных `writeFile`/`process`/`Qt`), поэтому
результат материализуется в файл на стороне Python. По этой же причине в
JS-шаблоны подставляются только числа и ASCII-ключи: заголовки и
пользовательский текст (в том числе кириллица) обрабатываются в Python и
в JS никогда не попадают.

В KWin 6 в объекте окна нет методов `requestActivate()`/`activate()`;
работают присваивание `workspace.activeWindow = окно` и
`workspace.raiseWindow(окно)`. В `kwinlib` реализована цепочка из всех
четырёх способов, поэтому код совместим и с KWin 5.

Съёмка: `spectacle -b -n -a|-f -o FILE` (окно/экран), размер проверяется
через `identify`. OCR: `tesseract ... --psm 6` (текст) и `--psm 11 tsv`
(слова с координатами).

### Контроль размера кадра в capture_window

Spectacle снимает окно в физических пикселях и включает рамку и тень, а
`windowList()` отдаёт логические координаты (могут быть дробными и не
совпадать с пикселями из-за масштабирования дисплея и рамок). Поэтому:

- `size_check="calibrate"` (по умолчанию): эталон размера берётся из
  первого кадра после успешной активации и сверяется на последующих
  попытках — это ловит ситуации «сняли не то окно / весь экран»;
- `size_check="fixed"`: ожидаемый размер задаётся явно через
  `expect_w`/`expect_h` в физических пикселях;
- `size_check="off"`: контроль размера отключён.

## Примеры использования агентом

1. Посмотреть, какие окна открыты: `list_windows`.
2. Найти окно браузера: `find_window(title="Firefox")`.
3. Снять его с проверкой, что в кадре нужный контент:
   `capture_window(title="Firefox", marker="поиск", size_check="calibrate")`.
4. Прочитать картинку: ресурс `screenshot://firefox_...png`.
5. Извлечь из снимка текст: `ocr_text(screenshot_uri=resource_uri)` или слова
   с координатами `ocr_words(screenshot_uri=resource_uri)`, где `resource_uri`
   взят из результата шага 3.
6. Проверить, что в кадре нужный контент: `verify_ocr(screenshot_uri=...,
   expected="поиск")` — вернёт `found`, число вхождений и фрагмент текста
   вокруг совпадения.
7. Снять весь экран: `capture_screen()`.

## Smoke-тест

Скрипт проверяет библиотеку в живой сессии:

```bash
/home/lute/.local/bin/MCP/PytnonVenv/bin/python \
  /home/lute/.local/bin/MCP/PytnonVenv/wayland-shot-mcp-server/scripts/selftest.py --capture
```

Без `--capture` выполняется только чтение (список окон, поиск, активное окно).

## Ограничения

- Только KDE Plasma 6 (Wayland): используется D-Bus-интерфейс
  `org.kde.kwin.Scripting`; под GNOME/wlroots не работает.
- Нужен доступ к журналу `kwin_wayland` (journalctl): при редких настройках
  журналирования инструменты не смогут работать.
- `start()` запускает все загруженные KWin-скрипты сессии — после каждого
  вызова наш скрипт сразу разгружается (`unloadScript`), но кратковременный
  запуск пользовательских скриптов возможен. Сервер рассчитан на автономную
  среду.
- pid у нативных Wayland-окон доступен через `windowList()`, но для
  служебных окон (панели, виджеты) может быть -1 — такие окна ищутся по
  заголовку/классу.
- Ресурс `screenshot://<имя>` отдаёт только файлы из `WAYLAND_SHOT_OUTPUT`
  и только по имени без разделителей путей (защита от чтения произвольных
  файлов).