# -*- coding: utf-8 -*-
"""
Hox Launcher v3.5 — проверка целостности + установка строго по кнопке.

Схема «лаунчер отдельно — плеер отдельно» (v0.4.0+, поведение v3.1):
  * установщик Hox-Setup-*.exe ставит ТОЛЬКО лаунчер (runtime Python +
    Hox-Launcher.pyw) и ярлык «Hox» на этот лаунчер;
  * при каждом запуске лаунчер проверяет локальные файлы плеера
    (целостность — SHA-256 по манифесту установки) и последнюю версию
    на GitHub Releases;
  * если файлы отсутствуют/повреждены или вышла новая версия — лаунчер
    пишет «Требуется обновление» и ЖДЁТ: сам он ничего не скачивает,
    установка начинается только по нажатию кнопки
    «Обновить / Переустановить»;
  * по кнопке качается архив плеера Hox-Player-<версия>.zip с GitHub
    Releases (проверка SHA-256 до распаковки) и распаковывается
    в папку app;
  * источники архива плеера (по приоритету):
      1) GitHub Releases (репозиторий: константа GITHUB_REPO ниже или
         [updates] github_repo в launcher.ini);
      2) старый JSON-манифест ([updates] manifest_url в launcher.ini);
      3) Hox-Player-*.zip рядом с лаунчером (автономная установка);
      4) полный установщик Hox-Setup.exe рядом с лаунчером;
      5) zip-пейлоад рядом с лаунчером / собственная копия файлов;
  * проверка обновлений при запуске; на время проверки кнопка запуска
    плеера заблокирована, а кнопка установки остаётся активной;
  * новости о новых версиях + галерея скриншотов прямо в окне;
  * подпись «Ранняя бета»;
  * запасной TLS-режим: если проверка сертификата не проходит (старые
    корневые сертификаты Windows 7), скачивание повторяется без неё;
  * перед обновлением сам закрывает запущенный плеер;
  * v0.4.6: лаунчер обновляет сам себя с GitHub Releases — если релиз
    новее его сборки (RELEASE_VERSION), кнопка «Обновить лаунчер»
    скачивает установщик Hox-Setup-<версия>.exe (проверка SHA-256),
    запускает его тихо (/S) — установщик заменит файлы и перезапустит
    лаунчер сам; после него обновляется и плеер;
  * пока плеер запущен, кнопки запуска неактивны (опрос player.lock, v0.4.4);
  * v0.4.5: пока плеер запущен, окно лаунчера сворачивается в трей,
    при закрытии плеера — разворачивается обратно; крестик оставляет
    лаунчер работать в значке трея (выход — меню значка); повторный
    запуск лаунчера разворачивает первое окно;
  * никаких консольных окон (CREATE_NO_WINDOW, pythonw.exe).

Совместимость: PyQt6 на Win8.1/10/11 и fake-PyQt6 (PyQt5) на Win7 SP1
(Python 3.8) — синтаксис ограничен 3.8, только scoped-энумы.

Версия: 3.6 (релиз плеера 0.4.6)
"""

import os
import re
import sys
import json
import time
import hashlib
import zipfile
import shutil
import socket
import ssl
import tempfile
import subprocess
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable, Tuple

LAUNCHER_VERSION = "3.6"
# Релиз, которому соответствует эта сборка лаунчера (версии плеера и
# лаунчера выпускаются вместе). Если в GitHub Releases появился релиз
# новее, лаунчер предлагает обновить сам себя: скачивает установщик
# Hox-Setup-<версия>.exe (для Win7-сборки — Hox-Setup-Win7-*.exe)
# и запускает его тихо — тот заменяет файлы и перезапускает лаунчер.
RELEASE_VERSION = "0.4.6"
APP_TITLE = "Hox — Лаунчер"
PLAYER_SCRIPT = "hox_music_player.py"
# Манифест целостности: SHA-256 установленных файлов плеера; пишется при
# установке (run_player_install), проверяется при каждом запуске —
# см. player_integrity().
MANIFEST_NAME = "player_files.json"
# Win7-сборка (payload-win7) патчит эти две константы: маркер включается,
# а локальная установка ищет Hox-Setup-Win7.exe рядом с лаунчером.
IS_WIN7_BUILD = False
SETUP_EXE = "Hox-Setup.exe"

# GitHub-репозиторий релизов по умолчанию ("владелец/репозиторий").
# Пусто = источник не задан; тогда лаунчер работает автономно, а
# репозиторий можно указать в %LOCALAPPDATA%\Hoxmusicp\launcher.ini:
#   [updates]
#   github_repo = owner/hox-music-player
GITHUB_REPO = "Dima-Xox/Hox-Music-Player"
# Плеер запускается пропатченным Hox.exe (копия pythonw.exe с логотипом);
# pythonw.exe оставлен как запасной вариант запуска
PLAYER_EXE_NAMES = ("Hox.exe", "pythonw.exe")

# v0.4.4 (лаунчер 3.4): пока плеер запущен, кнопки запуска неактивны.
# Плеер пишет свой PID в player.lock при старте и убирает файл при выходе;
# лаунчер опрашивает состояние таймером: свой Popen -> player.lock ->
# периодический поиск процессов (PowerShell) — на случай старого плеера,
# который ещё не пишет player.lock.
PLAYER_LOCK_NAME = "player.lock"
PLAYER_POLL_MS = 2000        # период опроса состояния плеера
PS_SWEEP_SEC = 15.0          # период фоновой проверки процессов (PowerShell)

# v0.4.5 (лаунчер 3.5): поведение окна и трея.
# Пока плеер запущен, окно лаунчера сворачивается в системный трей,
# при закрытии плеера — разворачивается обратно. Закрытие окна (крестик)
# не завершает лаунчер: он продолжает работать в значке трея
# (настоящий выход — пункт «Выход» в меню значка). Повторный запуск
# лаунчера не создаёт вторую копию — разворачивает первое окно.
SINGLE_PORTS = (46121, 46122, 46123)  # TCP на 127.0.0.1: «уже запущен»

# v0.4.6 (лаунчер 3.6): файл лаунчера в payload — по нему установщик
# подтверждает успешное самообновление лаунчера.
LAUNCHER_FILE = "Hox-Launcher.pyw"
# Однострочник: PID процессов плеера (в cmdline есть hox_music_player).
# Get-CimInstance — Win8.1/10/11; на Win7 (PS 2.0, без CIM) — wmic.
PS_PLAYER_PIDS_CMD = (
    "$ErrorActionPreference='SilentlyContinue';"
    "$out=@();"
    "$cim=$false;"
    "try{$cim=[bool](Get-Command Get-CimInstance -ErrorAction Stop)}catch{};"
    "if($cim){"
    "$out=@(Get-CimInstance Win32_Process|"
    "Where-Object{$_.Name -imatch '^(python|hox)' "
    "-and $_.CommandLine -imatch 'hox_music_player'}|"
    "ForEach-Object{[string]$_.ProcessId})"
    "}else{"
    "$out=@(wmic process get ProcessId,CommandLine /format:csv|"
    "ForEach-Object{if($_ -imatch 'hox_music_player' "
    "-and $_ -imatch ',(\\d+)\\s*$'){$Matches[1]}})"
    "};"
    "$out"
)

# Ключ деинсталляции — общий с Hox-Setup.nsi (INSTKEY)
UNINST_KEY = "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\Hoxmusicp"
REG_HOME = "Software\\Hoxmusicp"

# Оценка числа файлов в пейлоаде — для грубого прогресса тихой установки
EST_FILES = 1600

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) HoxLauncher/" + LAUNCHER_VERSION

# ------------------------------------------------------------------
# Тема (в духе aurora плеера; лаунчер самодостаточен, свои QSS-переменные)
# ------------------------------------------------------------------
BG = "#10131f"
BG2 = "#1a1f35"
BG3 = "#232a4d"
ACCENT = "#00e5ff"
ACCENT2 = "#3d5afe"
TEXT = "#e8ecf5"
MUTED = "#8a93b2"
BORDER = "#39405e"
OK = "#4ec9b0"
WARN = "#ffb454"

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# ------------------------------------------------------------------
# Встроенные новости о версиях (офлайн-фолбэк; онлайн-заметки релиза
# с GitHub показываются поверх этого списка)
# ------------------------------------------------------------------
NEWS: List[Tuple[str, List[str]]] = [
    ("0.4.6", [
        "Лаунчер научился обновлять сам себя: если на GitHub вышел релиз новее, лаунчер сам предложит «Обновить лаунчер до v…» — скачает установщик, проверит его по SHA-256, запустит тихо и перезапустится; руками что-либо скачивать не нужно.",
        "Обновление лаунчера предлагается раньше обновления плеера: свежий лаунчер гарантирует корректную установку новых версий плеера (старые копии лаунчера, например v0.4.2, сами не умели ставить плеер).",
        "После самообновления лаунчера файлы и настройки плеера сохраняются: установщик заменяет только лаунчер и runtime, а плеер доустанавливается одной кнопкой, если он новее установленного.",
    ]),
    ("0.4.5", [
        "Лаунчер сам уходит в трей, пока играет музыка: как только плеер запущен, окно сворачивается в значок в системном трее, а когда плеер закрыт — лаунчер сам разворачивается обратно.",
        "Крестик больше не выключает лаунчер: он продолжает работать в трее — запуск плеера, проверка обновлений и настоящий выход доступны из меню значка; при первом сворачивании показывается подсказка.",
        "Повторный запуск лаунчера (ярлык «Hox») не открывает вторую копию — разворачивается уже открытое окно, даже если оно свернуто в трей.",
    ]),
    ("0.4.4", [
        "Пока плеер запущен, кнопки «Запустить плеер» и «Открыть плеер» неактивны — случайный повторный запуск исключён; как только плеер закрыт, кнопки снова активны — лаунчер замечает это сам, без перезапуска.",
        "Лаунчер видит плеера, запущенного откуда угодно: плеер при старте пишет свой PID в файл player.lock в папке установки и убирает его при выходе; для старых установок, где плеер ещё не пишет файл, работает резервная проверка процессов.",
        "Если плеер был принудительно закрыт или упал, устаревший player.lock распознаётся по мёртвому PID и убирается автоматически — ложной блокировки кнопки не будет.",
    ]),
    ("0.4.3", [
        "Исправлена ошибка v0.4.2: установка не запускалась после успешного скачивания архива плеера — лаунчер оставался в режиме «занят» с неактивными кнопками, а журнал обрывался на «Скачано».",
        "Теперь цепочка работает целиком: «Обновить / Переустановить» -> скачивание Hox-Player-*.zip -> проверка SHA-256 -> распаковка в app -> манифест целостности -> «Установка завершена».",
        "Сам плеер без функциональных изменений: караоке, синхронизатор текста, темы и все возможности предыдущих версий полностью на месте.",
    ]),
    ("0.4.2", [
        "Лаунчер сжался под маленькие экраны: окно автоматически подстраивается под разрешение (проверено на 1366x768) — панель установки, кнопка «Обновить» и прогресс-бар всегда видны целиком, ничего не уезжает за край экрана.",
        "На компактных экранах уменьшаются только гибкие блоки (новости, журнал, скриншоты); на обычных разрешениях окно выглядит как раньше.",
        "На совсем маленьких экранах включается внешняя прокрутка — интерфейс остаётся доступным на любом разрешении.",
        "Сам плеер без функциональных изменений: караоке, синхронизатор текста, темы и все возможности предыдущих версий полностью на месте.",
    ]),
    ("0.4.1", [
        "Лаунчер больше не скачивает плеер сам при запуске: при первом старте он проверяет наличие и целостность файлов и пишет «Требуется обновление» — скачивание и установка начинаются строго по нажатию кнопки «Обновить / Переустановить».",
        "Проверка целостности при каждом запуске: SHA-256 каждого файла плеера сверяется с манифестом установки (player_files.json); отсутствующие или повреждённые файлы — снова «Требуется обновление», установка чинит их в один клик.",
        "Кнопка «Обновить / Переустановить» всегда активна: один клик — скачивание лёгкого архива плеера Hox-Player-*.zip с GitHub Releases с прогресс-баром, проверкой SHA-256 и распаковкой в папку app.",
        "Запасные режимы: без интернета кнопка поставит плеера из архива Hox-Player-*.zip рядом с лаунчером; на системах со старыми корневыми сертификатами (Windows 7) скачивание повторяется в запасном TLS-режиме.",
    ]),
    ("0.4.0", [
        "Новая схема «лаунчер отдельно — плеер отдельно»: установщик ставит только лаунчер, а плеер лаунчер скачивает с GitHub сам — отдельным лёгким архивом Hox-Player-<версия>.zip.",
        "Если плеер ещё не установлен, лаунчер при запуске автоматически скачает и установит его, после чего плеер запускается одной кнопкой.",
        "Обновления плеера: проверка при каждом запуске и по кнопке «Проверить обновления»; качается только архив плеера (сотни килобайт вместо полного установщика), каждый файл проверяется по SHA-256 до распаковки.",
    ]),
    ("0.3.9", [
        "Репозиторий обновлений вшит в лаунчер: автообновление работает сразу после установки — launcher.ini настраивать не нужно.",
        "Официальный источник обновлений: Dima-Xox/Hox-Music-Player (GitHub Releases); версия, заметки о релизе и установщики берутся оттуда.",
        "Свой источник по-прежнему можно задать в launcher.ini ([updates] github_repo) — он перекрывает вшитый адрес.",
    ]),
    ("0.3.8", [
        "Лаунчер обновлён до версии 2.0: установка плеера происходит прямо в окне лаунчера — с прогресс-баром, выбором папки и созданием ярлыков на рабочем столе и в меню «Пуск».",
        "Проверка обновлений выполняется при запуске лаунчера (на время проверки кнопка запуска плеера заблокирована) и по кнопке «Проверить обновления» в любой момент.",
        "Обновления берутся с GitHub Releases — всегда актуальная версия (репозиторий указывается в launcher.ini, см. README); скачивание идёт с прогресс-баром и проверкой SHA-256.",
        "В лаунчер встроены новости о новых версиях и галерея скриншотов; рядом с прогресс-баром появилась кнопка «Открыть плеер».",
        "Лаунчер работает автономно: без интернета он установит плеер из установщика Hox-Setup.exe, лежащего рядом, или из собственной копии файлов.",
        "При обновлении поверх существующей установки настройки, темы и плейлисты полностью сохраняются; кнопка «Переустановить / восстановить» чинит установленного плеера в один клик.",
        "Каждый файл проверяется по контрольной сумме SHA-256 до распаковки — повреждённая или неполная сборка просто не установится.",
        "Деинсталлятор в «Приложения и компоненты» Windows аккуратно убирает плеер, лаунчер и ярлыки; автономный установщик Hox-Setup.exe по-прежнему поставляется отдельно — на случай полного отсутствия интернета.",
    ]),
    ("0.3.7", [
        "Новый раздел «Инструменты → Синхронизатор текста»: отдельное окно создаёт синхронизированный текст песни — по готовому тексту или автопознаванием речи (Whisper) с пословными метками времени.",
        "Запись в теги: MP3/WAV — SYLT + TXXX:LYRICS_LRC, FLAC — LYRICS/LYRICS_LINE; плеер читает эти теги, поэтому обработанный трек сразу играет с караоке-подсветкой.",
        "Обработка идёт в фоне и не блокирует плеер; лог, drag&drop и автоподстановка текущего трека.",
    ]),
    ("0.3.6", [
        "Вкладка «Текст песни» убрана — весь текст живёт в караоке-блоке на панели управления, рядом с обложкой и кнопками.",
        "Активная строка подсвечивается сразу и заливается пословно; строки плавно скользят при смене куплета; клик — перемотка, колесо — прокрутка с автовозвратом.",
        "Правый клик по караоке: загрузить .lrc/.txt, сдвиг синхронизации ±0.5 с, сброс.",
    ]),
    ("0.3.5", [
        "Текст песни в стиле стриминговых сервисов: крупные строки, активная — цветом темы, плавная автопрокрутка и пословная заливка.",
        "Таймер песни: время, ползунок и длительность обновляются ровным ходом таймера 100 мс.",
        "Честный режим для текста без таймингов — без ложной подсветки.",
    ]),
    ("0.3.4", [
        "Визуализатор-эквалайзер стал крупнее и толще; тотальная векторизация иконок (18 рисунков в цвете темы).",
        "Умный караоке-таймер по тишине: «Играет проигрыш — вокал через N с»; сдвиг синхронизации ±0.5 с.",
        "Анимированный сплэш-экран и оптимизация процессора.",
    ]),
]

# Скриншоты для галереи (ищутся в launcher_shots/ рядом с лаунчером
# или внутри установленного плеера). Имена без версии — не меняются
# от релиза к релизу.
SHOTS: List[Tuple[str, str]] = [
    ("shot-main-aurora.png", "Главное окно — тема Aurora"),
    ("shot-sync-aurora.png", "Синхронизатор текста"),
    ("shot-mini.png", "Мини-плеер"),
    ("shot-splash.png", "Сплэш-экран"),
]


# ------------------------------------------------------------------
# Процессы плеера (закрытие перед обновлением) — из v1.7, проверено
# ------------------------------------------------------------------
def _player_pids() -> list:
    """PID процессов плеера (в командной строке есть hox_music_player).

    PowerShell CIM — на Win8.1/10/11; на Win7 (PS 2.0, без Get-CimInstance)
    — wmic. Фильтр по hox_music_player исключает процессы самого лаунчера
    (Hox.exe с Hox-Launcher.pyw) — их нельзя ни закрывать, ни считать плеером.
    """
    if os.name != "nt":
        return []
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-Command", PS_PLAYER_PIDS_CMD],
            capture_output=True, text=True, timeout=30,
            creationflags=CREATE_NO_WINDOW, check=False)
        return _parse_pids_text(r.stdout)
    except Exception:
        return []


def _pids_alive(pids: list) -> list:
    """Какие из PID ещё живы (через OpenProcess, без сторонних утилит)."""
    if not pids:
        return []
    if os.name != "nt":
        # POSIX (тестовый контур): os.kill(pid, 0) только проверяет живость
        alive = []
        for pid in pids:
            pid = int(pid)
            if pid <= 0:
                continue
            try:
                os.kill(pid, 0)
                alive.append(pid)
            except ProcessLookupError:
                pass
            except PermissionError:
                alive.append(pid)
            except OSError:
                pass
        return alive
    try:
        import ctypes
        alive = []
        for pid in pids:
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
            if handle:  # PROCESS_QUERY_LIMITED_INFORMATION
                ctypes.windll.kernel32.CloseHandle(handle)
                alive.append(pid)
        return alive
    except Exception:
        return list(pids)  # не смогли проверить — считаем живыми


def _parse_pids_text(text: str) -> List[int]:
    """Целые PID из вывода PowerShell/wmic (по одному в строке/слове)."""
    out: List[int] = []
    for tok in (text or "").split():
        t = tok.strip()
        if t.isdigit():
            v = int(t)
            if 0 < v <= 4194304 and v not in out:  # максимум PID в Windows
                out.append(v)
    return out


def lock_candidates() -> List[Path]:
    """Где лаунчер ищет player.lock: рядом с установленным плеером и рядом
    с самим лаунчером (в установленной копии это одна и та же папка app)."""
    out: List[Path] = []
    ps = find_player_script()
    if ps is not None:
        out.append(ps.parent / PLAYER_LOCK_NAME)
    here = Path(__file__).resolve().parent / PLAYER_LOCK_NAME
    if here not in out:
        out.append(here)
    return out


def close_player_processes(timeout: float = 12.0) -> bool:
    """Завершить плеер перед обновлением файлов (мягко, затем принудительно)."""
    if os.name != "nt":
        return True
    try:
        pids = [p for p in _player_pids() if p != os.getpid()]
        if not pids:
            return True
        args = ["taskkill"]
        for pid in pids:
            args += ["/PID", str(pid)]
        subprocess.run(args, capture_output=True, timeout=15,
                       creationflags=CREATE_NO_WINDOW, check=False)
        deadline = time.monotonic() + timeout / 2
        while time.monotonic() < deadline:
            if not _pids_alive(pids):
                return True
            time.sleep(0.3)
        subprocess.run(args + ["/F"], capture_output=True, timeout=15,
                       creationflags=CREATE_NO_WINDOW, check=False)
        deadline = time.monotonic() + timeout / 2
        while time.monotonic() < deadline:
            if not _pids_alive(pids):
                return True
            time.sleep(0.3)
        return not _pids_alive(pids)
    except Exception:
        return False


# ------------------------------------------------------------------
# Пути и версии
# ------------------------------------------------------------------
def local_appdata() -> Path:
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    else:
        local = str(Path.home() / ".local" / "share")
    return Path(local)


def app_base() -> Path:
    """Каталог установки по умолчанию: %LOCALAPPDATA%\\Hoxmusicp."""
    base = local_appdata() / "Hoxmusicp"
    base.mkdir(parents=True, exist_ok=True)
    return base


def own_base() -> Path:
    """Каталог, где живёт сам лаунчер: <база>\\app\\Hox-Launcher.pyw.
    Плеер по умолчанию ставится в эту же папку (туда же, где runtime)."""
    return Path(__file__).resolve().parent.parent


def runtime_bases() -> List[Path]:
    """Каталоги-кандидаты, где может лежать runtime (Python) установки."""
    out: List[Path] = []
    try:
        out.append(own_base())
    except Exception:
        pass
    local = local_appdata()
    out.append(local / "Hoxmusicp")
    out.append(local / "Hox")  # старая установка
    uniq: List[Path] = []
    for c in out:
        if c not in uniq:
            uniq.append(c)
    return uniq


def find_install_base() -> Optional[Path]:
    """Каталог, где реально установлен ПЛЕЕР (есть app/hox_music_player.py).
    Лаунчер при этом может быть уже установлен, а плеер — ещё нет."""
    for c in runtime_bases():
        try:
            if (c / "app" / PLAYER_SCRIPT).is_file() and \
               ((c / "runtime" / "python").is_dir() or (c / "runtime").is_dir()):
                return c
        except Exception:
            continue
    return None


def find_runtime() -> Optional[Path]:
    """Hox.exe/pythonw.exe — независимо от того, скачан ли уже плеер."""
    for base in runtime_bases():
        for exe in PLAYER_EXE_NAMES:
            p = base / "runtime" / "python" / exe
            if p.is_file():
                return p
    return None


def find_player_script() -> Optional[Path]:
    base = find_install_base()
    if base:
        p = base / "app" / PLAYER_SCRIPT
        if p.is_file():
            return p
    here = Path(__file__).resolve().parent
    p = here / PLAYER_SCRIPT
    return p if p.is_file() else None


def read_player_version() -> str:
    ps = find_player_script()
    if not ps:
        return ""
    try:
        head = ps.read_text(encoding="utf-8", errors="ignore")[:4000]
        m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', head)
        return m.group(1) if m else ""
    except Exception:
        return ""


def _read_version_of(path) -> str:
    """APP_VERSION из конкретного файла (не из найденной установки)."""
    try:
        head = Path(path).read_text(encoding="utf-8", errors="ignore")[:4000]
        m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', head)
        return m.group(1) if m else ""
    except Exception:
        return ""


def player_integrity() -> Tuple[bool, str]:
    """Целостность установленных файлов плеера: (ok, деталь).

    ok=False -> файлов нет или они повреждены; лаунчер показывает
    «Требуется обновление», а установка по кнопке чинит это в один клик.
    """
    base = find_install_base()
    if base is None:
        return False, "плеер не установлен"
    app_dir = Path(base) / "app"
    if not (app_dir / PLAYER_SCRIPT).is_file():
        return False, "не найден " + PLAYER_SCRIPT
    mf = app_dir / MANIFEST_NAME
    if not mf.is_file():
        # установка от старого установщика (без манифеста) — наличие
        # главного файла уже проверено выше
        return True, ""
    try:
        data = json.loads(mf.read_text(encoding="utf-8"))
    except Exception:
        return False, "манифест установки не читается"
    for name, hexdigest in (data.get("files") or {}).items():
        f = app_dir / str(name)
        if not f.is_file():
            return False, "отсутствует файл " + str(name)
        try:
            h = hashlib.sha256(f.read_bytes()).hexdigest()
        except Exception:
            return False, "файл не читается: " + str(name)
        if str(hexdigest).lower() != h:
            return False, "не совпал SHA-256: " + str(name)
    return True, ""


def parse_version(v: str) -> tuple:
    """'0.3.10' -> (0, 3, 10); нечисловые части отбрасываются."""
    parts = []
    for chunk in re.findall(r"\d+", str(v))[:4]:
        parts.append(int(chunk))
    return tuple(parts) if parts else (0,)


def expand_dir(text: str) -> Path:
    """Текст из комбобокса -> Path (с раскрытием %LOCALAPPDATA% и ~)."""
    t = str(text).strip().strip('"')
    t = t.split("  —  ")[0].strip()  # убираем подпись пресета
    t = os.path.expandvars(os.path.expanduser(t))
    return Path(t)


# ------------------------------------------------------------------
# Поиск локальных источников установки (автономный режим)
# ------------------------------------------------------------------
def launcher_dirs() -> List[Path]:
    """Каталоги, где лаунчер ищет установщик/пейлоад рядом с собой."""
    here = Path(__file__).resolve().parent
    out = [here, here.parent, Path.cwd()]
    uniq: List[Path] = []
    for d in out:
        if d not in uniq:
            uniq.append(d)
    return uniq


def find_local_setup() -> Optional[Path]:
    """Hox-Setup[-Win7].exe рядом с лаунчером (offline-установка)."""
    names = [SETUP_EXE]
    alt = "Hox-Setup-Win7.exe" if not IS_WIN7_BUILD else "Hox-Setup.exe"
    names.append(alt)
    for d in launcher_dirs():
        for n in names:
            p = d / n
            if p.is_file():
                return p
    return None


def find_local_payload_zip() -> Optional[Path]:
    """Hox-Payload-*.zip рядом с лаунчером (offline-установка)."""
    best: Optional[Path] = None
    best_ver = (0,)
    for d in launcher_dirs():
        for p in d.glob("Hox-Payload-*.zip"):
            m = re.search(r"(\d+(?:\.\d+)+)", p.name)
            ver = parse_version(m.group(1)) if m else (0,)
            if ver > best_ver:
                best, best_ver = p, ver
    return best


def find_local_player_zip() -> Optional[Path]:
    """Hox-Player-*.zip рядом с лаунчером (автономная установка плеера)."""
    best: Optional[Path] = None
    best_ver = (0,)
    for d in launcher_dirs():
        for p in d.glob("Hox-Player-*.zip"):
            m = re.search(r"(\d+(?:\.\d+)+)", p.name)
            ver = parse_version(m.group(1)) if m else (0,)
            if ver > best_ver:
                best, best_ver = p, ver
    return best


def shots_dirs() -> List[Path]:
    """Где искать скриншоты для галереи."""
    here = Path(__file__).resolve().parent
    return [here / "launcher_shots", here.parent / "app" / "launcher_shots",
            here / "shots", here / "media"]


def find_shot(name: str) -> Optional[Path]:
    for d in shots_dirs():
        p = d / name
        if p.is_file():
            return p
    return None


def open_url(url, timeout: float = 30.0):
    """urlopen с запасным TLS-режимом.

    На старых системах (Windows 7 без обновлений корневых сертификатов)
    проверка сертификата GitHub может не проходить — тогда запрос
    повторяется с отключённой проверкой, чтобы обновление скачивалось
    всё равно.
    """
    try:
        return urllib.request.urlopen(url, timeout=timeout)
    except urllib.error.URLError as e:
        msg = str(getattr(e, "reason", e)).upper()
        if "SSL" in msg or "CERTIFICATE" in msg:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return urllib.request.urlopen(url, timeout=timeout, context=ctx)
        raise


# ------------------------------------------------------------------
# GitHub Releases (архив плеера берётся с GitHub — актуальная версия)
# ------------------------------------------------------------------
def github_latest(repo: str) -> Optional[Dict[str, Any]]:
    """Последний релиз: {version, notes, asset_url, asset_name, sha_url}."""
    url = "https://api.github.com/repos/" + repo.strip() + "/releases/latest"
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/vnd.github+json"})
    with open_url(req, 15) as r:
        data = json.loads(r.read().decode("utf-8"))
    tag = str(data.get("tag_name", "") or "")
    version = tag.lstrip("vV")
    if not version:
        name = str(data.get("name", "") or "")
        m = re.search(r"(\d+(?:\.\d+)+)", name)
        version = m.group(1) if m else ""
    if not version:
        raise ValueError("в релизе нет версии (tag_name)")

    # v3.0: лаунчер качает только архив плеера Hox-Player-<версия>.zip —
    # он один и тот же для main- и Win7-сборки (чистый исходник Python).
    assets = data.get("assets") or []
    player_re = re.compile(r"^hox-player-(\d+(?:\.\d+)*)\.zip$")
    picked, picked_ver = None, (0,)
    for a in assets:
        n = str(a.get("name", ""))
        m = player_re.match(n.lower())
        if not m:
            continue
        ver = tuple(int(x) for x in m.group(1).split("."))
        if ver > picked_ver:
            picked, picked_ver = a, ver
    if picked is None:
        raise ValueError("в релизе нет архива плеера Hox-Player-*.zip")
    asset_url = str(picked.get("browser_download_url", ""))
    asset_name = str(picked.get("name", ""))
    sha_url = ""
    for a in assets:
        if str(a.get("name", "")) == asset_name + ".sha256":
            sha_url = str(a.get("browser_download_url", ""))
            break

    # v3.6: дополнительно выбираем установщик лаунчера — по нему лаунчер
    # обновляет сам себя. Для основной сборки это Hox-Setup-<ver>.exe,
    # для Win7-сборки — Hox-Setup-Win7-<ver>.exe (runtime другой).
    setup_re = re.compile(r"^hox-setup-(?:win7-)?(\d+(?:\.\d+)*)\.exe$")
    setup, setup_ver = None, (0,)
    for a in assets:
        n = str(a.get("name", ""))
        m = setup_re.match(n.lower())
        if not m:
            continue
        if ("win7-" in n.lower()) != bool(IS_WIN7_BUILD):
            continue  # чужая сборка
        ver = tuple(int(x) for x in m.group(1).split("."))
        if ver > setup_ver:
            setup, setup_ver = a, ver
    setup_url = str(setup.get("browser_download_url", "")) if setup else ""
    setup_name = str(setup.get("name", "")) if setup else ""
    setup_sha_url = ""
    if setup_name:
        for a in assets:
            if str(a.get("name", "")) == setup_name + ".sha256":
                setup_sha_url = str(a.get("browser_download_url", ""))
                break
    notes = str(data.get("body", "") or "")
    published = str(data.get("published_at", "") or "")
    return {"version": version, "notes": notes, "asset_url": asset_url,
            "asset_name": asset_name, "sha_url": sha_url, "published": published,
            "html_url": str(data.get("html_url", "") or ""),
            "setup_url": setup_url, "setup_name": setup_name,
            "setup_sha_url": setup_sha_url}


def fetch_sha256(url: str) -> str:
    """Скачивает *.sha256 и возвращает hex-дайджест из содержимого."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with open_url(req, 15) as r:
        text = r.read(4096).decode("utf-8", errors="ignore")
    m = re.search(r"\b([0-9a-fA-F]{64})\b", text)
    return m.group(1).lower() if m else ""


def launcher_update_available(remote_version: str) -> bool:
    """True, если в релизе появился лаунчер новее текущей сборки.

    Сравнивается версия релиза с RELEASE_VERSION — версией, которой
    соответствует эта сборка лаунчера (плеер и лаунчер выпускаются
    вместе, поэтому версия релиза == версия сборки лаунчера).
    """
    try:
        return bool(remote_version) and \
            parse_version(remote_version) > parse_version(RELEASE_VERSION)
    except Exception:  # noqa: BLE001
        return False


def resolve_update_source(settings) -> Dict[str, Any]:
    """Определяет источник обновлений. Приоритет: GitHub -> манифест.

    Возвращает словарь-описание; при отсутствии источников — kind='none'.
    Чистая функция сетевого I/O — вызывается из CheckWorker.
    """
    repo = str(settings.value("updates/github_repo", "") or "").strip() or GITHUB_REPO.strip()
    murl = str(settings.value("updates/manifest_url", "") or "").strip()
    result: Dict[str, Any] = {"kind": "none", "version": "", "notes": "",
                              "url": "", "sha256": "", "source": ""}
    if repo and "/" in repo:
        try:
            gh = github_latest(repo)
            sha = ""
            if gh.get("sha_url"):
                try:
                    sha = fetch_sha256(str(gh["sha_url"]))
                except Exception:  # noqa: BLE001
                    sha = ""
            # v3.6: SHA-256 установщика лаунчера + признак «есть обновление
            # самого лаунчера» (релиз новее RELEASE_VERSION этой сборки)
            setup_sha = ""
            if gh.get("setup_sha_url"):
                try:
                    setup_sha = fetch_sha256(str(gh["setup_sha_url"]))
                except Exception:  # noqa: BLE001
                    setup_sha = ""
            result.update({"kind": "github", "version": gh["version"],
                           "notes": gh["notes"], "url": gh["asset_url"],
                           "asset_name": gh["asset_name"], "sha256": sha,
                           "published": gh.get("published", ""),
                           "html_url": gh.get("html_url", ""),
                           "setup_url": str(gh.get("setup_url", "") or ""),
                           "setup_name": str(gh.get("setup_name", "") or ""),
                           "setup_sha256": setup_sha,
                           "launcher_update": launcher_update_available(
                               str(gh["version"])),
                           "source": "GitHub: " + repo})
            return result
        except Exception:  # noqa: BLE001
            pass  # падаем на манифест
    if murl:
        try:
            req = urllib.request.Request(murl, headers={"User-Agent": UA})
            with open_url(req, 20) as r:
                data = json.loads(r.read().decode("utf-8"))
            if not isinstance(data, dict) or not data.get("url") or not data.get("version"):
                raise ValueError("манифест без version/url")
            url = str(data.get("url", ""))
            if IS_WIN7_BUILD:
                url = str(data.get("url_win7", "") or url)
            result.update({"kind": "manifest", "version": str(data.get("version", "")),
                           "notes": str(data.get("notes", "") or ""), "url": url,
                           "sha256": str(data.get("sha256", "") or ""),
                           "setup_url": "", "setup_name": "", "setup_sha256": "",
                           "launcher_update": False,
                           "source": "манифест"})
            return result
        except Exception:
            pass
    return result


# ------------------------------------------------------------------
# Ярлыки и деинсталлятор (для установки, выполненной самим лаунчером)
# ------------------------------------------------------------------
PS_SHORTCUTS = r"""$ErrorActionPreference = 'SilentlyContinue'
$ws = New-Object -ComObject WScript.Shell
$desktop = $ws.SpecialFolders('Desktop')
$smprog = $ws.SpecialFolders('StartMenu') + '\Programs'
$base = '{base}'
$rt = $base + '\runtime\python'
$app = $base + '\app'
function Mk($lnk, $tgt, $arg, $dir, $ico) {{
  $s = $ws.CreateShortcut($lnk)
  $s.TargetPath = $tgt
  if ($arg) {{ $s.Arguments = $arg }}
  $s.WorkingDirectory = $dir
  $s.IconLocation = $ico + ',0'
  $s.Save()
}}
New-Item -ItemType Directory -Force -Path ($smprog + '\Hox') | Out-Null
Mk ($desktop + '\Hox.lnk') ($rt + '\Hox.exe') ('"' + $app + '\Hox-Launcher.pyw"') $app ($app + '\hox.ico')
Mk ($smprog + '\Hox\Hox Music Player.lnk') ($rt + '\Hox.exe') ('"' + $app + '\hox_music_player.py"') $app ($app + '\hox.ico')
Mk ($smprog + '\Hox\Hox Launcher.lnk') ($rt + '\pythonw.exe') ('"' + $app + '\Hox-Launcher.pyw"') $app ($app + '\hox.ico')
"""


def make_shortcuts(base: Path, desktop: bool, startmenu: bool, log: Callable[[str], None]) -> None:
    """Ярлыки плеера: рабочий стол и/или меню «Пуск» (WScript.Shell)."""
    if os.name != "nt":
        log("Ярлыки: пропущены (не Windows)")
        return
    ps = PS_SHORTCUTS.format(base=str(base))
    try:
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-Command", ps],
                       capture_output=True, text=True, timeout=60,
                       creationflags=CREATE_NO_WINDOW, check=False)
        if not desktop:
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            "Remove-Item -LiteralPath ((New-Object -ComObject "
                            "WScript.Shell).SpecialFolders('Desktop') + '\\Hox.lnk') "
                            "-Force -ErrorAction SilentlyContinue"],
                           capture_output=True, timeout=30,
                           creationflags=CREATE_NO_WINDOW, check=False)
        if not startmenu:
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            "Remove-Item -LiteralPath ((New-Object -ComObject "
                            "WScript.Shell).SpecialFolders('StartMenu') + '\\Programs\\Hox') "
                            "-Recurse -Force"],
                           capture_output=True, timeout=30,
                           creationflags=CREATE_NO_WINDOW, check=False)
        log("Ярлыки: рабочий стол — " + ("создан" if desktop else "не создавать")
            + "; меню «Пуск» — " + ("создан" if startmenu else "не создавать"))
    except Exception as e:  # noqa: BLE001
        log("Ярлыки: не удалось создать (" + str(e) + ")")


UNINSTALL_VBS = """' Деинсталлятор Hox Music Player (создан лаунчером v{lv})
Option Explicit
Dim sh, fso, desk, sms
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
On Error Resume Next
sh.Run "taskkill /F /IM Hox.exe /T", 0, True
Set desk = fso.GetFolder(sh.SpecialFolders("Desktop"))
If fso.FileExists(desk & "\\Hox.lnk") Then fso.DeleteFile desk & "\\Hox.lnk", True
Set sms = fso.GetFolder(sh.SpecialFolders("StartMenu") & "\\Programs\\Hox")
If fso.FolderExists(sms) Then fso.DeleteFolder sms, True
sh.Run "reg delete ""HKCU\\{uk}"" /f", 0, True
sh.Run "reg delete ""HKCU\\{rh}"" /f", 0, True
MsgBox "Hox Music Player удалён.", vbInformation, "Hox"
q = Chr(34)
del = "cmd /c ping -n 2 127.0.0.1 >nul & rmdir /s /q " & q & "{base}" & q
sh.Run del, 0, False
"""


def register_uninstaller(base: Path, log: Callable[[str], None]) -> None:
    """Регистрация деинсталлятора для установки, сделанной лаунчером
    (тот же ключ реестра, что и у Hox-Setup.exe)."""
    if os.name != "nt":
        log("Деинсталлятор: пропущен (не Windows)")
        return
    try:
        vbs = base / "Uninstall-Hox.vbs"
        vbs.write_text(UNINSTALL_VBS.format(lv=LAUNCHER_VERSION, uk=UNINST_KEY,
                                            rh=REG_HOME, base=str(base)),
                       encoding="cp1251", errors="replace")
        exe = str(base / "runtime" / "python" / "Hox.exe")
        ico = str(base / "app" / "hox.ico")
        size_kb = 0
        for f in base.rglob("*"):
            try:
                if f.is_file():
                    size_kb += f.stat().st_size // 1024
            except Exception:
                pass
        cmds = [
            ["reg", "add", "HKCU\\" + UNINST_KEY, "/v", "DisplayName",
             "/t", "REG_SZ", "/d", "Hox Music Player", "/f"],
            ["reg", "add", "HKCU\\" + UNINST_KEY, "/v", "DisplayVersion",
             "/t", "REG_SZ", "/d", read_player_version() or "0.0.0", "/f"],
            ["reg", "add", "HKCU\\" + UNINST_KEY, "/v", "Publisher",
             "/t", "REG_SZ", "/d", "Hox", "/f"],
            ["reg", "add", "HKCU\\" + UNINST_KEY, "/v", "DisplayIcon",
             "/t", "REG_SZ", "/d", ico + ",0", "/f"],
            ["reg", "add", "HKCU\\" + UNINST_KEY, "/v", "InstallLocation",
             "/t", "REG_SZ", "/d", str(base), "/f"],
            ["reg", "add", "HKCU\\" + UNINST_KEY, "/v", "UninstallString",
             "/t", "REG_SZ", "/d", "wscript.exe \"" + str(vbs) + "\"", "/f"],
            ["reg", "add", "HKCU\\" + UNINST_KEY, "/v", "QuietUninstallString",
             "/t", "REG_SZ", "/d", "wscript.exe \"" + str(vbs) + "\"", "/f"],
            ["reg", "add", "HKCU\\" + UNINST_KEY, "/v", "NoModify",
             "/t", "REG_DWORD", "/d", "1", "/f"],
            ["reg", "add", "HKCU\\" + UNINST_KEY, "/v", "NoRepair",
             "/t", "REG_DWORD", "/d", "1", "/f"],
            ["reg", "add", "HKCU\\" + UNINST_KEY, "/v", "EstimatedSize",
             "/t", "REG_DWORD", "/d", str(size_kb), "/f"],
            ["reg", "add", "HKCU\\" + REG_HOME, "/v", "InstallDir",
             "/t", "REG_SZ", "/d", str(base), "/f"],
        ]
        for c in cmds:
            subprocess.run(c, capture_output=True, timeout=20,
                           creationflags=CREATE_NO_WINDOW, check=False)
        log("Деинсталлятор зарегистрирован: Uninstall-Hox.vbs + ключ «Установка и удаление программ»")
    except Exception as e:  # noqa: BLE001
        log("Деинсталлятор: не удалось зарегистрировать (" + str(e) + ")")


def count_files(root: Path) -> int:
    n = 0
    try:
        for _ in root.rglob("*"):
            n += 1
    except Exception:
        pass
    return n


# ------------------------------------------------------------------
# v0.4.5: одиночный экземпляр лаунчера — повторный запуск (ярлык «Hox»,
# когда лаунчер свёрнут в трей) разворачивает первое окно, а не создаёт
# вторую копию. Служебный TCP-порт на 127.0.0.1: первый экземпляр
# занимает порт, вторая копия отправляет ему «hox-show» и завершается.
# ------------------------------------------------------------------

def _single_bind():
    """Занять служебный TCP-порт на 127.0.0.1 (признак первого
    экземпляра). None — ни один порт занять не удалось."""
    for port in SINGLE_PORTS:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if os.name == "nt":
                # на Windows исключающая привязка запрещает двойное
                # занятие порта вторым экземпляром (SO_REUSEADDR тут
                # работает наоборот — «увести» уже занятый порт)
                excl = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
                if excl is not None:
                    s.setsockopt(socket.SOL_SOCKET, excl, 1)
            else:
                try:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                except OSError:
                    pass
            s.bind(("127.0.0.1", port))
            s.listen(1)
            s.setblocking(False)
            return s
        except OSError:
            try:
                s.close()
            except Exception:  # noqa: BLE001
                pass
    return None


def _single_notify_existing() -> bool:
    """Сообщить запущенному лаунчеру «развернись». True — отправлено."""
    for port in SINGLE_PORTS:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.6)
        try:
            s.connect(("127.0.0.1", port))
            s.sendall(b"hox-show\n")
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            s.close()
            return True
        except OSError:
            try:
                s.close()
            except Exception:  # noqa: BLE001
                pass
    return False


# ------------------------------------------------------------------
# Qt-импорты (PyQt6; на Win7 их отдаёт fake-пакет pyqt6_compat)
# ------------------------------------------------------------------
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QProgressBar, QMessageBox, QFileDialog, QComboBox,
    QCheckBox, QGroupBox, QScrollArea, QTextEdit, QFrame, QDialog,
    QMenu, QSystemTrayIcon
)
from PyQt6.QtCore import (Qt, QThread, pyqtSignal, QTimer, QSettings,
                          QRect, QProcess)
from PyQt6.QtGui import QPixmap, QPainter, QColor, QLinearGradient, QPen, QFont, QIcon
# QAction в PyQt6 живёт в QtGui, в PyQt5 (fake-PyQt6 на Win7) — в QtWidgets
try:
    from PyQt6.QtGui import QAction  # noqa: F401
except Exception:  # noqa: BLE001
    from PyQt6.QtWidgets import QAction  # noqa: F401


class _SettingsShim:
    """Заглушка QSettings для воркера: заранее извлечённые значения,
    чтобы не трогать QSettings из чужого потока."""

    def __init__(self, repo: str, murl: str):
        self._repo, self._murl = repo, murl

    def value(self, key: str, default: str = "") -> str:
        if key == "updates/github_repo":
            return self._repo
        return self._murl


class CheckWorker(QThread):
    """Фоновое определение источника обновлений (GitHub -> манифест)."""
    resolved = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, repo: str, murl: str, parent=None):
        super().__init__(parent)
        self.repo, self.murl = repo, murl

    def run(self):
        try:
            src = resolve_update_source(_SettingsShim(self.repo, self.murl))
            self.resolved.emit(src)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class DownloadWorker(QThread):
    """Скачивание пакета обновления с прогрессом и проверкой SHA-256."""
    stage = pyqtSignal(str)
    progress = pyqtSignal(int)
    done_ok = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, url: str, dest: Path, sha256: str = "", parent=None):
        super().__init__(parent)
        self.url, self.dest, self.sha256 = url, dest, sha256

    def run(self):
        err = ""
        for attempt in (1, 2):  # одна повторная попытка при сбое сети
            try:
                req = urllib.request.Request(self.url, headers={"User-Agent": UA})
                with open_url(req, 30) as resp:
                    total = int(resp.headers.get("Content-Length", 0) or 0)
                    done = 0
                    hasher = hashlib.sha256()
                    with open(self.dest, "wb") as f:
                        while True:
                            chunk = resp.read(256 * 1024)
                            if not chunk:
                                break
                            f.write(chunk)
                            hasher.update(chunk)
                            done += len(chunk)
                            if total:
                                pct = int(done * 100 / total)
                                self.progress.emit(pct)
                                if pct % 5 == 0:
                                    self.stage.emit(
                                        "Скачиваю: {:.1f} из {:.1f} МБ".format(
                                            done / 1048576.0, total / 1048576.0))
                if self.sha256:
                    got = hasher.hexdigest().lower()
                    if got != self.sha256.lower():
                        raise ValueError(
                            "контрольная сумма не совпала ({}…)".format(got[:12]))
                self.done_ok.emit(str(self.dest))
                return
            except Exception as e:  # noqa: BLE001
                err = str(e)
                try:
                    if Path(self.dest).exists():
                        Path(self.dest).unlink()
                except Exception:
                    pass
                if attempt == 1:
                    time.sleep(1.0)  # короткая пауза и повтор
        self.failed.emit(err)


def _zip_contains_installer(zf: zipfile.ZipFile) -> bool:
    """zip с установщиком внутри (один .exe в корне) vs zip-пейлоад."""
    for n in zf.namelist():
        base = n.replace("\\", "/").split("/")[-1]
        if base.lower().endswith(".exe") and "/" not in n.replace("\\", "/"):
            return True
    return False


def _write_update_cmd(base: Path, tmp: Path) -> Path:
    """cmd-скрипт самообновления: ждёт закрытия лаунчера, копирует файлы,
    подчищает tmp и перезапускает лаунчер."""
    cmd = base / "update_hox.cmd"
    pyw = base / "runtime" / "python" / "pythonw.exe"
    lines = [
        "@echo off",
        "ping -n 3 127.0.0.1 >nul",
        'xcopy /y /e /i /q "{}\\app" "{}\\app\\" >nul'.format(tmp, base),
        'xcopy /y /e /i /q "{}\\runtime" "{}\\runtime\\" >nul'.format(tmp, base),
        'if exist "{0}\\support" xcopy /y /e /i /q "{0}\\support" "{1}\\support\\" >nul'.format(tmp, base),
        "rmdir /s /q \"{}\"".format(tmp),
        'start "" "{}" "{}\\Hox-Launcher.pyw"'.format(pyw, base / "app"),
        "del \"%~f0\"",
    ]
    cmd.write_text("\r\n".join(lines) + "\r\n", encoding="cp866", errors="replace")
    return cmd


class InstallWorker(QThread):
    """Установка плеера в выбранную папку — прямо из лаунчера.

    kind='player'   — файлы плеера из Hox-Player-*.zip в <папка>\\app
                      (лёгкое обновление: runtime и ярлыки не трогаем);
    kind='zip'      — распаковка пейлоада (или установщика из zip) с
                      пофайловым прогрессом;
    kind='selfcopy' — копирование собственной установки в новую папку
                      (перенос/восстановление без интернета);
    kind='setup'    — тихая установка Hox-Setup.exe (/S /D=) с опросом
                      числа файлов для прогресса (v3.6: также
                      самообновление лаунчера — check_file задаёт,
                      какой файл подтверждает успешную установку).
    """
    stage = pyqtSignal(str)
    progress = pyqtSignal(int)
    done_ok = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, kind: str, target: Path, desktop: bool, startmenu: bool,
                 zip_path: Optional[Path] = None, setup_path: Optional[Path] = None,
                 parent=None, check_file: str = PLAYER_SCRIPT):
        super().__init__(parent)
        self.kind = kind
        self.target = target
        self.desktop = desktop
        self.startmenu = startmenu
        self.zip_path = zip_path
        self.setup_path = setup_path
        self.check_file = check_file
        self._cancelled = False

    def log(self, msg: str) -> None:
        self.stage.emit(msg)

    # ---------- zip ----------

    def run_zip(self) -> None:
        own = find_install_base()
        target = self.target
        with zipfile.ZipFile(self.zip_path) as zf:
            if _zip_contains_installer(zf):
                # zip-обёртка установщика: распаковать во временную и запустить /S /D=
                self.log("В архиве установщик — распаковываю во временную папку…")
                tmpd = Path(tempfile.mkdtemp(prefix="Hox-setup-"))
                zf.extractall(tmpd)
                exe = None
                for p in tmpd.rglob("*.exe"):
                    exe = p
                    break
                if exe is None:
                    raise ValueError("установщик в архиве не найден")
                self.run_setup_exe(exe)
                try:
                    shutil.rmtree(tmpd, ignore_errors=True)
                except Exception:
                    pass
                return
            names = [i for i in zf.infolist() if not i.is_dir()]
            if own is not None and target.resolve() == Path(own).resolve():
                # самообновление текущей копии: через update_hox.cmd
                self.log("Обновляю текущую установку (файлы заменит командный скрипт)…")
                tmp = target / "update_tmp"
                if tmp.exists():
                    shutil.rmtree(tmp, ignore_errors=True)
                for i, info in enumerate(names):
                    zf.extract(info, tmp)
                    self.progress.emit(int((i + 1) * 100 / max(1, len(names))))
                cmd = _write_update_cmd(target, tmp)
                subprocess.Popen(["cmd", "/c", str(cmd)],
                                 creationflags=CREATE_NO_WINDOW, cwd=str(target))
                self.done_ok.emit("Обновление применится через пару секунд — "
                                  "лаунчер перезапустится автоматически.")
                return
            # обычная установка/обновление в выбранную папку
            n = len(names)
            skipped = 0
            for i, info in enumerate(names):
                dest = target / info.filename
                try:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info) as srcf, open(dest, "wb") as dstf:
                        shutil.copyfileobj(srcf, dstf)
                except PermissionError:
                    skipped += 1  # файл занят (например, сам pythonw.exe)
                except OSError as e:
                    if getattr(e, "winerror", None) in (5, 32):  # доступ/занято
                        skipped += 1
                    else:
                        raise
                self.progress.emit(int((i + 1) * 100 / max(1, n)))
                if i % 50 == 0:
                    self.log("Распаковка: {}/{} файлов".format(i + 1, n))
            self.log("Распаковано {} файлов{}".format(
                n, ", пропущено занятых: {}".format(skipped) if skipped else ""))
        ver = read_player_version()
        register_uninstaller(target, self.log)
        make_shortcuts(target, self.desktop, self.startmenu, self.log)
        if os.name == "nt" and not Path(os.environ.get("WINDIR", "C:\\Windows")) \
                .joinpath("System32", "ucrtbase.dll").exists() and \
                (target / "support" / "vc_redist.x86.exe").is_file():
            self.log("Внимание: в системе нет ucrtbase.dll — запустите "
                     "support\\vc_redist.x86.exe из папки установки (Windows 7).")
        self.done_ok.emit("Плеер {}установлен: {}".format(
            "пере" if own is not None else "", target) +
            ("\nВерсия плеера: v" + ver if ver else ""))

    # ---------- player zip (только файлы плеера) ----------

    def run_player_install(self) -> None:
        """Установка/обновление плеера из Hox-Player-*.zip: распаковка
        файлов плеера в <папка>\\app (runtime и ярлыки не трогаются)."""
        app_dir = self.target / "app"
        app_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(self.zip_path) as zf:
            names = [i for i in zf.infolist() if not i.is_dir()]
            # если архив собран с общей папкой сверху — срезаем её
            tops = set()
            for n in names:
                tops.add(n.filename.replace("\\", "/").split("/")[0])
            strip = ""
            if len(tops) == 1 and any("/" in n.filename.replace("\\", "/")
                                      for n in names):
                strip = tops.pop() + "/"
            safe = []
            for info in names:
                rel = info.filename.replace("\\", "/")
                if strip:
                    if not rel.startswith(strip):
                        continue
                    rel = rel[len(strip):]
                if not rel or rel.startswith("/") or ".." in rel.split("/"):
                    continue  # защита от выхода за пределы app
                if "/" in rel or rel.lower().endswith(".exe"):
                    continue  # плеер — плоский набор исходников, без .exe
                safe.append((info, rel))
            if not any(rel.lower() == PLAYER_SCRIPT for _, rel in safe):
                raise ValueError("в архиве нет " + PLAYER_SCRIPT)
            n = len(safe)
            skipped: List[str] = []
            written: List[str] = []
            for i, (info, rel) in enumerate(safe):
                dest = app_dir / rel
                try:
                    with zf.open(info) as srcf, open(dest, "wb") as dstf:
                        shutil.copyfileobj(srcf, dstf)
                    written.append(rel)
                except PermissionError:
                    skipped.append(rel)  # файл занят — не мешает остальным
                except OSError as e:
                    if getattr(e, "winerror", None) in (5, 32):
                        skipped.append(rel)
                    else:
                        raise
                self.progress.emit(int((i + 1) * 100 / max(1, n)))
            self.log("Файлов плеера установлено: {}{}".format(
                len(written),
                ", пропущено занятых: {}".format(len(skipped)) if skipped else ""))
            # манифест целостности: SHA-256 каждого установленного файла —
            # по нему каждый запуск лаунчера проверяет, что файлы на месте
            # и не повреждены (иначе — «Требуется обновление»)
            ver = _read_version_of(app_dir / PLAYER_SCRIPT)
            manifest = {"version": ver or "",
                        "installed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "files": {}}
            for rel in written:
                try:
                    manifest["files"][rel] = hashlib.sha256(
                        (app_dir / rel).read_bytes()).hexdigest()
                except Exception:
                    pass
            try:
                (app_dir / MANIFEST_NAME).write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=1),
                    encoding="utf-8")
                self.log("Манифест целостности записан ({} файлов)".format(
                    len(manifest["files"])))
            except Exception as e:  # noqa: BLE001
                self.log("Манифест целостности не записан: " + str(e))
        ver = _read_version_of(app_dir / PLAYER_SCRIPT) or read_player_version()
        self.done_ok.emit("Плеер установлен: v{}\nПапка: {}".format(
            ver or "?", app_dir))

    # ---------- selfcopy ----------

    def run_selfcopy(self) -> None:
        own = find_install_base()
        if own is None:
            raise ValueError("собственная установка не найдена — нечего копировать")
        own = Path(own)
        if own.resolve() == self.target.resolve():
            self.done_ok.emit("Плеер уже установлен в эту папку.")
            return
        subdirs = [d for d in ("app", "runtime", "support") if (own / d).is_dir()]
        allfiles: List[Path] = []
        for d in subdirs:
            allfiles.extend([p for p in (own / d).rglob("*") if p.is_file()])
        n = len(allfiles)
        if n == 0:
            raise ValueError("в собственной установке нет файлов")
        for i, src in enumerate(allfiles):
            rel = src.relative_to(own)
            dest = self.target / rel
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
            except PermissionError:
                pass  # занятые файлы не мешают остальным
            self.progress.emit(int((i + 1) * 100 / n))
            if i % 100 == 0:
                self.log("Копирование: {}/{} файлов".format(i + 1, n))
        self.log("Скопировано {} файлов".format(n))
        register_uninstaller(self.target, self.log)
        make_shortcuts(self.target, self.desktop, self.startmenu, self.log)
        ver = read_player_version()
        self.done_ok.emit("Плеер установлен: {}\nВерсия плеера: v{}".format(
            self.target, ver or "?"))

    # ---------- setup /S /D ----------

    def _verify_setup_installed(self) -> None:
        """Проверка результата тихой установки: check_file должен
        появиться в <target>\app. Для обновления плеера это файл плеера,
        для самообновления лаунчера (v3.6) — Hox-Launcher.pyw (плеер
        мог быть ещё не установлен — это не ошибка обновления)."""
        if not (self.target / "app" / self.check_file).is_file():
            raise ValueError("установщик завершился, но файл не появился: "
                             + self.check_file)

    def run_setup_exe(self, exe: Optional[Path] = None) -> None:
        exe = exe or self.setup_path
        if exe is None:
            raise ValueError("установщик не найден")
        self.log("Запускаю тихую установку: {}".format(exe.name))
        self.stage.emit("Установщик заменит файлы и перезапустит лаунчер — "
                        "это нормально, окно откроется само.")
        # /D обязан идти последним аргументом и БЕЗ кавычек (требование NSIS)
        cmdline = '"{}" /S /D={}'.format(exe, self.target)
        proc = subprocess.Popen(cmdline, creationflags=CREATE_NO_WINDOW)
        while True:
            if proc.poll() is not None:
                break
            found = count_files(self.target)
            self.progress.emit(min(95, int(found * 100 / EST_FILES)))
            time.sleep(1.0)
        self.progress.emit(100)
        self._verify_setup_installed()
        # NSIS создал все ярлыки сам — применяем выбор пользователя
        if os.name == "nt":
            ps_del = []
            if not self.desktop:
                ps_del.append(
                    "Remove-Item -LiteralPath ((New-Object -ComObject WScript.Shell)"
                    ".SpecialFolders('Desktop') + '\\Hox.lnk') -Force")
            if not self.startmenu:
                ps_del.append(
                    "Remove-Item -LiteralPath ((New-Object -ComObject WScript.Shell)"
                    ".SpecialFolders('StartMenu') + '\\Programs\\Hox') -Recurse -Force")
            for c in ps_del:
                subprocess.run(["powershell", "-NoProfile", "-Command", c],
                               capture_output=True, timeout=30,
                               creationflags=CREATE_NO_WINDOW, check=False)
        self.log("Ярлыки: " + ("рабочий стол + " if self.desktop else "")
                 + ("меню «Пуск»" if self.startmenu else "по выбору пользователя"))
        ver = read_player_version()
        self.done_ok.emit("Плеер {}установлен: {}\nВерсия плеера: v{}".format(
            "пере" if find_install_base() else "", self.target, ver or "?"))

    def run(self):
        try:
            self.target.mkdir(parents=True, exist_ok=True)
            if self.kind == "player":
                self.run_player_install()
            elif self.kind == "zip":
                self.run_zip()
            elif self.kind == "selfcopy":
                self.run_selfcopy()
            elif self.kind == "setup":
                self.run_setup_exe()
            else:
                raise ValueError("неизвестный тип установки: " + self.kind)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


# ------------------------------------------------------------------
# Логотип (та же пластика, что у плеера)
# ------------------------------------------------------------------
def draw_logo(size: int = 96) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r = size * 0.18
    grad = QLinearGradient(0, 0, size, size)
    grad.setColorAt(0.0, QColor(BG2))
    grad.setColorAt(1.0, QColor("#0b0e18"))
    p.setBrush(grad)
    p.setPen(QPen(QColor(ACCENT), max(1.0, size * 0.02)))
    p.drawRoundedRect(int(size * 0.04), int(size * 0.04),
                      int(size * 0.92), int(size * 0.92), int(r), int(r))
    bars = [0.42, 0.68, 0.52, 0.82, 0.60]
    bw = size * 0.09
    gap = size * 0.055
    total = len(bars) * bw + (len(bars) - 1) * gap
    x = (size - total) / 2
    base_y = size * 0.72
    for i, h_frac in enumerate(bars):
        h = size * h_frac * 0.62
        p.setBrush(QColor(ACCENT) if i % 2 == 0 else QColor(ACCENT2))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(int(x), int(base_y - h), int(bw), int(h), 2, 2)
        x += bw + gap
    p.end()
    return pm


def news_html(extra_head: str = "") -> str:
    """Встроенные новости -> HTML (свежая версия — акцентом, старые — глуше)."""
    parts = [extra_head] if extra_head else []
    for i, (ver, items) in enumerate(NEWS):
        head_color = ACCENT if i == 0 else MUTED
        parts.append("<p style='margin:10px 0 3px 0;'><b style='color:{0};"
                     "font-size:13px;'>v{1} — что нового</b></p>".format(head_color, ver))
        for it in items:
            parts.append("<p style='margin:3px 0 3px 14px;'>• {}</p>".format(it))
    return "".join(parts)


def release_notes_html(src: Dict[str, Any]) -> str:
    """Заметки релиза с GitHub -> HTML-блок для верха новостной ленты."""
    notes = str(src.get("notes", "") or "").strip()
    head = "<p style='margin:8px 0 2px 0;'><b style='color:{};'>v{} — заметки " \
           "релиза ({})</b></p>".format(WARN, src.get("version", "?"),
                                        src.get("source", ""))
    if not notes:
        return head + "<p style='margin:2px 0 2px 12px;color:{};'>Без описания.</p>".format(MUTED)
    esc = notes.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return head + "<p style='margin:2px 0 2px 12px;white-space:pre-wrap;'>" + esc + "</p>"


# ------------------------------------------------------------------
# Главное окно лаунчера
# ------------------------------------------------------------------
LAUNCHER_QSS = """
    QMainWindow, QDialog {{
        background: qlineargradient(y1:0, y2:1, stop:0 {bg}, stop:1 #0b0e19);
        color: {text};
    }}
    QLabel {{ color: {text}; }}
    QLabel#TitleLabel {{ font-size: 19px; font-weight: bold; color: #ffffff; }}
    QLabel#SubLabel {{ color: {muted}; font-size: 11px; }}
    QLabel#SectionLabel {{ color: {accent}; font-size: 11px; font-weight: bold;
                           letter-spacing: 2px; }}
    QLabel#ShotCaption {{ color: {muted}; font-size: 10px; }}
    QLabel#BetaBadge {{
        color: {bg}; background-color: {warn};
        border-radius: 10px; padding: 4px 12px;
        font-size: 11px; font-weight: bold; letter-spacing: 1px;
    }}
    QFrame#StatusCard {{
        background-color: {bg2}; border: 1px solid {border};
        border-left: 3px solid {accent};
        border-radius: 8px;
    }}
    QLabel#StatusLabel {{ font-size: 13px; }}
    QLabel#UpdateLabel {{ font-size: 12px; color: {muted}; }}
    QTextEdit#NewsBox, QTextEdit#LogBox {{
        background-color: {bg2}; color: {text};
        border: 1px solid {border}; border-radius: 8px;
        padding: 6px 8px;
    }}
    QTextEdit#LogBox {{ font-family: 'Consolas', 'Courier New', monospace;
                        font-size: 11px; color: {muted}; }}
    QScrollArea#ShotsArea {{ border: none; background: transparent; }}
    QWidget#ShotsRow {{ background: transparent; }}
    QScrollArea#OuterScroll {{ border: none; background: transparent; }}
    QScrollArea#OuterScroll > QWidget > QWidget {{ background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: #2a3252; border-radius: 4px;
                                   min-height: 24px; }}
    QScrollBar::handle:vertical:hover {{ background: {accent2}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
    QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 0; }}
    QScrollBar::handle:horizontal {{ background: #2a3252; border-radius: 4px;
                                     min-width: 24px; }}
    QScrollBar::handle:horizontal:hover {{ background: {accent2}; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: none; }}
    QPushButton {{ background-color: {bg2}; color: {text};
                   border: 1px solid {border}; border-radius: 7px;
                   padding: 8px 16px; font-size: 13px; }}
    QPushButton:hover {{ border-color: {accent}; color: {accent};
                         background-color: {bg3}; }}
    QPushButton:disabled {{ color: {muted}; border-color: #2a2f47;
                            background-color: {bg2}; }}
    QPushButton#LaunchButton {{
        background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
            stop:0 #00e5ff, stop:1 #00a8d8);
        color: #061018; font-weight: bold; font-size: 14px;
        border: none; border-radius: 8px; padding: 11px 18px;
    }}
    QPushButton#LaunchButton:hover {{
        background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
            stop:0 #33ecff, stop:1 #00c0ee);
        color: #061018;
    }}
    QPushButton#LaunchButton:disabled {{ background-color: {bg3};
                                         color: {muted}; }}
    QPushButton#InstallButton {{ border-color: {accent2};
                                 color: #b9c6ff; font-weight: bold; }}
    QPushButton#InstallButton:hover {{ border-color: {accent}; color: {accent}; }}
    QPushButton#ShotButton {{ background-color: {bg2}; border: 1px solid {border};
                              border-radius: 6px; padding: 2px; }}
    QPushButton#ShotButton:hover {{ border-color: {accent};
                                    background-color: #0e1428; }}
    QComboBox {{ background-color: {bg2}; color: {text};
                 border: 1px solid {border}; border-radius: 7px;
                 padding: 6px 10px; font-size: 12px; }}
    QComboBox:hover {{ border-color: {accent}; }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    QComboBox QAbstractItemView {{ background-color: {bg2};
                                   color: {text};
                                   selection-background-color: {bg3}; }}
    QCheckBox {{ color: {text}; font-size: 12px; spacing: 6px; }}
    QCheckBox::indicator {{ width: 15px; height: 15px; border-radius: 4px;
                            border: 1px solid {border};
                            background: {bg}; }}
    QCheckBox::indicator:hover {{ border-color: {accent}; }}
    QCheckBox::indicator:checked {{ background: {accent};
                                    border-color: {accent}; }}
    QGroupBox {{ color: {text}; background-color: {bg2};
                 border: 1px solid {border}; border-radius: 10px;
                 margin-top: 10px; padding: 10px 8px 8px 8px;
                 font-size: 12px; font-weight: bold; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 12px;
                        padding: 0 5px; color: {accent}; }}
    QProgressBar {{ background-color: {bg2}; border: 1px solid {border};
                    border-radius: 8px; text-align: center; color: {text};
                    font-size: 11px; min-height: 18px; }}
    QProgressBar::chunk {{ border-radius: 7px;
        background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
            stop:0 #00e5ff, stop:1 #3d5afe); }}
""".format(bg=BG, bg2=BG2, bg3=BG3, border=BORDER, accent=ACCENT,
           accent2=ACCENT2, text=TEXT, muted=MUTED, warn=WARN)


class ShotPreview(QDialog):
    """Просмотр скриншота крупно (клик — закрыть)."""

    def __init__(self, pm: QPixmap, caption: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(caption or "Скриншот")
        self.setStyleSheet("background-color: #0b0e18;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        img = QLabel()
        img.setPixmap(pm)
        img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(img)
        cap = QLabel(caption)
        cap.setStyleSheet("color: {}; font-size: 12px;".format(MUTED))
        cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(cap)


# ------------------------------------------------------------------
# Адаптивный размер окна: на маленьких экранах (1366x768 и ниже) окно
# должно влезать в экран ЦЕЛИКОМ, включая панель установки с кнопкой
# «Обновить» и прогресс-баром. Порядок сжатия: отступы -> новости ->
# журнал -> скриншоты; если не хватило и этого — вызывающий код
# включает внешнюю прокрутку (запасной режим).
# ------------------------------------------------------------------
WIN_W, WIN_H = 880, 816                     # исходный размер окна
NEWS_H, SHOTS_H, LOG_H = 148, 130, 76       # исходные высоты гибких блоков
NEWS_MIN, SHOTS_MIN, LOG_MIN = 72, 92, 48   # нижняя граница сжатия
FRAME_MARGIN = 36   # запас на заголовок окна + границы (вне client area)
COMPACT_SAVE = 51   # экономия компакт-режима: маргины 62->38 (24) + интервалы 9x8->9x5 (27)


def fit_window_size(avail_w: int, avail_h: int) -> Tuple[int, int, int, int, int, int]:
    """Размер окна и высоты гибких блоков, чтобы окно влезло в экран.

    Возвращает (w, h, news_h, shots_h, log_h, overflow); overflow > 0
    означает, что даже после сжатия контент выше экрана — нужна внешняя
    прокрутка (запасной режим для очень маленьких экранов).
    """
    w = min(WIN_W, max(640, avail_w - 24))
    h = min(WIN_H, max(480, avail_h - FRAME_MARGIN))
    news_h, shots_h, log_h = NEWS_H, SHOTS_H, LOG_H
    overflow = max(0, WIN_H - h)
    if overflow > 0:
        overflow -= COMPACT_SAVE  # плотнее отступы/интервалы в компакт-режиме
        take = min(max(0, news_h - NEWS_MIN), max(0, overflow))
        news_h -= take
        overflow -= take
        take = min(max(0, log_h - LOG_MIN), max(0, overflow))
        log_h -= take
        overflow -= take
        take = min(max(0, shots_h - SHOTS_MIN), max(0, overflow))
        shots_h -= take
        overflow -= take
        overflow = max(0, overflow)
    return w, h, news_h, shots_h, log_h, overflow


class LauncherWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(QIcon(draw_logo(64)))
        # Окно обязано влезать в экран целиком (1366x768 и меньше):
        # подбираем размер и высоты гибких блоков под доступную геометрию.
        scr = QApplication.primaryScreen()
        geo = scr.availableGeometry() if scr else QRect(0, 0, 1366, 768)
        win_w, win_h, self._news_h, self._shots_h, self._log_h, \
            self._scroll_overflow = fit_window_size(geo.width(), geo.height())
        self._compact = win_h < WIN_H
        self.setFixedSize(win_w, win_h)
        self.setStyleSheet(LAUNCHER_QSS)

        self.settings = QSettings(str(app_base() / "launcher.ini"),
                                  QSettings.Format.IniFormat)
        self._busy = False
        self._installed = False
        self._integrity_ok = True
        self._install_after_check = False
        self._pending: Optional[Dict[str, Any]] = None
        # v3.6: что ждёт в _pending — "" (архив плеера) или "launcher"
        # (установщик для самообновления лаунчера)
        self._pending_kind = ""
        self._downloaded: Optional[Path] = None
        self._check_thread: Optional[QThread] = None
        self._download_thread: Optional[QThread] = None
        self._install_thread: Optional[QThread] = None
        # v0.4.4 (лаунчер 3.4): отслеживание запущенного плеера
        self._player_proc: Optional["subprocess.Popen"] = None
        self._player_running = False
        self._ps_pids: List[int] = []
        self._ps_sweep_ts = 0.0
        self._ps_proc: Optional[QProcess] = None
        # v0.4.5 (лаунчер 3.5): трей и одиночный экземпляр
        self._tray_exit = False          # настоящий выход (меню значка)
        self._tray_notice_shown = False  # подсказку «в трее» — один раз
        self.tray: Optional[QSystemTrayIcon] = None
        self._tray_act_show = None
        self._tray_act_launch = None
        self._single_srv = None

        central = QWidget()
        if self._scroll_overflow > 0:
            # Экран настолько мал, что даже сжатый макет не влезает —
            # запасной режим: вся компоновка во внешней прокрутке.
            outer = QScrollArea()
            outer.setObjectName("OuterScroll")
            outer.setWidgetResizable(True)
            outer.setFrameShape(QFrame.Shape.NoFrame)
            outer.setWidget(central)
            self.setCentralWidget(outer)
        else:
            self.setCentralWidget(central)
        root = QVBoxLayout(central)
        if self._compact:
            root.setContentsMargins(12, 8, 12, 6)
            root.setSpacing(5)
        else:
            root.setContentsMargins(18, 14, 18, 12)
            root.setSpacing(8)

        # --- шапка ---
        head = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(draw_logo(56))
        head.addWidget(logo)
        tcol = QVBoxLayout()
        t = QLabel("Hox Music Player")
        t.setObjectName("TitleLabel")
        sub = QLabel("Лаунчер v{} — установка, обновления, новости".format(LAUNCHER_VERSION))
        sub.setObjectName("SubLabel")
        tcol.addWidget(t)
        tcol.addWidget(sub)
        head.addLayout(tcol, 1)
        badge = QLabel("РАННЯЯ БЕТА")
        badge.setObjectName("BetaBadge")
        head.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(head)

        # --- карточка состояния ---
        card = QFrame()
        card.setObjectName("StatusCard")
        clay = QVBoxLayout(card)
        clay.setContentsMargins(12, 8, 12, 8)
        clay.setSpacing(2)
        self.status_label = QLabel("Проверяю состояние…")
        self.status_label.setObjectName("StatusLabel")
        self.status_label.setWordWrap(True)
        self.update_label = QLabel("")
        self.update_label.setObjectName("UpdateLabel")
        self.update_label.setWordWrap(True)
        clay.addWidget(self.status_label)
        clay.addWidget(self.update_label)
        root.addWidget(card)

        # --- новости ---
        nl = QLabel("НОВОСТИ ВЕРСИЙ")
        nl.setObjectName("SectionLabel")
        root.addWidget(nl)
        self.news_box = QTextEdit()
        self.news_box.setObjectName("NewsBox")
        self.news_box.setReadOnly(True)
        self.news_box.setHtml(news_html())
        self.news_box.setFixedHeight(self._news_h)
        root.addWidget(self.news_box)

        # --- скриншоты ---
        self.shots_title = QLabel("СКРИНШОТЫ")
        self.shots_title.setObjectName("SectionLabel")
        root.addWidget(self.shots_title)
        self.shots_area = QScrollArea()
        self.shots_area.setObjectName("ShotsArea")
        self.shots_area.setWidgetResizable(True)
        self.shots_area.setFixedHeight(self._shots_h)
        # горизонтальная полоса — по необходимости (узкие экраны)
        self.shots_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.shots_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        shots_row = QWidget()
        shots_row.setObjectName("ShotsRow")
        srow = QHBoxLayout(shots_row)
        srow.setContentsMargins(0, 0, 0, 0)
        srow.setSpacing(8)
        shot_count = self._build_shots(srow)
        self.shots_area.setWidget(shots_row)
        root.addWidget(self.shots_area)
        if shot_count == 0:
            self.shots_area.hide()
            self.shots_title.hide()

        # --- установка ---
        self.install_box = QGroupBox("Установка и обновление")
        vbox = QVBoxLayout(self.install_box)
        vbox.setContentsMargins(12, 14, 12, 8)
        vbox.setSpacing(6)
        row1 = QHBoxLayout()
        row1.setSpacing(8)
        row1.addWidget(QLabel("Папка установки:"))
        self.dir_combo = QComboBox()
        self.dir_combo.setMinimumWidth(280)
        row1.addWidget(self.dir_combo, 1)
        self.btn_browse = QPushButton("Обзор…")
        self.btn_browse.clicked.connect(self.browse_dir)
        row1.addWidget(self.btn_browse)
        vbox.addLayout(row1)
        row2 = QHBoxLayout()
        row2.setSpacing(8)
        self.chk_desktop = QCheckBox("Ярлык на рабочем столе")
        self.chk_desktop.setChecked(True)
        row2.addWidget(self.chk_desktop)
        self.chk_startmenu = QCheckBox("Ярлык в меню «Пуск»")
        self.chk_startmenu.setChecked(True)
        row2.addWidget(self.chk_startmenu)
        row2.addStretch(1)
        self.btn_install = QPushButton("Установить Hox")
        self.btn_install.setObjectName("InstallButton")
        self.btn_install.clicked.connect(self.on_install_clicked)
        row2.addWidget(self.btn_install)
        vbox.addLayout(row2)
        root.addWidget(self.install_box)

        # --- прогресс + открыть ---
        prow = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        prow.addWidget(self.progress, 1)
        self.btn_open = QPushButton("Открыть плеер")
        self.btn_open.clicked.connect(self.launch_player)
        prow.addWidget(self.btn_open)
        root.addLayout(prow)

        # --- запуск/проверка ---
        brow = QHBoxLayout()
        self.btn_launch = QPushButton("▶  Запустить плеер")
        self.btn_launch.setObjectName("LaunchButton")
        self.btn_launch.clicked.connect(self.launch_player)
        brow.addWidget(self.btn_launch, 1)
        self.btn_check = QPushButton("⟳  Проверить обновления")
        self.btn_check.clicked.connect(self.check_updates)
        brow.addWidget(self.btn_check)
        root.addLayout(brow)

        # --- журнал ---
        self.log_box = QTextEdit()
        self.log_box.setObjectName("LogBox")
        self.log_box.setReadOnly(True)
        self.log_box.setFixedHeight(self._log_h)
        root.addWidget(self.log_box)

        self._make_tray()
        self.attach_single_listener(_single_bind())

        self._fill_dir_combo()
        self.refresh_state()
        self.log("Лаунчер v{} запущен (сборка плеера {})".format(
            LAUNCHER_VERSION, "Win7" if IS_WIN7_BUILD else "основная"))
        QTimer.singleShot(700, self.startup_check)
        # v0.4.4: опрос состояния плеера — кнопки запуска активны,
        # только пока плеер закрыт
        self._player_timer = QTimer(self)
        self._player_timer.setInterval(PLAYER_POLL_MS)
        self._player_timer.timeout.connect(self._refresh_player_state)
        self._player_timer.start()
        self._ps_sweep_async()  # первый фоновый свип процессов плеера

    # ---------- скриншоты ----------

    def _build_shots(self, lay: QHBoxLayout) -> int:
        n = 0
        for fname, caption in SHOTS:
            p = find_shot(fname)
            if not p or not p.is_file():
                continue
            pm = QPixmap(str(p))
            if pm.isNull():
                continue
            cell = QWidget()
            v = QVBoxLayout(cell)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(4)
            btn = QPushButton()
            btn.setObjectName("ShotButton")
            btn_h = max(64, self._shots_h - 24)
            thumb = pm.scaled(200, max(40, btn_h - 8),
                              Qt.AspectRatioMode.KeepAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
            btn.setIcon(QIcon(thumb))
            btn.setIconSize(thumb.size())
            btn.setFixedSize(204, btn_h)
            btn.setToolTip(caption)
            btn.clicked.connect(lambda checked=False, p_=pm, c_=caption:
                                self._show_shot(p_, c_))
            v.addWidget(btn)
            cap = QLabel(caption)
            cap.setObjectName("ShotCaption")
            cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cap.setFixedWidth(204)
            v.addWidget(cap)
            lay.addWidget(cell)
            n += 1
        lay.addStretch(1)
        return n

    def _show_shot(self, pm: QPixmap, caption: str) -> None:
        scr = QApplication.primaryScreen()
        maxw = int(scr.size().width() * 0.8) if scr else 1100
        maxh = int(scr.size().height() * 0.8) if scr else 700
        scaled = pm
        if pm.width() > maxw or pm.height() > maxh:
            scaled = pm.scaled(maxw, maxh, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
        dlg = ShotPreview(scaled, caption, self)
        dlg.setModal(True)
        dlg.exec()

    # ---------- журнал ----------

    def log(self, msg: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log_box.append("[{}] {}".format(stamp, msg))

    # ---------- состояние ----------

    def _fill_dir_combo(self) -> None:
        base = find_install_base()
        local = local_appdata()
        own = own_base()
        self.dir_combo.clear()
        if base is not None:
            items = ["{}  —  текущая установка".format(base)]
            if base.resolve() != own.resolve():
                items.append("{}  —  папка лаунчера".format(own))
        else:
            items = ["{}  —  папка лаунчера".format(own)]
        items.append("{}  —  по умолчанию".format(local / "Hoxmusicp"))
        items.append("{}  —  Programs\\Hox".format(local / "Programs" / "Hox"))
        for it in items:
            if self.dir_combo.findText(it) < 0:
                self.dir_combo.addItem(it)
        self.dir_combo.setCurrentIndex(0)

    def refresh_state(self) -> None:
        base = find_install_base()
        ver = read_player_version()
        ok, detail = player_integrity()
        self._integrity_ok = ok
        self._installed = bool(base and find_runtime() and find_player_script() and ok)
        if not base or not find_player_script():
            self.status_label.setText(
                "Требуется обновление: плеер не установлен. Нажмите "
                "«Обновить / Переустановить» — лаунчер скачает архив плеера "
                "с GitHub и установит его (нужен интернет).")
        elif not ok:
            self.status_label.setText(
                "Требуется обновление: файлы плеера отсутствуют или повреждены "
                "({}). Нажмите «Обновить / Переустановить» для восстановления."
                .format(detail))
        else:
            self.status_label.setText(
                "Плеер установлен: v{} — {}".format(ver or "?", base))
        running = bool(getattr(self, "_player_running", False))
        act = self._installed and not self._busy and not running
        self.btn_launch.setEnabled(act)
        self.btn_launch.setText(
            "●  Плеер запущен" if running else "▶  Запустить плеер")
        self.btn_launch.setToolTip("Плеер уже запущен" if running else "")
        self.btn_open.setEnabled(act)
        self.btn_open.setToolTip("Плеер уже запущен" if running else "")
        self.btn_check.setEnabled(not self._busy)
        # v0.4.5: синхронизировать «Запустить плеер» в меню значка трея
        if self._tray_act_launch is not None:
            self._tray_act_launch.setEnabled(act)
        if self._pending is not None and self._pending_kind == "launcher":
            self.btn_install.setText("⬆  Обновить лаунчер до v{}".format(
                self._pending.get("version", "")))
        elif self._pending is not None:
            self.btn_install.setText("⬇  Обновить до v{}".format(
                self._pending.get("version", "")))
        elif not self._installed:
            self.btn_install.setText("⬇  Обновить / Переустановить")
        else:
            self.btn_install.setText("Переустановить / восстановить")
        # кнопка установки всегда активна (кроме занятого состояния) —
        # скачивание начинается только по нажатию, без авто-скачиваний
        self.btn_install.setEnabled(not self._busy)
        self.btn_browse.setEnabled(not self._busy)
        self.dir_combo.setEnabled(not self._busy)
        self.chk_desktop.setEnabled(not self._busy)
        self.chk_startmenu.setEnabled(not self._busy)

    def set_busy(self, on: bool) -> None:
        self._busy = on
        if on:
            self.progress.setValue(0)
        self.refresh_state()

    # ---------- трей и одиночный экземпляр (v0.4.5) ----------

    def _make_tray(self) -> None:
        """Значок в системном трее: клик — показать/скрыть окно,
        меню — показать, запустить плеер, выход. Если трея нет
        (или окружение тестовое), лаунчер работает как раньше."""
        try:
            icon = self.windowIcon()
            if icon.isNull():
                icon = QIcon(draw_logo(64))
            tray = QSystemTrayIcon(icon, self)
            tray.setToolTip(APP_TITLE)
            menu = QMenu()
            act_show = QAction("Показать лаунчер", menu)
            act_show.triggered.connect(self.restore_from_tray)
            act_launch = QAction("Запустить плеер", menu)
            act_launch.triggered.connect(self.launch_player)
            act_quit = QAction("Выход", menu)
            act_quit.triggered.connect(self.tray_quit)
            menu.addAction(act_show)
            menu.addAction(act_launch)
            menu.addSeparator()
            menu.addAction(act_quit)
            tray.setContextMenu(menu)
            tray.activated.connect(self._on_tray_activated)
            tray.show()
            self.tray = tray
            self._tray_act_show = act_show
            self._tray_act_launch = act_launch
        except Exception:  # noqa: BLE001
            self.tray = None
            self._tray_act_show = None
            self._tray_act_launch = None

    def _on_tray_activated(self, reason) -> None:
        """Клик по значку трея: показать/скрыть окно лаунчера."""
        try:
            r = int(reason)
        except Exception:  # noqa: BLE001
            return
        # 3 = Trigger (одинарный клик), 2 = DoubleClick — значения
        # совпадают в Qt5 и Qt6; числовое сравнение обходит разницу
        # в доступе к scoped-энумам между версиями
        if r not in (2, 3):
            return
        if self.isVisible() and not self.isMinimized():
            self.minimize_to_tray()
        else:
            self.restore_from_tray()

    def minimize_to_tray(self, notice: str = "") -> None:
        """Свернуть окно в трей, не завершая лаунчер."""
        if self.tray is None:
            self.showMinimized()
            return
        self.hide()
        if notice:
            try:
                self.tray.showMessage(APP_TITLE, notice)
            except Exception:  # noqa: BLE001
                pass

    def restore_from_tray(self) -> None:
        """Развернуть окно лаунчера (из трея или при повторном запуске)."""
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def tray_quit(self) -> None:
        """Настоящий выход из лаунчера (пункт «Выход» в меню значка)."""
        self._tray_exit = True
        self.close()

    def _teardown(self) -> None:
        """Остановить фоновые службы перед настоящим выходом."""
        if self._ps_proc is not None:
            try:
                self._ps_proc.kill()
            except Exception:  # noqa: BLE001
                pass
        if self.tray is not None:
            try:
                self.tray.hide()
            except Exception:  # noqa: BLE001
                pass
        srv = self._single_srv
        if srv is not None:
            try:
                srv.close()
            except Exception:  # noqa: BLE001
                pass

    def attach_single_listener(self, srv) -> None:
        """Слушать запросы «развернись» от вторых копий лаунчера."""
        self._single_srv = srv
        if srv is None:
            return
        self._single_timer = QTimer(self)
        self._single_timer.setInterval(400)
        self._single_timer.timeout.connect(self._poll_single)
        self._single_timer.start()

    def _poll_single(self) -> None:
        """Забрать запрос второй копии и развернуть окно."""
        srv = self._single_srv
        if srv is None:
            return
        try:
            conn, _addr = srv.accept()
        except OSError:
            return
        if conn is None:
            return
        try:
            conn.settimeout(0.3)
            try:
                conn.recv(64)
            except OSError:
                pass
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        self.restore_from_tray()
        self.log("Лаунчер развёрнут по повторному запуску")

    # ---------- состояние плеера: запущен/закрыт (v0.4.4) ----------

    def _check_in_flight(self) -> bool:
        """Идёт ли сейчас проверка обновлений (её поток)."""
        return self._check_thread is not None and self._check_thread.isRunning()

    def _lock_pids(self) -> List[int]:
        """PID из player.lock — файл пишет плеер при старте, убирает при выходе."""
        pids: List[int] = []
        for f in lock_candidates():
            try:
                if not f.is_file():
                    continue
                raw = f.read_text(encoding="ascii", errors="ignore").strip()
                if not re.match(r"^\d{1,7}$", raw):
                    continue
                pid = int(raw)
                if pid > 0 and pid not in pids:
                    pids.append(pid)
            except Exception:
                continue
        return pids

    def _drop_dead_locks(self, dead: List[int]) -> None:
        """Убрать player.lock с мёртвым PID (плеер упал или был убит)."""
        dead_s = {str(p) for p in dead}
        for f in lock_candidates():
            try:
                if f.is_file() and f.read_text(
                        encoding="ascii", errors="ignore").strip() in dead_s:
                    f.unlink()
                    self.log("Убран устаревший player.lock (процесс плеера завершён)")
            except Exception:
                pass

    def _quick_player_probe(self) -> bool:
        """Быстрая проверка без PowerShell: player.lock + кэш свипа."""
        known = self._lock_pids() + list(self._ps_pids)
        known = [p for p in dict.fromkeys(known) if p != os.getpid()]
        return bool(_pids_alive(known))

    def _ps_sweep_async(self) -> None:
        """Фоновый поиск процессов плеера через PowerShell (UI не блокируется).

        Нужен для старых установок, где плеер ещё не пишет player.lock."""
        if os.name != "nt" or self._ps_proc is not None:
            return
        try:
            p = QProcess(self)
            p.setProgram("powershell")
            p.setArguments(["-NoProfile", "-ExecutionPolicy", "Bypass",
                            "-Command", PS_PLAYER_PIDS_CMD])
            p.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
            p.finished.connect(self._on_ps_sweep_finished)
            self._ps_proc = p
            p.start()
        except Exception:
            self._ps_proc = None

    def _on_ps_sweep_finished(self, *args) -> None:  # noqa: ANN002
        p, self._ps_proc = self._ps_proc, None
        out = ""
        if p is not None:
            try:
                out = bytes(p.readAllStandardOutput()).decode("utf-8", "ignore")
            except Exception:
                out = ""
        self._ps_pids = [x for x in _parse_pids_text(out) if x != os.getpid()]
        if not self._busy and not self._check_in_flight():
            self._refresh_player_state()

    def _refresh_player_state(self) -> None:
        """Опрос состояния плеера (таймер PLAYER_POLL_MS):
        свой Popen -> player.lock -> кэш PowerShell-свипа (+ новый свип)."""
        if self._busy or self._check_in_flight():
            return
        running = False
        proc = self._player_proc
        if proc is not None:
            if proc.poll() is None:
                running = True
            else:
                self._player_proc = None
        known = self._lock_pids() + list(self._ps_pids)
        known = [p for p in dict.fromkeys(known) if p != os.getpid()]
        alive = _pids_alive(known)
        if alive:
            running = True
        alive_set = set(alive)
        dead = [p for p in known if p not in alive_set]
        if dead:
            self._drop_dead_locks(dead)
        self._ps_pids = [p for p in self._ps_pids if p in alive_set]
        if not running:
            now = time.monotonic()
            if now - self._ps_sweep_ts >= PS_SWEEP_SEC:
                self._ps_sweep_ts = now
                self._ps_sweep_async()
        self._apply_player_running(running)

    def _apply_player_running(self, running: bool) -> None:
        """Синхронизировать кнопки запуска с фактическим состоянием плеера."""
        if running == self._player_running:
            return
        self._player_running = running
        if self._busy or self._check_in_flight():
            return  # кнопками управляет check_updates/refresh_state
        if running:
            self.log("Плеер запущен — кнопки запуска отключены")
            # v0.4.5: пока плеер играет — окно лаунчера сворачивается в трей
            self.minimize_to_tray(
                "Плеер запущен — лаунчер свёрнут в трей.\n"
                "Щёлкните значок в трее, чтобы вернуть окно; когда "
                "закроете плеер, лаунчер развернётся сам.")
        else:
            self.log("Плеер закрыт — запуск снова доступен")
            # v0.4.5: плеер закрыт — окно лаунчера разворачивается обратно
            self.restore_from_tray()
            if self.tray is not None:
                try:
                    self.tray.showMessage(
                        APP_TITLE, "Плеер закрыт — лаунчер развёрнут.")
                except Exception:  # noqa: BLE001
                    pass
        self.refresh_state()

    # ---------- проверка обновлений ----------

    def startup_check(self) -> None:
        self.check_updates(auto=True)

    def check_updates(self, checked: bool = False, auto: bool = False) -> None:
        if self._busy:
            return
        repo = str(self.settings.value("updates/github_repo", "") or "").strip() or GITHUB_REPO.strip()
        murl = str(self.settings.value("updates/manifest_url", "") or "").strip()
        self._check_thread = CheckWorker(repo, murl)
        self._check_thread.resolved.connect(
            lambda d, a=auto: self.on_source_resolved(d, a))
        self._check_thread.failed.connect(
            lambda e, a=auto: self.on_check_failed(e, a))
        # на время проверки запуск плеера заблокирован; кнопка установки
        # остаётся активной — если проверка затянется, установку можно
        # запросить сразу (она выполнится после ответа GitHub)
        self.btn_launch.setEnabled(False)
        self.btn_launch.setText("Проверяю обновления…")
        self.btn_open.setEnabled(False)
        self.btn_check.setEnabled(False)
        self.btn_install.setEnabled(True)
        self.update_label.setText("Проверяю обновления…")
        self.update_label.setStyleSheet("color: {};".format(MUTED))
        self.log("Проверка обновлений: GitHub={} манифест={}".format(
            repo or "—", murl or "—"))
        self._check_thread.start()

    def on_check_failed(self, err: str, auto: bool) -> None:
        need_install = self._install_after_check
        self._install_after_check = False
        ok, detail = player_integrity()
        if ok:
            self.update_label.setText(
                "Не удалось проверить обновления: {}. Проверка запустится при "
                "следующем старте лаунчера или по кнопке.".format(err[:120]))
        else:
            self.update_label.setText(
                "ТРЕБУЕТСЯ ОБНОВЛЕНИЕ ({}), но источник сейчас недоступен: {}. "
                "Нажмите кнопку ещё раз или положите Hox-Player-*.zip рядом "
                "с лаунчером.".format(detail, err[:100]))
        self.update_label.setStyleSheet(
            "color: {}; font-weight: bold;".format(WARN))
        self.log("Проверка обновлений не удалась: " + err)
        if need_install:
            QMessageBox.warning(
                self, "Hox",
                "Не удалось получить источник установки:\n" + err[:200] +
                "\n\nПроверьте интернет и нажмите кнопку установки ещё раз, "
                "либо положите архив плеера Hox-Player-*.zip рядом с лаунчером.")
        elif not auto:
            QMessageBox.warning(self, "Hox",
                                "Не удалось проверить обновления:\n" + err)
        self.refresh_state()

    def on_source_resolved(self, src: Dict[str, Any], auto: bool) -> None:
        ok, detail = player_integrity()
        local = read_player_version() or "0"
        click_install = self._install_after_check
        self._install_after_check = False
        if src.get("kind") == "none":
            self._pending = None
            self._pending_kind = ""
            self.update_label.setText(
                "Источник обновлений не настроен — лаунчер работает автономно. "
                "Укажите GitHub-репозиторий (launcher.ini → [updates] github_repo) "
                "или URL манифеста, либо положите Hox-Player-*.zip рядом с лаунчером.")
            self.update_label.setStyleSheet("color: {};".format(MUTED))
            self.log("Источник обновлений не настроен")
            if click_install:
                QMessageBox.information(
                    self, "Hox",
                    "Источник обновлений не настроен.\n\n"
                    "Варианты:\n"
                    "1) launcher.ini → [updates] github_repo = владелец/репозиторий — "
                    "обновления будут браться с GitHub Releases;\n"
                    "2) launcher.ini → [updates] manifest_url = URL JSON-манифеста "
                    "{version, url, sha256};\n"
                    "3) автономно: положите Hox-Player-*.zip или установщик "
                    "Hox-Setup.exe рядом с лаунчером.")
            self.refresh_state()
            return
        remote = str(src.get("version", ""))
        self.log("Источник: {} — v{}".format(src.get("source"), remote))
        try:
            has_update = parse_version(remote) > parse_version(local)
        except Exception:  # noqa: BLE001
            has_update = False
        # v3.6: обновление САМОГО лаунчера — приоритет над обновлением плеера:
        # свежий лаунчер гарантирует корректную установку новых версий плеера
        if bool(src.get("launcher_update")):
            self._pending = dict(src)
            self._pending_kind = "launcher"
            self._downloaded = None
            self.update_label.setText(
                "ДОСТУПНО ОБНОВЛЕНИЕ ЛАУНЧЕРА: v{} (у вас v{}) — нажмите "
                "«Обновить лаунчер до v{}»: скачается установщик, проверится по "
                "SHA-256 и запустится тихо — лаунчер перезапустится сам, после "
                "чего обновится и плеер.".format(remote, RELEASE_VERSION, remote))
            self.update_label.setStyleSheet(
                "color: {}; font-weight: bold;".format(WARN))
            self.news_box.setHtml(news_html(extra_head=release_notes_html(src)))
            self.log("Доступно обновление лаунчера: v{} -> v{}".format(
                RELEASE_VERSION, remote))
            self.refresh_state()
            return
        self._pending_kind = ""
        if click_install:
            # пользователь нажал «Обновить / Переустановить» — качаем и ставим
            self._pending = dict(src)
            self._downloaded = None
            self.news_box.setHtml(news_html(extra_head=release_notes_html(src)))
            self.log("Установка по кнопке: скачиваю v{}".format(remote))
            self.refresh_state()
            self.start_update()
            return
        if not ok:
            # файлы плеера отсутствуют или повреждены — статус и ЖДЁМ нажатия
            self._pending = dict(src)
            self._downloaded = None
            self.update_label.setText(
                "ТРЕБУЕТСЯ ОБНОВЛЕНИЕ ({}). Нажмите «Обновить / Переустановить», "
                "чтобы скачать и установить v{} ({}).".format(
                    detail, remote, src.get("source", "")))
            self.update_label.setStyleSheet(
                "color: {}; font-weight: bold;".format(WARN))
            self.news_box.setHtml(news_html(extra_head=release_notes_html(src)))
            self.log("Требуется обновление: {}".format(detail))
            self.refresh_state()
            return
        if has_update:
            # вышла новая версия — статус и ждём нажатия кнопки (без диалогов)
            self._pending = dict(src)
            self._downloaded = None
            self.update_label.setText(
                "ТРЕБУЕТСЯ ОБНОВЛЕНИЕ: доступна версия v{} (у вас v{}) — "
                "нажмите «Обновить до v{}».".format(remote, local, remote))
            self.update_label.setStyleSheet(
                "color: {}; font-weight: bold;".format(WARN))
            self.news_box.setHtml(news_html(extra_head=release_notes_html(src)))
            self.log("Доступно обновление: v{} -> v{}".format(local, remote))
            self.refresh_state()
            return
        self._pending = None
        self._pending_kind = ""
        self.update_label.setText(
            "У вас последняя версия: лаунчер v{} (релиз v{}), плеер v{} — "
            "источник: {}. Файлы проверены — целы.".format(
                LAUNCHER_VERSION, RELEASE_VERSION, local, src.get("source", "")))
        self.update_label.setStyleSheet("color: {};".format(OK))
        self.refresh_state()

    # ---------- скачивание и установка ----------

    def on_install_clicked(self) -> None:
        if self._busy:
            return
        if self._pending is not None and self._downloaded is None:
            self.start_update()
            return
        if self._downloaded is not None:
            self.run_install(self._downloaded, "скачанный пакет", "player")
            return
        # офлайн-источники (без интернета)
        pzip = find_local_player_zip()
        if pzip is not None:
            self.run_install(pzip, "архив плеера рядом с лаунчером", "player")
            return
        setup = find_local_setup()
        if setup is not None:
            self.run_install(setup, "установщик рядом с лаунчером")
            return
        full = find_local_payload_zip()
        if full is not None:
            self.run_install(full, "пейлоад рядом с лаунчером")
            return
        # локальных источников нет — качаем с GitHub (если проверка ещё
        # не сделана, сначала спрашиваем GitHub, потом сразу ставим)
        self._install_after_check = True
        if self._check_thread is not None and self._check_thread.isRunning():
            self.update_label.setText(
                "Ищу источник установки — идёт проверка GitHub…")
            self.update_label.setStyleSheet("color: {};".format(MUTED))
            self.log("Кнопка установки нажата во время проверки — "
                     "установлю сразу после ответа GitHub")
            return
        self.log("Локальных источников нет — запрашиваю GitHub")
        self.check_updates()

    def start_update(self) -> None:
        if self._pending is None or self._busy:
            return
        src = self._pending
        if self._pending_kind == "launcher":
            # v3.6: качаем установщик лаунчера Hox-Setup-<ver>.exe
            # (Win7-сборка — Hox-Setup-Win7-<ver>.exe), а не архив плеера
            url = str(src.get("setup_url", "") or "")
            if not url:
                QMessageBox.warning(
                    self, "Hox",
                    "В релизе нет установщика лаунчера Hox-Setup-*.exe — "
                    "обновите лаунчер вручную, скачав установщик с GitHub.")
                return
            ver = str(src.get("version", "new"))
            last = str(src.get("setup_name", "") or "") or \
                "Hox-Setup-" + ver + ".exe"
            sha = str(src.get("setup_sha256", "") or "")
        else:
            url = str(src.get("url", "") or "")
            if not url:
                QMessageBox.warning(self, "Hox",
                                    "В источнике обновления нет ссылки на пакет.")
                return
            ver = str(src.get("version", "new"))
            last = url.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
            if not last.lower().endswith((".zip", ".exe")):
                last = "Hox-Player-" + ver + ".zip"
            sha = str(src.get("sha256", "") or "")
        dest = Path(tempfile.gettempdir()) / ("Hox-download-" + ver + "-" + last)
        self.set_busy(True)
        self.progress.setValue(0)
        if self._pending_kind == "launcher":
            self.status_label.setText("Скачиваю обновление лаунчера v{}…".format(ver))
            self.log("Скачивание установщика лаунчера: " + url)
        else:
            self.status_label.setText("Скачиваю обновление v{}…".format(ver))
            self.log("Скачивание: " + url)
        self._download_thread = DownloadWorker(url, dest, sha)
        self._download_thread.progress.connect(self.progress.setValue)
        self._download_thread.stage.connect(self.status_label.setText)
        self._download_thread.done_ok.connect(self.on_download_done)
        self._download_thread.failed.connect(self.on_download_failed)
        self._download_thread.start()

    def on_download_done(self, path: str) -> None:
        self._downloaded = Path(path)
        self.log("Скачано: {} ({:.1f} МБ)".format(
            path, self._downloaded.stat().st_size / 1048576.0))
        # FIX v0.4.3 (Лаунчер 3.3): две ошибки v0.4.2 на пути
        # «скачал -> установил»:
        # 1) busy оставался поднятым после скачивания, а run_install()
        #    начинается с `if self._busy: return` — установка молча
        #    пропускалась, лаунчер зависал с неактивными кнопками
        #    (лог обрывался на «Скачано …»; доказано тестом
        #    scripts/prove_busy_bug.py на коде v0.4.2);
        # 2) kind_hint не передавался: пакет качается под именем
        #    Hox-download-<ver>-Hox-Player-<ver>.zip, проверка
        #    startswith("hox-player-") на нём ложна -> установка шла по
        #    generic-пути zip (без манифеста целостности, а при цели в
        #    собственную папку — «самообновление» через cmd, которое с
        #    плоским zip плеера ничего не устанавливает). Скачивание архива
        #    плеера передаёт kind_hint="player" явно; v3.6: путь
        #    самообновления лаунчера передаёт kind_hint="launcher".
        self.set_busy(False)
        if self._pending_kind == "launcher":
            # v3.6: скачан установщик лаунчера — тихая установка /S /D=;
            # установщик закроет этот экземпляр лаунчера, заменит файлы
            # и сам перезапустит уже новый лаунчер
            self.run_install(self._downloaded, "скачанный установщик лаунчера",
                             "launcher")
            return
        self.run_install(self._downloaded, "скачанный пакет", "player")

    def on_download_failed(self, err: str) -> None:
        self.set_busy(False)
        self.status_label.setText("Скачивание не удалось.")
        self.log("Скачивание не удалось: " + err)
        QMessageBox.warning(self, "Hox", "Не удалось скачать обновление:\n" + err)

    def run_install(self, path: Optional[Path], src_label: str,
                    kind_hint: str = "") -> None:
        if self._busy:
            return
        target = expand_dir(self.dir_combo.currentText())
        try:
            target.mkdir(parents=True, exist_ok=True)
            probe = target / ".hox_write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(
                self, "Hox",
                "Нельзя писать в выбранную папку:\n{}\n\nОшибка: {}".format(target, e))
            return
        self.set_busy(True)
        self.progress.setValue(0)
        self.status_label.setText("Закрываю запущенный плеер…")
        QApplication.processEvents()
        if not close_player_processes():
            self.set_busy(False)
            QMessageBox.warning(
                self, "Hox",
                "Не удалось закрыть запущенный плеер.\nЗакройте Hox Music Player "
                "вручную и повторите.")
            return
        # плеер закрыт — сбрасываем признак «запущен»; player.lock с мёртвым
        # PID уберётся сам при следующем опросе (_drop_dead_locks)
        self._player_proc = None
        self._player_running = False
        desktop = self.chk_desktop.isChecked()
        startmenu = self.chk_startmenu.isChecked()
        if path is None:
            kind = "selfcopy"
            self._install_thread = InstallWorker(kind, target, desktop, startmenu)
        elif path.suffix.lower() == ".exe":
            kind = "setup"
            # v3.6: путь самообновления лаунчера — проверяем файл лаунчера,
            # а не плеера (плеер мог быть ещё не установлен)
            check_file = LAUNCHER_FILE if kind_hint == "launcher" \
                else PLAYER_SCRIPT
            self._install_thread = InstallWorker(kind, target, desktop, startmenu,
                                                 setup_path=path,
                                                 check_file=check_file)
        elif kind_hint == "player" or "hox-player-" in path.name.lower():
            kind = "player"
            self._install_thread = InstallWorker(kind, target, desktop, startmenu,
                                                 zip_path=path)
        else:
            kind = "zip"
            self._install_thread = InstallWorker(kind, target, desktop, startmenu,
                                                 zip_path=path)
        self.status_label.setText("Устанавливаю плеер из: {}".format(src_label))
        self.log("Установка из {} в {}".format(src_label, target))
        self._install_thread.stage.connect(self.status_label.setText)
        self._install_thread.stage.connect(self.log)
        self._install_thread.progress.connect(self.progress.setValue)
        self._install_thread.done_ok.connect(self.on_install_done)
        self._install_thread.failed.connect(self.on_install_failed)
        self._install_thread.start()

    def on_install_done(self, msg: str) -> None:
        self.set_busy(False)
        self.progress.setValue(100)
        self.status_label.setText("Готово — " + msg.replace("\n", " "))
        self.update_label.setText(
            "Установка завершена. Файлы на месте — нажмите «Запустить плеер».")
        self.update_label.setStyleSheet("color: {};".format(OK))
        self.log("Установка завершена: " + msg.replace("\n", " "))
        self._fill_dir_combo()
        # после установки плеер закрыт — сброс состояния запуска
        self._player_proc = None
        self._player_running = False
        self.refresh_state()

    def on_install_failed(self, err: str) -> None:
        self.set_busy(False)
        self.progress.setValue(0)
        self.status_label.setText("Установка не удалась: {}".format(err[:150]))
        self.log("Установка не удалась: " + err)
        QMessageBox.critical(self, "Hox", "Ошибка установки:\n" + err)

    # ---------- запуск плеера ----------

    def launch_player(self) -> None:
        if self._busy:
            return
        proc = self._player_proc
        if (proc is not None and proc.poll() is None) or self._player_running \
                or self._quick_player_probe():
            self._apply_player_running(True)
            self.status_label.setText("Плеер уже запущен")
            self.log("Плеер уже запущен — повторный запуск не требуется")
            return
        rt = find_runtime()
        ps = find_player_script()
        if not (rt and ps):
            QMessageBox.warning(self, "Hox", "Плеер не установлен.")
            return
        try:
            kwargs = {}
            if os.name == "nt":
                kwargs["creationflags"] = CREATE_NO_WINDOW
            self._player_proc = subprocess.Popen([str(rt), str(ps)],
                                                 cwd=str(ps.parent), **kwargs)
            self._apply_player_running(True)
            self.status_label.setText("Плеер запущен")
            self.log("Плеер запущен: " + str(rt))
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Hox", "Не удалось запустить плеер:\n" + str(e))

    # ---------- выбор папки ----------

    def browse_dir(self) -> None:
        start = expand_dir(self.dir_combo.currentText())
        d = QFileDialog.getExistingDirectory(self, "Папка установки Hox", str(start))
        if d:
            self.dir_combo.insertItem(0, d)
            self.dir_combo.setCurrentIndex(0)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._tray_exit:
            # настоящий выход: пункт «Выход» меню трея или выход во время
            # скачивания/установки по подтверждению пользователя
            self._teardown()
            event.accept()
            return
        if self._busy and (self._download_thread and self._download_thread.isRunning()
                           or self._install_thread and self._install_thread.isRunning()):
            btn = QMessageBox.question(
                self, "Hox",
                "Идёт скачивание/установка. Прервать и закрыть лаунчер?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if btn != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self._tray_exit = True
            self._teardown()
            event.accept()
            return
        # v0.4.5: крестик сворачивает лаунчер в трей — плеер, проверки и
        # обновления продолжают работать; настоящий выход — меню значка
        if self.tray is not None:
            event.ignore()
            self.hide()
            if not self._tray_notice_shown:
                self._tray_notice_shown = True
                try:
                    self.tray.showMessage(
                        APP_TITLE,
                        "Лаунчер свёрнут в трей. Щёлкните значок, чтобы "
                        "открыть окно; выход — в меню значка.")
                except Exception:  # noqa: BLE001
                    pass
            return
        self._teardown()
        event.accept()


def main():
    os.environ.setdefault("QT_LOGGING_RULES", "*.debug=false")
    app = QApplication(sys.argv)
    app.setApplicationName("Hox Launcher")
    app.setApplicationVersion(LAUNCHER_VERSION)
    app.setWindowIcon(QIcon(draw_logo(64)))
    # v0.4.5: одиночный экземпляр — если лаунчер уже запущен (в т.ч.
    # свёрнут в трей), повторный запуск разворачивает первое окно
    srv = _single_bind()
    if srv is None and _single_notify_existing():
        return
    win = LauncherWindow()
    win.attach_single_listener(srv)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
