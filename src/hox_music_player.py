import os
import sys
import re
import wave
import math
import array
import shutil
import subprocess
import base64
import string
import time
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass
from typing import Optional, List, Set, Dict, Any, Tuple

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QSlider, QFileDialog, QTableView,
    QHeaderView, QComboBox, QAbstractItemView,
    QProgressBar, QDialog, QCheckBox, QSpinBox, QLineEdit,
    QFormLayout, QMessageBox, QStatusBar, QTabWidget,
    QListWidget, QListWidgetItem, QScrollArea, QFrame, QSplitter,
    QSizeGrip, QSplashScreen, QTextBrowser, QGraphicsDropShadowEffect,
    QMenu, QGridLayout, QGroupBox, QTextEdit, QPlainTextEdit
)
from PyQt6.QtCore import (
    Qt, QUrl, QEvent, QTimer, QPoint, QThread, pyqtSignal,
    QSettings, QFileSystemWatcher, QAbstractTableModel, QModelIndex,
    QAbstractNativeEventFilter, QRectF, QPointF, QSize
)
from PyQt6.QtGui import (
    QPixmap, QImage, QColor, QPainter, QFont, QMouseEvent,
    QDragEnterEvent, QDropEvent, QLinearGradient, QIcon, QPen, QPalette, QBrush,
    QPainterPath, QFontMetrics, QAction, QRadialGradient, QGuiApplication
)
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput, QMediaDevices

import mutagen
from mutagen.flac import FLAC
from mutagen.mp4 import MP4

APP_NAME = "Hox Music Player"
APP_VERSION = "0.4.3"
APP_FULL_TITLE = f"{APP_NAME} v{APP_VERSION}"

SUPPORTED_EXTENSIONS = {
    '.flac', '.wav', '.wave', '.mp3', '.ogg', '.m4a', '.aac', '.opus', '.wma', '.aiff'
}

_NATURAL_RE = re.compile(r'(\d+)')
_LEADING_NUM_RE = re.compile(r'^\s*(\d{1,4})[\s._\-)]+')
_LRC_TIME_RE = re.compile(r'\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]')
_LRC_WORD_RE = re.compile(r'<(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?>')


def natural_sort_key(text: str) -> Tuple:
    parts = _NATURAL_RE.split(text.casefold())
    return tuple(int(p) if p.isdigit() else p for p in parts)


class ClickableLabel(QLabel):
    """QLabel с сигналом clicked — для кликабельного имени исполнителя."""
    clicked = pyqtSignal()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


def create_hox_icon(size: int = 128) -> QIcon:
    """Генерирует фирменную векторную иконку Hox Music Player."""
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)

    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Неоновый скруглённый щит/квадрат
    grad = QLinearGradient(0, 0, size, size)
    grad.setColorAt(0.0, QColor("#1a1f35"))
    grad.setColorAt(0.5, QColor("#283593"))
    grad.setColorAt(1.0, QColor("#0d101d"))
    p.setBrush(grad)
    p.setPen(QPen(QColor("#00e5ff"), max(2, size // 32)))
    pad = size // 12
    p.drawRoundedRect(pad, pad, size - 2 * pad, size - 2 * pad, size * 0.22, size * 0.22)

    # Эквалайзерные столбики внутри иконки (стилизация буквы H и звуковой волны)
    bar_colors = ["#00e5ff", "#536dfe", "#ff4081", "#536dfe", "#00e5ff"]
    heights = [0.52, 0.28, 0.64, 0.28, 0.52]
    bar_w = size * 0.09
    gap = size * 0.045
    total_w = 5 * bar_w + 4 * gap
    start_x = (size - total_w) / 2.0

    p.setPen(Qt.PenStyle.NoPen)
    for i, (col, h_ratio) in enumerate(zip(bar_colors, heights)):
        bh = size * h_ratio
        bx = start_x + i * (bar_w + gap)
        by = (size - bh) / 2.0
        p.setBrush(QColor(col))
        p.drawRoundedRect(int(bx), int(by), int(bar_w), int(bh), 4, 4)

    # Горизонтальная перемычка буквы H
    p.setBrush(QColor("#00e5ff"))
    p.drawRoundedRect(int(start_x), int(size * 0.46), int(total_w), int(size * 0.08), 3, 3)

    p.end()
    return QIcon(pix)


def rounded_pixmap(pix: QPixmap, radius: int = 14) -> QPixmap:
    """Скругляет углы обложки под неоновую рамку (в стиле логотипа)."""
    if pix.isNull():
        return pix
    out = QPixmap(pix.size())
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    mask = QPainterPath()
    mask.addRoundedRect(0.0, 0.0, float(pix.width()), float(pix.height()),
                        float(radius), float(radius))
    painter.setClipPath(mask, Qt.ClipOperation.IntersectClip)
    painter.drawPixmap(0, 0, pix)
    painter.end()
    return out


def make_glow_halo(w: int, h: int, radius: int, color: QColor, blur: int) -> QPixmap:
    """Предрендеренное неоновое свечение (v0.2.0).

    Рисуется один раз и ставится статичной подложкой под обложку и кнопку
    Play вместо живого QGraphicsDropShadowEffect: эффект перерисовывается
    при каждом hover/resize/обновлении виджета и заметно грузит CPU на
    слабых ПК (именно из-за него «подвисала» система при работе плеера).
    """
    pad = max(8, blur // 2 + 4)
    pix = QPixmap(w + 2 * pad, h + 2 * pad)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(float(pad), float(pad), float(w), float(h),
                        float(radius), float(radius))
    steps = 12
    # Широкие полупрозрачные обводки снизу вверх: мягкий ореол как у blur-тени
    for i in range(steps, 0, -1):
        pen_w = 2.0 * pad * i / steps
        c = QColor(color)
        c.setAlphaF(color.alphaF() * (1.0 - i / (steps + 1.0)))
        p.setPen(QPen(c, pen_w))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
    p.end()
    return pix


def make_circle_glow(diameter: int, color: QColor, blur: int) -> QPixmap:
    """Мягкий круговой ореол (v0.2.0) — для круглой кнопки Play.

    Радиальный градиент даёт плавное затухание как у blur-тени, без
    «пончик»-артефактов накладывающихся обводок на маленьком круге.
    """
    pad = max(8, blur // 2 + 4)
    size = diameter + 2 * pad
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    half = size / 2.0
    g = QRadialGradient(half, half, half)
    r_edge = (diameter / 2.0) / half  # край кнопки в долях радиуса pixmap
    c_mid = QColor(color)
    c_mid.setAlphaF(color.alphaF() * 0.38)
    g.setColorAt(r_edge, color)
    g.setColorAt(min(1.0, r_edge + 0.05), c_mid)
    g.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(g)
    p.drawEllipse(0, 0, size, size)
    p.end()
    return pix


def make_icon(name: str, color: Optional[QColor] = None, pixmap_size: int = 48) -> QIcon:
    """Векторная иконка кнопки, рисуемая QPainter (v0.3.3).

    Эмодзи ⏮ ⏭ 🔁 🗕 🗖 ✕ ⚙ по-разному рендерятся на разных системах и
    шрифтах (а часть вообще не имеет глифа в шрифтах Windows 7), поэтому
    все иконки кнопок теперь рисуются сами: одноцветные, в цвете активной
    темы. «Перекраска» при смене темы — это просто повторная отрисовка с
    новым QColor (см. _apply_button_icons/_apply_top_icons); при
    переключении play/pause иконка меняется без пересоздания виджета.
    """
    if color is None:
        color = QColor(T["sub1"])
    pix = QPixmap(pixmap_size, pixmap_size)
    pix.fill(Qt.GlobalColor.transparent)

    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(color, max(1.6, pixmap_size * 0.085),
               Qt.PenStyle.SolidLine,
               Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    s = float(pixmap_size)
    m = s * 0.18

    if name == "play":
        tri = QPainterPath()
        tri.moveTo(m * 1.4, m)
        tri.lineTo(s - m * 1.4, s / 2)
        tri.lineTo(m * 1.4, s - m)
        tri.closeSubpath()
        p.setBrush(color)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPath(tri)

    elif name == "pause":
        bw, gap = s * 0.16, s * 0.16
        cx = s / 2
        p.setBrush(color)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(int(cx - gap / 2 - bw), int(m),
                          int(bw), int(s - 2 * m), 2, 2)
        p.drawRoundedRect(int(cx + gap / 2), int(m),
                          int(bw), int(s - 2 * m), 2, 2)

    elif name == "stop":
        p.setBrush(color)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(int(m * 1.25), int(m * 1.25),
                          int(s - 2 * m * 1.25), int(s - 2 * m * 1.25), 3, 3)

    elif name == "prev":
        p.setBrush(color)
        p.setPen(Qt.PenStyle.NoPen)
        bar_w = max(2, int(s * 0.10))
        p.drawRect(int(m * 1.2), int(m), bar_w, int(s - 2 * m))
        tri = QPainterPath()
        tri.moveTo(s - m * 1.2, m)
        tri.lineTo(m * 1.2 + bar_w + s * 0.06, s / 2)
        tri.lineTo(s - m * 1.2, s - m)
        tri.closeSubpath()
        p.drawPath(tri)

    elif name == "next":
        p.setBrush(color)
        p.setPen(Qt.PenStyle.NoPen)
        bar_w = max(2, int(s * 0.10))
        p.drawRect(int(s - m * 1.2 - bar_w), int(m), bar_w, int(s - 2 * m))
        tri = QPainterPath()
        tri.moveTo(m * 1.2, m)
        tri.lineTo(s - m * 1.2 - bar_w - s * 0.06, s / 2)
        tri.lineTo(m * 1.2, s - m)
        tri.closeSubpath()
        p.drawPath(tri)

    elif name in ("repeat", "repeat_one"):
        r = QRectF(m, m, s - 2 * m, s - 2 * m)
        p.drawArc(r, 45 * 16, 270 * 16)
        cx = s / 2 + (s / 2 - m) * math.cos(math.radians(45))
        cy = s / 2 - (s / 2 - m) * math.sin(math.radians(45))
        tip = QPainterPath()
        tip.moveTo(cx, cy)
        tip.lineTo(cx - s * 0.14, cy - s * 0.02)
        tip.lineTo(cx - s * 0.02, cy + s * 0.14)
        tip.closeSubpath()
        p.setBrush(color)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPath(tip)
        if name == "repeat_one":
            f = QFont()
            f.setPointSizeF(s * 0.20)
            f.setBold(True)
            p.setFont(f)
            p.setPen(color)
            p.drawText(QRectF(0, 0, s, s), Qt.AlignmentFlag.AlignCenter, "1")

    elif name == "menu":
        for y in (s * 0.32, s * 0.5, s * 0.68):
            p.drawLine(int(m), int(y), int(s - m), int(y))

    elif name == "maximize":
        p.drawRoundedRect(int(m * 1.2), int(m * 1.2),
                          int(s - 2 * m * 1.2), int(s - 2 * m * 1.2), 2, 2)

    elif name == "minimize":
        p.drawLine(int(m), int(s * 0.5), int(s - m), int(s * 0.5))

    elif name == "minimize_taskbar":
        p.drawRoundedRect(int(m * 1.2), int(m * 1.2),
                          int(s - 2 * m * 1.2), int(s * 0.55), 2, 2)
        p.drawLine(int(m * 0.9), int(s * 0.82), int(s - m * 0.9), int(s * 0.82))

    elif name == "close":
        p.drawLine(int(m * 1.2), int(m * 1.2), int(s - m * 1.2), int(s - m * 1.2))
        p.drawLine(int(s - m * 1.2), int(m * 1.2), int(m * 1.2), int(s - m * 1.2))

    elif name == "volume":
        sp = QPainterPath()
        sp.moveTo(m * 1.2, s * 0.35)
        sp.lineTo(s * 0.35, s * 0.35)
        sp.lineTo(s * 0.55, s * 0.18)
        sp.lineTo(s * 0.55, s * 0.82)
        sp.lineTo(s * 0.35, s * 0.65)
        sp.lineTo(m * 1.2, s * 0.65)
        sp.closeSubpath()
        p.setBrush(color)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPath(sp)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(pen)
        p.drawArc(QRectF(s * 0.5, s * 0.28, s * 0.38, s * 0.44), -50 * 16, 100 * 16)

    elif name == "headphones":
        p.drawArc(QRectF(m, m, s - 2 * m, s - 2 * m), 0, 180 * 16)
        p.setBrush(color)
        p.setPen(Qt.PenStyle.NoPen)
        cw = s * 0.16
        p.drawRoundedRect(int(m), int(s * 0.5), int(cw), int(s * 0.35), 3, 3)
        p.drawRoundedRect(int(s - m - cw), int(s * 0.5), int(cw), int(s * 0.35), 3, 3)

    elif name == "folder":
        f = QPainterPath()
        f.moveTo(m, s * 0.3)
        f.lineTo(s * 0.4, s * 0.3)
        f.lineTo(s * 0.48, s * 0.4)
        f.lineTo(s - m, s * 0.4)
        f.lineTo(s - m, s * 0.82)
        f.lineTo(m, s * 0.82)
        f.closeSubpath()
        p.drawPath(f)

    elif name == "file":
        f = QPainterPath()
        f.moveTo(s * 0.28, m)
        f.lineTo(s * 0.6, m)
        f.lineTo(s * 0.76, m + s * 0.16)
        f.lineTo(s * 0.76, s - m)
        f.lineTo(s * 0.28, s - m)
        f.closeSubpath()
        p.drawPath(f)
        p.drawLine(int(s * 0.6), int(m), int(s * 0.6), int(m + s * 0.16))
        p.drawLine(int(s * 0.6), int(m + s * 0.16), int(s * 0.76), int(m + s * 0.16))

    elif name == "refresh":
        p.drawArc(QRectF(m, m, s - 2 * m, s - 2 * m), 30 * 16, 300 * 16)
        cx = s / 2 + (s / 2 - m) * math.cos(math.radians(30))
        cy = s / 2 - (s / 2 - m) * math.sin(math.radians(30))
        tip = QPainterPath()
        tip.moveTo(cx, cy)
        tip.lineTo(cx - s * 0.13, cy - s * 0.05)
        tip.lineTo(cx - s * 0.01, cy + s * 0.13)
        tip.closeSubpath()
        p.setBrush(color)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPath(tip)

    elif name == "settings":
        cx, cy = s / 2, s / 2
        r_outer = (s - 2 * m) / 2
        r_inner = (s - 2 * m) * 0.55
        r_center = (s - 2 * m) * 0.33
        teeth = 8
        path = QPainterPath()
        for i in range(teeth * 2):
            ang = math.pi * i / teeth
            r = r_outer if i % 2 == 0 else r_inner
            x = cx + r * math.cos(ang)
            y = cy + r * math.sin(ang)
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        path.closeSubpath()
        p.setBrush(color)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPath(path)
        # Дырка по центру шестерёнки — цветом фона темы
        p.setBrush(QColor(T["bg"]))
        p.drawEllipse(QPointF(cx, cy), r_center, r_center)

    elif name == "album":
        # v0.3.4: векторный «диск» для карточек альбомов (замена эмодзи 💿)
        p.drawEllipse(QPointF(s / 2, s / 2), s * 0.35, s * 0.35)
        p.drawEllipse(QPointF(s / 2, s / 2), s * 0.12, s * 0.12)

    elif name == "sync":
        # v0.3.7: круговые стрелки для кнопки «Инструменты» /
        # пункта «Синхронизатор текста» (обновление/конвертация)
        cx, cy = s / 2, s / 2
        r = (s - 2 * m) / 2
        rect = QRectF(cx - r, cy - r, 2 * r, 2 * r)
        p.drawArc(rect, 15 * 16, 150 * 16)
        p.drawArc(rect, 195 * 16, 150 * 16)
        # Наконечники на концах дуг — по касательной, по ходу стрелки
        for a_deg in (165, 345):
            a = math.radians(a_deg)
            ex = cx + r * math.cos(a)
            ey = cy - r * math.sin(a)
            tx, ty = -math.sin(a), -math.cos(a)
            nx, ny = -ty, tx
            tri = QPainterPath()
            tri.moveTo(ex + tx * s * 0.11, ey + ty * s * 0.11)
            tri.lineTo(ex + nx * s * 0.055, ey + ny * s * 0.055)
            tri.lineTo(ex - nx * s * 0.055, ey - ny * s * 0.055)
            tri.closeSubpath()
            p.setBrush(color)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPath(tri)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(pen)

    else:
        p.drawEllipse(QPointF(s / 2, s / 2), s * 0.3, s * 0.3)

    p.end()
    return QIcon(pix)


# --- Тёмный заголовок окна (v0.1.9, Windows 10/11) -------------------------
DWMWA_USE_IMMERSIVE_DARK_MODE = 20   # тёмный режим заголовка (сборка 19041+)
DWMWA_CAPTION_COLOR = 35             # точный цвет заголовка/рамки (Windows 11)


def apply_dark_title_bar(widget: QWidget) -> None:
    """Окрашивает заголовок окна в цвет темы плеера (#10131f).

    Windows 11 — точный цвет шапки и рамки через DWMWA_CAPTION_COLOR;
    Windows 10 (18086+) — тёмный режим через DWMWA_USE_IMMERSIVE_DARK_MODE
    (атрибут 20, на старых сборках — 19). Windows 7/8 и другие ОС — тихий
    no-op: DwmSetWindowAttribute вернёт ошибку, которую мы игнорируем.
    Вызов идемпотентен — можно повторять при каждом показе окна.
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        hwnd = int(widget.winId())  # создаёт нативное окно при необходимости
        dwm = ctypes.windll.dwmapi
        # v0.3.0: цвет заголовка = фон активной темы (COLORREF 0x00BBGGRR)
        _cc = QColor(T["bg"])
        caption = ctypes.c_uint((_cc.blue() << 16) | (_cc.green() << 8) | _cc.red())
        dwm.DwmSetWindowAttribute(hwnd, DWMWA_CAPTION_COLOR,
                                  ctypes.byref(caption), ctypes.sizeof(caption))
        dark = ctypes.c_int(1)
        for attr in (DWMWA_USE_IMMERSIVE_DARK_MODE, 19):
            if dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(dark),
                                         ctypes.sizeof(dark)) == 0:
                break
    except Exception:
        # Косметика не должна мешать запуску плеера ни при каких условиях
        pass


def _audio_dev_label(dev) -> str:
    """Человекочитаемое имя аудиоустройства (Qt6: description, Qt5: deviceName)."""
    try:
        d = dev.description()
        if d:
            return str(d)
    except Exception:
        pass
    try:
        return str(dev.deviceName())
    except Exception:
        return "?"


def _audio_dev_key(dev) -> str:
    """Стабильный ключ устройства для запоминания выбора (id или имя)."""
    try:
        bid = bytes(dev.id())
        if bid:
            return "id:" + bid.decode("utf-8", "replace")
    except Exception:
        pass
    return "name:" + _audio_dev_label(dev)


# На Win7-сборке (fake-PyQt6 поверх Qt5) маршрут вывода всегда идёт на
# системное устройство по умолчанию — кнопку выбора аудиовыхода не показываем
_AUDIO_OUTPUT_SELECT = not getattr(QMediaDevices, "IS_QT5_SHIM", False)


@dataclass
class LyricLine:
    time_ms: int
    text: str
    words: Optional[List[Tuple[int, str]]] = None  # (time_ms, слово) — пословная подсветка v0.1.9


@dataclass
class TrackInfo:
    filepath: Path
    filename: str
    title: str
    artist: str
    album: str
    track_num: int
    folder_order_num: int
    mtime: float
    duration_sec: float
    folder_key: Tuple
    natural_file_key: Tuple
    filename_key: str
    title_key: str
    num_str: str
    date_str: str
    dur_str: str


def parse_track_number(raw_val) -> int:
    if not raw_val:
        return 999999
    val_str = str(raw_val[0]) if isinstance(raw_val, list) else str(raw_val)
    val_str = val_str.split('/')[0].strip()
    return int(val_str) if val_str.isdigit() else 999999


def extract_file_leading_number(filename: str) -> Optional[int]:
    m = _LEADING_NUM_RE.match(filename)
    return int(m.group(1)) if m else None


def read_metadata(filepath: Path, folder_index: int = 0) -> TrackInfo:
    filename = filepath.name
    title = filepath.stem
    artist = "Неизвестный исполнитель"
    album = filepath.parent.name or "Без альбома"
    track_num = 999999
    duration_sec = 0.0
    try:
        mtime = filepath.stat().st_mtime
    except OSError:
        mtime = 0.0

    try:
        audio = mutagen.File(filepath, easy=True)
        if audio is not None:
            if audio.info and hasattr(audio.info, 'length'):
                duration_sec = float(audio.info.length)
            if 'title' in audio and audio['title']:
                title = str(audio['title'][0]).strip() or title
            if 'artist' in audio and audio['artist']:
                artist = str(audio['artist'][0]).strip() or artist
            if 'album' in audio and audio['album']:
                album = str(audio['album'][0]).strip() or album
            if 'tracknumber' in audio:
                track_num = parse_track_number(audio['tracknumber'])
    except Exception:
        pass

    leading_num = extract_file_leading_number(filename)
    display_num = leading_num if leading_num is not None else (
        track_num if track_num != 999999 else folder_index
    )
    num_str = str(display_num) if display_num and display_num != 999999 else "-"

    date_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M") if mtime else "-"
    mins, secs = divmod(int(duration_sec), 60)
    dur_str = f"{mins:02d}:{secs:02d}"

    return TrackInfo(
        filepath=filepath,
        filename=filename,
        title=title,
        artist=artist,
        album=album,
        track_num=track_num,
        folder_order_num=folder_index,
        mtime=mtime,
        duration_sec=duration_sec,
        folder_key=natural_sort_key(str(filepath.parent)),
        natural_file_key=natural_sort_key(filename),
        filename_key=filename.casefold(),
        title_key=title.casefold(),
        num_str=num_str,
        date_str=date_str,
        dur_str=dur_str
    )


def extract_cover_bytes(filepath: Path) -> Optional[bytes]:
    try:
        ext = filepath.suffix.lower()
        if ext == '.flac':
            audio = FLAC(filepath)
            if audio.pictures:
                return audio.pictures[0].data
        elif ext in {'.m4a', '.mp4', '.aac'}:
            audio = MP4(filepath)
            covr = audio.tags.get('covr') if audio.tags else None
            if covr:
                return bytes(covr[0])

        audio_raw = mutagen.File(filepath)
        if audio_raw is not None and audio_raw.tags is not None:
            for key, tag in audio_raw.tags.items():
                if key.startswith('APIC') or hasattr(tag, 'data'):
                    if hasattr(tag, 'data') and isinstance(tag.data, bytes):
                        return tag.data
            if 'metadata_block_picture' in audio_raw.tags:
                b64_data = audio_raw.tags['metadata_block_picture'][0]
                raw_data = base64.b64decode(b64_data)
                pic = mutagen.flac.Picture(raw_data)
                return pic.data
    except Exception:
        pass

    folder = filepath.parent
    for cover_name in ('cover.jpg', 'cover.png', 'folder.jpg', 'folder.png', 'front.jpg'):
        candidate = folder / cover_name
        if candidate.is_file():
            try:
                return candidate.read_bytes()
            except Exception:
                pass
    return None


def _parse_word_timings(content: str, line_ms: int) -> Tuple[str, Optional[List[Tuple[int, str]]]]:
    """Разбирает пословные метки <mm:ss.xx> (расширенный формат LRC A2).

    Возвращает (чистый текст без меток, [(time_ms, слово)] | None).
    Каждый тег предшествует своему слову; текст перед первым тегом
    получает время начала строки.
    """
    tags = list(_LRC_WORD_RE.finditer(content))
    if not tags:
        return content.strip(), None
    words: List[Tuple[int, str]] = []
    head = content[:tags[0].start()].strip()
    if head:
        words.append((line_ms, head))
    for i, m in enumerate(tags):
        end = tags[i + 1].start() if i + 1 < len(tags) else len(content)
        chunk = content[m.end():end].strip()
        if not chunk:
            continue
        mm, ss = int(m.group(1)), int(m.group(2))
        frac = m.group(3) or "0"
        t_ms = (mm * 60 + ss) * 1000 + int(frac.ljust(3, "0")[:3])
        words.append((t_ms, chunk))
    if not words:
        return "", None
    return " ".join(w for _, w in words), words


def parse_lyrics_text(raw_text: str, duration_sec: float = 180.0) -> Tuple[List[LyricLine], bool]:
    """Разбирает LRC с таймкодами [mm:ss.xx] или обычный текст (распределяя по времени трека).

    Поддерживает расширенный формат A2 с пословными метками <mm:ss.xx>:
    [00:12.34]<00:12.40>Слово <00:12.80>второе — строки получают список words.
    """
    lines: List[LyricLine] = []
    has_lrc = False

    for raw_line in raw_text.splitlines():
        matches = list(_LRC_TIME_RE.finditer(raw_line))
        if matches:
            has_lrc = True
            clean_txt = _LRC_TIME_RE.sub("", raw_line).strip()
            for m in matches:
                mm = int(m.group(1))
                ss = int(m.group(2))
                frac = m.group(3) or "0"
                ms = int(frac.ljust(3, '0')[:3])
                total_ms = (mm * 60 + ss) * 1000 + ms
                txt, word_times = _parse_word_timings(clean_txt, total_ms)
                lines.append(LyricLine(total_ms, txt or "♪ ♪ ♪", word_times))

    if has_lrc and lines:
        lines.sort(key=lambda x: x.time_ms)
        return lines, True

    # Если таймкодов нет, равномерно распределяем строки по длительности трека для автопрокрутки
    plain_lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
    if not plain_lines:
        return [], False

    total_ms = max(10000, int(duration_sec * 1000))
    step = total_ms // max(1, len(plain_lines))
    for i, txt in enumerate(plain_lines):
        lines.append(LyricLine(i * step, txt))
    return lines, False


def extract_lyrics(filepath: Path, duration_sec: float) -> Tuple[List[LyricLine], bool]:
    """Ищет .lrc файл, встроенные теги текста песни (USLT/SYLT/LYRICS/©lyr) или .txt."""
    # 1. Внешний .lrc файл рядом с треком
    lrc_path = filepath.with_suffix('.lrc')
    if lrc_path.is_file():
        for enc in ('utf-8', 'utf-8-sig', 'cp1251', 'latin-1'):
            try:
                return parse_lyrics_text(lrc_path.read_text(encoding=enc), duration_sec)
            except Exception:
                pass

    # 2. Встроенные теги в самом аудиофайле
    try:
        audio = mutagen.File(filepath)
        if audio is not None and audio.tags is not None:
            tags_items = list(audio.tags.items())

            # v0.3.7, проход 1: TXXX:LYRICS_LRC — пословный enhanced LRC,
            # который пишет «Синхронизатор текста». Самый богатый формат,
            # поэтому проверяется первым независимо от порядка кадров.
            for k, v in tags_items:
                if k.upper() == 'TXXX:LYRICS_LRC' and hasattr(v, 'text'):
                    raw = v.text
                    if isinstance(raw, (list, tuple)):
                        raw = raw[0] if raw else ""
                    lines, is_sync = parse_lyrics_text(str(raw), duration_sec)
                    if lines:
                        return lines, is_sync

            # v0.3.7, проход 2: SYLT (построчный синхронный текст),
            # затем USLT / LYRICS — прежнее поведение.
            for k, v in tags_items:
                k_up = k.upper()
                if k_up.startswith('SYLT') and hasattr(v, 'text'):
                    sylt: List[LyricLine] = []
                    for item in (v.text or []):
                        try:
                            txt, t_ms = item
                        except Exception:
                            continue
                        sylt.append(LyricLine(int(t_ms), str(txt).strip() or "♪ ♪ ♪"))
                    if sylt:
                        sylt.sort(key=lambda x: x.time_ms)
                        return sylt, True
                if k_up.startswith('USLT') and hasattr(v, 'text'):
                    lines, is_sync = parse_lyrics_text(str(v.text), duration_sec)
                    if lines:
                        return lines, is_sync
                if k_up in ('LYRICS', 'UNSYNCEDLYRICS', '©LYR', '\xa9LYR'):
                    val = v[0] if isinstance(v, list) and v else v
                    lines, is_sync = parse_lyrics_text(str(val), duration_sec)
                    if lines:
                        return lines, is_sync
    except Exception:
        pass

    # 3. Внешний .txt файл рядом с треком
    txt_path = filepath.with_suffix('.txt')
    if txt_path.is_file():
        for enc in ('utf-8', 'cp1251'):
            try:
                return parse_lyrics_text(txt_path.read_text(encoding=enc), duration_sec)
            except Exception:
                pass

    return [], False


# --- Фоновый анализатор огибающей громкости для честного визуализатора в такт музыки ---
class EnvelopeWorker(QThread):
    envelope_ready = pyqtSignal(str, list)  # filepath_str, список амплитуд [0.0..1.0] с шагом 50 мс

    def __init__(self, filepath: Path):
        super().__init__()
        self.filepath = filepath

    def run(self):
        fpath_str = str(self.filepath)
        env: List[float] = []
        try:
            ext = self.filepath.suffix.lower()
            if ext in ('.wav', '.wave'):
                env = self._extract_wav_envelope(self.filepath)
            elif shutil.which("ffmpeg"):
                env = self._extract_ffmpeg_envelope(self.filepath)
        except Exception:
            env = []

        self.envelope_ready.emit(fpath_str, env)

    @staticmethod
    def _extract_wav_envelope(filepath: Path) -> List[float]:
        with wave.open(str(filepath), 'rb') as wf:
            rate = wf.getframerate()
            channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            if sampwidth != 2 or rate <= 0:
                return []
            chunk_frames = max(1, int(rate * 0.05))  # окно 50 мс (20 Гц)
            envelope: List[float] = []
            while True:
                raw = wf.readframes(chunk_frames)
                if not raw:
                    break
                samples = array.array('h', raw)
                if not samples:
                    break
                step = max(1, channels * 4)
                acc = sum(abs(samples[i]) for i in range(0, len(samples), step))
                count = (len(samples) + step - 1) // step
                rms = (acc / max(1, count)) / 16384.0
                envelope.append(min(1.0, rms))
            return envelope

    @staticmethod
    def _extract_ffmpeg_envelope(filepath: Path) -> List[float]:
        # Быстро декодируем моно-поток 1000 Гц 16-бит PCM для снятия точных битов музыки
        cmd = [
            "ffmpeg", "-v", "quiet", "-i", str(filepath),
            "-ac", "1", "-ar", "1000", "-f", "s16le", "-"
        ]
        CreationFlags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform.startswith("win") else 0
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=8, creationflags=CreationFlags)
        raw = proc.stdout
        if not raw:
            return []
        samples = array.array('h', raw[:len(raw) - (len(raw) % 2)])
        chunk = 50  # 50 сэмплов при 1000 Гц = 50 мс
        env: List[float] = []
        for i in range(0, len(samples), chunk):
            sub = samples[i:i + chunk]
            if not sub:
                break
            avg = sum(abs(x) for x in sub) / (len(sub) * 14000.0)
            env.append(min(1.0, avg))
        return env


# --- Виджет визуализатора под ползунком громкости ---
class AudioVisualizerWidget(QWidget):
    # v0.2.0: ~14 FPS вместо 25; v0.3.3: ~11 FPS — визуально так же плавно,
    # CPU на слабых ПК ещё ниже
    VIZ_INTERVAL_MS = 80  # v0.3.4: 12.5 FPS — визуализатор выглядит как эквалайзер

    def __init__(self, parent=None, num_bars: int = 24):
        super().__init__(parent)
        self.num_bars = num_bars
        self.levels = [0.05] * num_bars
        self.peaks = [0.05] * num_bars
        self.envelope: List[float] = []
        self.is_playing = False
        self.current_pos_ms = 0
        self.volume_factor = 0.7
        self.track_seed = 1
        self.setFixedHeight(60)  # v0.3.4: 60 px — «полноценный эквалайзер»
        # v0.2.0: весь фон закрашивается непрозрачным — без blend-композиции
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self._viz_active = True
        self._bar_grad: Optional[QLinearGradient] = None  # кэш градиента полос

        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self._tick)
        # v0.2.0: таймер стартует только когда виджет реально видим
        self.set_active(True)

    def set_active(self, active: bool):
        """Полное включение/выключение анимации (экономия CPU на слабых ПК)."""
        self._viz_active = bool(active)
        self._update_timer_state()

    def _update_timer_state(self):
        """Таймер нужен только когда виджет включён, видим И играет
        (v0.2.0 + v0.3.3: на паузе анимировать нечего — таймер спит)."""
        need = self._viz_active and self.isVisible() and self.is_playing
        if need and not self.anim_timer.isActive():
            self.anim_timer.start(self.VIZ_INTERVAL_MS)
        elif not need and self.anim_timer.isActive():
            self.anim_timer.stop()

    def showEvent(self, event):
        super().showEvent(event)
        self._update_timer_state()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._update_timer_state()

    def set_envelope(self, env: List[float], seed: int):
        self.envelope = env
        self.track_seed = max(1, seed)

    def set_playing(self, playing: bool):
        self.is_playing = playing
        # v0.3.3: Play после паузы мгновенно будит таймер (см. _update_timer_state)
        self._update_timer_state()

    def update_music_clock(self, pos_ms: int, volume: float):
        self.current_pos_ms = pos_ms
        self.volume_factor = max(0.15, min(1.0, volume))

    def _tick(self):
        # Не тратим CPU, пока виджет скрыт (главное окно в миниплеере и наоборот)
        if not self.isVisible():
            return
        if not self.is_playing:
            changed = False
            for i in range(self.num_bars):
                if self.levels[i] > 0.04 or self.peaks[i] > 0.04:
                    self.levels[i] = max(0.03, self.levels[i] * 0.80)
                    self.peaks[i] = max(0.03, self.peaks[i] - 0.03)
                    changed = True
            if changed:
                self.update()
            else:
                # v0.3.3: все столбики затихли — таймер усыпляем до следующего
                # Play (set_playing перезапустит его без задержки)
                self.anim_timer.stop()
            return

        self.current_pos_ms += self.anim_timer.interval() or self.VIZ_INTERVAL_MS
        idx = self.current_pos_ms // 50  # шаг 50 мс

        # Берём реальную амплитуду трека в текущей точке (или ритмическую огибающую)
        if self.envelope and 0 <= idx < len(self.envelope):
            base_energy = self.envelope[idx]
            prev_energy = self.envelope[max(0, idx - 1)]
            beat_punch = max(0.0, (base_energy - prev_energy) * 1.9)
        else:
            t = self.current_pos_ms / 1000.0
            bpm_freq = 2.1 + (self.track_seed % 5) * 0.15
            pulse = abs(math.sin(t * math.pi * bpm_freq)) ** 3
            sub = abs(math.cos(t * math.pi * bpm_freq * 0.5))
            base_energy = 0.25 + 0.55 * pulse + 0.2 * sub
            beat_punch = 0.25 * pulse

        t_sec = self.current_pos_ms / 1000.0
        for i in range(self.num_bars):
            # Частотное распределение (басы слева/в центре, высокие справа)
            freq_mod = abs(math.sin(t_sec * (3.0 + i * 0.55) + i * 0.7))
            band_weight = 1.15 - abs(i - self.num_bars * 0.35) / (self.num_bars * 0.9)
            target = (base_energy * 0.75 + beat_punch * 0.5 + freq_mod * 0.32) * band_weight * self.volume_factor
            target = max(0.05, min(1.0, target))

            # Плавная атака и спад столбика
            if target > self.levels[i]:
                self.levels[i] = self.levels[i] * 0.35 + target * 0.65
            else:
                self.levels[i] = self.levels[i] * 0.72 + target * 0.28

            if self.levels[i] >= self.peaks[i]:
                self.peaks[i] = self.levels[i]
            else:
                self.peaks[i] = max(self.levels[i], self.peaks[i] - 0.025)

        self.update()

    def refresh_theme(self):
        """v0.3.0: сброс кэша градиента полос при смене темы оформления."""
        self._bar_grad = None
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        p.fillRect(0, 0, w, h, QColor(T["viz_bg"]))
        p.setPen(QColor(T["viz_border"]))
        p.drawRoundedRect(0, 0, w - 1, h - 1, 8, 8)

        margin = 6
        avail_w = w - 2 * margin
        avail_h = h - 2 * margin
        step = avail_w / self.num_bars
        bar_w = max(4.0, step - 2.0)  # v0.3.4: столбики толще

        # v0.2.0: один общий вертикальный градиент в кэше вместо 24 новых
        # QLinearGradient на каждый кадр (было ~1200 аллокаций/с на два виджета)
        if self._bar_grad is None:
            grad = QLinearGradient(0.0, float(margin + avail_h), 0.0, float(margin))
            grad.setColorAt(0.0, QColor(T["bar1"]))
            grad.setColorAt(0.6, QColor(T["bar2"]))
            grad.setColorAt(1.0, QColor(T["bar3"]))
            self._bar_grad = grad

        for i in range(self.num_bars):
            lvl = self.levels[i]
            bh = max(3.0, lvl * avail_h)
            bx = margin + i * step
            by = margin + avail_h - bh

            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self._bar_grad)
            p.drawRoundedRect(int(bx), int(by), int(bar_w), int(bh), 3, 3)

            # Пиковая риска
            peak_y = margin + avail_h - max(3.0, self.peaks[i] * avail_h) - 2
            p.setBrush(QColor(T["peak"]))
            p.drawRect(int(bx), max(margin, int(peak_y)), int(bar_w), 2)

        p.end()


# --- Загрузочный экран Hox Music Player (v0.0.4, анимация v0.3.4) ---
class HoxSplashScreen(QSplashScreen):
    def __init__(self):
        # v0.3.4: pixmap живёт на инстансе — анимация перерисовывает его по таймеру
        self.pixmap = QPixmap(520, 300)
        self.pixmap.fill(Qt.GlobalColor.transparent)
        super().__init__(self.pixmap)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)

        # График анимации: 2 с пауза, затем 3 с плавное заполнение
        self.progress = 0.0
        self.elapsed_ms = 0
        self.delay_ms = 2000
        self.fill_ms = 3000
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(50)
        self._draw()

    def _tick(self):
        self.elapsed_ms += 50
        if self.elapsed_ms > self.delay_ms:
            self.progress = min(1.0, (self.elapsed_ms - self.delay_ms) / self.fill_ms)
            self._draw()
            if self.progress >= 1.0:
                self.timer.stop()

    def _draw(self):
        # v0.3.4-фикс: у QPainter нет метода clear() — код пользователя падал бы
        # при каждом запуске; прозрачную подложку делаем fill() до создания painter
        self.pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(self.pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        grad = QLinearGradient(0, 0, 520, 300)
        grad.setColorAt(0.0, QColor("#12131a"))
        grad.setColorAt(0.5, QColor("#1b1e2b"))
        grad.setColorAt(1.0, QColor("#0f1016"))
        painter.setBrush(grad)
        # v0.3.4: рамка/версия/полоса в цвете активной темы
        painter.setPen(QPen(QColor(T.get("sub1", "#00e5ff")), 2))
        painter.drawRoundedRect(2, 2, 516, 296, 16, 16)

        # Отрисовка фирменной иконки на сплеш-скрине
        icon_pix = create_hox_icon(76).pixmap(76, 76)
        painter.drawPixmap((520 - 76) // 2, 26, icon_pix)

        painter.setPen(QColor("#ffffff"))
        painter.setFont(QFont("Arial", 22, QFont.Weight.Bold))
        painter.drawText(0, 112, 520, 40, Qt.AlignmentFlag.AlignCenter, "HOX MUSIC PLAYER")

        painter.setPen(QColor(T.get("sub1", "#00e5ff")))
        painter.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        painter.drawText(0, 148, 520, 26, Qt.AlignmentFlag.AlignCenter, f"Версия {APP_VERSION}")

        painter.setPen(QColor("#9aa0b8"))
        painter.setFont(QFont("Arial", 9))
        painter.drawText(
            0, 178, 520, 26,
            Qt.AlignmentFlag.AlignCenter,
            "Hi-Res Audio Engine • Lyrics Sync • Live Visualizer"
        )

        painter.setBrush(QColor("#282c3e"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(80, 226, 360, 6, 3, 3)

        # v0.3.4: полоса прогресса заполняется плавно, а не стоит на месте
        if self.progress > 0:
            painter.setBrush(QColor(T.get("sub1", "#00e5ff")))
            bar_w = int(360 * self.progress)
            painter.drawRoundedRect(80, 226, bar_w, 6, 3, 3)

        painter.end()
        self.setPixmap(self.pixmap)

    def set_status(self, text: str):
        self.showMessage(
            text,
            Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter,
            QColor("#cfd4e8")
        )
        QApplication.processEvents()


# --- Виртуальная модель таблицы плейлиста со значком ▶ возле играющего трека ---
class PlaylistTableModel(QAbstractTableModel):
    HEADERS = ["№ в папке", "Название", "Исполнитель", "Альбом", "Имя файла", "Дата", "Длит."]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tracks: List[TrackInfo] = []
        self.playing_filepath: Optional[Path] = None
        self.is_playing_now: bool = False
        # v0.3.4: кэш QColor активной строки — data() вызывается тысячи раз,
        # создание QColor на каждый вызов зря грузит ЦП (сброс в refresh_colors)
        self._c_act_text = None
        self._c_act_bg = None

    def set_playing_state(self, filepath: Optional[Path], is_playing: bool):
        old_fp = self.playing_filepath
        self.playing_filepath = filepath
        self.is_playing_now = is_playing
        if self.tracks:
            for row, t in enumerate(self.tracks):
                if t.filepath == old_fp or t.filepath == filepath:
                    tl = self.index(row, 0)
                    br = self.index(row, len(self.HEADERS) - 1)
                    self.dataChanged.emit(tl, br)

    def refresh_colors(self):
        """v0.3.0: перерисовать все строки после смены темы
        (цвет выделения активного трека приходит из активной темы)."""
        # v0.3.4: пересоздать кэш цветов активной строки
        self._c_act_text = QColor(T["act_text"])
        self._c_act_bg = QColor(T["act_bg"])
        for row in range(len(self.tracks)):
            tl = self.index(row, 0)
            br = self.index(row, len(self.HEADERS) - 1)
            self.dataChanged.emit(tl, br)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.tracks)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            if 0 <= section < len(self.HEADERS):
                return self.HEADERS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        row = index.row()
        if not (0 <= row < len(self.tracks)):
            return None

        t = self.tracks[row]
        is_active = (self.playing_filepath is not None and t.filepath == self.playing_filepath)

        if role == Qt.ItemDataRole.DisplayRole:
            col = index.column()
            if col == 0:
                base_num = str(row + 1) if t.num_str == "-" else t.num_str
                if is_active:
                    icon = "▶" if self.is_playing_now else "⏸"
                    return f"{icon} {base_num}"
                return base_num
            elif col == 1:
                if is_active:
                    icon = "▶ " if self.is_playing_now else "⏸ "
                    return f"{icon}{t.title}"
                return t.title
            elif col == 2:
                return t.artist
            elif col == 3:
                return t.album
            elif col == 4:
                return t.filename
            elif col == 5:
                return t.date_str
            elif col == 6:
                return t.dur_str

        elif role == Qt.ItemDataRole.ForegroundRole and is_active:
            if self._c_act_text is None:  # v0.3.4: ленивый кэш QColor
                self._c_act_text = QColor(T["act_text"])
            return self._c_act_text

        elif role == Qt.ItemDataRole.BackgroundRole and is_active:
            if self._c_act_bg is None:
                self._c_act_bg = QColor(T["act_bg"])
            return self._c_act_bg

        elif role == Qt.ItemDataRole.FontRole and is_active:
            f = QFont()
            f.setBold(True)
            return f

        elif role == Qt.ItemDataRole.TextAlignmentRole:
            if index.column() in (0, 5, 6):
                return int(Qt.AlignmentFlag.AlignCenter)
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        return None

    def update_tracks(self, new_tracks: List[TrackInfo]):
        self.beginResetModel()
        self.tracks = new_tracks
        self.endResetModel()

    def sort_in_place(self, key_func, reverse: bool = False):
        self.layoutAboutToBeChanged.emit()
        self.tracks.sort(key=key_func, reverse=reverse)
        self.layoutChanged.emit()


class ScanWorker(QThread):
    progress = pyqtSignal(int, int, str)
    finished_scan = pyqtSignal(list, bool)

    def __init__(self, paths: List[Path], auto_play_first: bool = False):
        super().__init__()
        self.paths = paths
        self.auto_play_first = auto_play_first
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        file_list: List[Path] = []
        for p in self.paths:
            if self._cancelled:
                return
            try:
                resolved_p = p.resolve()
            except OSError:
                resolved_p = p
            if resolved_p.is_dir():
                try:
                    for sub in resolved_p.rglob("*"):
                        if self._cancelled:
                            return
                        if sub.is_file() and sub.suffix.lower() in SUPPORTED_EXTENSIONS:
                            file_list.append(sub)
                except OSError:
                    continue
            elif resolved_p.is_file() and resolved_p.suffix.lower() in SUPPORTED_EXTENSIONS:
                file_list.append(resolved_p)

        file_list.sort(key=lambda fp: (natural_sort_key(str(fp.parent)), natural_sort_key(fp.name)))

        total = len(file_list)
        results: List[TrackInfo] = []
        current_parent: Optional[Path] = None
        folder_counter = 0

        for idx, fpath in enumerate(file_list, start=1):
            if self._cancelled:
                break
            if fpath.parent != current_parent:
                current_parent = fpath.parent
                folder_counter = 1
            else:
                folder_counter += 1

            results.append(read_metadata(fpath, folder_index=folder_counter))
            if idx % 10 == 0 or idx == total:
                self.progress.emit(idx, total, fpath.name)

        if not self._cancelled:
            self.finished_scan.emit(results, self.auto_play_first)


class FolderCheckWorker(QThread):
    """Фоновая проверка папки на новые/удалённые треки.

    Раньше обход дерева каталогов выполнялся в GUI-потоке каждые N секунд
    и замораживал интерфейс (особенно миниплеер) на больших библиотеках.
    """
    checked = pyqtSignal(str, set)  # путь папки, множество текущих файлов на диске

    def __init__(self, folder: Path):
        super().__init__()
        self.folder = folder
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        found: Set[Path] = set()
        try:
            for p in self.folder.rglob("*"):
                if self._cancelled:
                    return
                try:
                    if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS:
                        found.add(p.resolve())
                except OSError:
                    continue
        except OSError:
            pass
        if not self._cancelled:
            self.checked.emit(str(self.folder), found)


def _preferred_handler_exe(python_exe: Path) -> Path:
    """Какой exe прописывать в ассоциациях файлов.

    В установленном плеере sys.executable — это Hox.exe (патченный pythonw,
    v0.1.3+): используем его как есть, чтобы «Открыть с помощью» показывал
    плеер, а не Python, и процесс оставался Hox.exe. При запуске из исходников
    с python.exe предпочитаем pythonw.exe (без консольного окна).
    """
    name = python_exe.name.lower()
    if name in ("hox.exe", "pythonw.exe"):
        return python_exe
    pythonw = python_exe.with_name("pythonw.exe")
    return pythonw if pythonw.exists() else python_exe


def register_as_default_player(parent: QWidget):
    script_path = Path(__file__).resolve()
    python_exe = Path(sys.executable).resolve()

    if sys.platform.startswith("win"):
        try:
            import winreg
            exe_to_use = _preferred_handler_exe(python_exe)
            prog_id = "HoxMusicPlayer.AudioFile"
            cmd_str = f'"{exe_to_use}" "{script_path}" "%1"'

            ico_path = script_path.with_name("hox.ico")
            icon_str = f'"{ico_path}",0' if ico_path.exists() else f'"{exe_to_use}",0'

            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, f"Software\\Classes\\{prog_id}") as key:
                winreg.SetValue(key, "", winreg.REG_SZ, APP_NAME)
                winreg.SetValueEx(key, "FriendlyTypeName", 0, winreg.REG_SZ, APP_NAME)
            with winreg.CreateKey(
                winreg.HKEY_CURRENT_USER, f"Software\\Classes\\{prog_id}\\DefaultIcon"
            ) as icon_key:
                winreg.SetValue(icon_key, "", winreg.REG_SZ, icon_str)
            with winreg.CreateKey(
                winreg.HKEY_CURRENT_USER, f"Software\\Classes\\{prog_id}\\shell\\open\\command"
            ) as cmd_key:
                winreg.SetValue(cmd_key, "", winreg.REG_SZ, cmd_str)

            for ext in SUPPORTED_EXTENSIONS:
                with winreg.CreateKey(
                    winreg.HKEY_CURRENT_USER, f"Software\\Classes\\{ext}\\OpenWithProgids"
                ) as ow_key:
                    winreg.SetValueEx(ow_key, prog_id, 0, winreg.REG_NONE, b"")

            subprocess.Popen(["cmd", "/c", "start", "ms-settings:defaultapps"], shell=False)
            QMessageBox.information(
                parent,
                "Интеграция с Windows",
                f"{APP_FULL_TITLE} зарегистрирован в системе!"
            )
        except Exception as e:
            QMessageBox.warning(parent, "Ошибка регистрации", f"Не удалось записать в реестр:\n{e}")

    elif sys.platform.startswith("linux"):
        try:
            apps_dir = Path.home() / ".local" / "share" / "applications"
            apps_dir.mkdir(parents=True, exist_ok=True)
            desktop_file = apps_dir / "hoxmusicplayer.desktop"
            mime_types = (
                "audio/flac;audio/x-flac;audio/wav;audio/x-wav;audio/mpeg;"
                "audio/ogg;audio/mp4;audio/aac;audio/opus;audio/x-ms-wma;audio/aiff;"
            )
            content = (
                "[Desktop Entry]\n"
                "Type=Application\n"
                f"Name={APP_NAME}\n"
                f'Exec="{python_exe}" "{script_path}" %F\n'
                "Icon=multimedia-volume-control\n"
                "Terminal=false\n"
                "Categories=AudioVideo;Audio;Player;\n"
                f"MimeType={mime_types}\n"
            )
            desktop_file.write_text(content, encoding="utf-8")
            for mime in mime_types.strip(";").split(";"):
                subprocess.run(["xdg-mime", "default", "hoxmusicplayer.desktop", mime], check=False)
            QMessageBox.information(
                parent, "Интеграция с Linux", f"Ярлык {desktop_file} назначен по умолчанию."
            )
        except Exception as e:
            QMessageBox.warning(parent, "Ошибка", f"Не удалось зарегистрировать:\n{e}")
    else:
        QMessageBox.information(
            parent, "Ассоциация файлов", f"Команда запуска:\n{python_exe} {script_path}"
        )


# --- Список изменений по версиям (для окна «Что нового») ---
CHANGELOG: List[Tuple[str, List[str]]] = [
    ("0.4.3", [
        "Лаунчер 3.3: исправлена ошибка v0.4.2, из-за которой установка не запускалась после успешного скачивания архива плеера — лаунчер оставался в режиме «занят» с неактивными кнопками, а журнал обрывался на строке «Скачано».",
        "Цепочка обновления теперь работает целиком: «Обновить / Переустановить» -> скачивание Hox-Player-*.zip -> проверка SHA-256 -> распаковка в папку app -> манифест целостности -> «Установка завершена».",
        "Архив плеера пересобран (Hox-Player-0.4.3.zip): состав прежний — hox_music_player.py + hox.ico; сам плеер без функциональных изменений: караоке, синхронизатор текста, темы и все возможности предыдущих версий полностью на месте.",
    ]),
    ("0.4.2", [
        "Лаунчер 3.2: окно автоматически подстраивается под разрешение экрана — на 1366x768 и меньше панель установки с кнопкой «Обновить» и прогресс-баром гарантированно видна целиком, ничего не уезжает за край экрана.",
        "На компактных экранах уменьшаются только гибкие блоки (новости, журнал, скриншоты); на обычных разрешениях окно выглядит как раньше; на совсем маленьких включается внешняя прокрутка.",
        "Сам плеер без функциональных изменений: караоке, синхронизатор текста, темы и все возможности предыдущих версий полностью на месте.",
    ]),
    ("0.4.1", [
        "Лаунчер 3.1: при первом запуске больше ничего не скачивается сам — лаунчер проверяет наличие и целостность файлов плеера и пишет «Требуется обновление»; установка начинается только по нажатию кнопки «Обновить / Переустановить».",
        "Проверка целостности при каждом запуске: SHA-256 файлов плеера сверяется с манифестом установки (player_files.json) — отсутствующие или повреждённые файлы чинятся переустановкой в один клик.",
        "Кнопка «Обновить / Переустановить» всегда активна: скачивание лёгкого архива плеера Hox-Player-*.zip с GitHub Releases с прогрессом и проверкой SHA-256; добавлены повторная попытка скачивания и запасной TLS-режим для старых систем (Windows 7).",
        "Сам плеер без функциональных изменений: караоке, синхронизатор текста, темы и все возможности предыдущих версий полностью на месте.",
    ]),
    ("0.4.0", [
        "Новая схема распространения «лаунчер отдельно — плеер отдельно»: установщик Hox-Setup-*.exe ставит только лаунчер, а плеер лаунчер скачивает с GitHub Releases отдельным лёгким архивом Hox-Player-<версия>.zip и распаковывает в папку app.",
        "Если плеер ещё не установлен, лаунчер при запуске автоматически скачает и установит его (проверка SHA-256 перед распаковкой) и запустит одной кнопкой; при выходе новой версии предлагается обновление в один клик.",
        "Ярлык «Hox» на рабочем столе теперь открывает лаунчер — он проверяет обновления при каждом запуске; прямой ярлык «Hox Music Player» в меню «Пуск» запускает плеера напрямую, как и раньше.",
        "Сам плеер без функциональных изменений: синхронизатор текста, караоке, темы и все возможности предыдущих версий полностью на месте.",
    ]),
    ("0.3.9", [
        "Автообновление заработало сразу после установки: адрес официального GitHub-репозитория (Dima-Xox/Hox-Music-Player) вшит в лаунчер — настраивать launcher.ini вручную больше не нужно.",
        "Лаунчер обновлён до версии 2.1: проверка обновлений сразу смотрит на GitHub Releases официального репозитория, версия и заметки о релизе берутся оттуда же; строка [updates] github_repo в launcher.ini по-прежнему перекрывает вшитый адрес (для форков и зеркал).",
        "Плеер без функциональных изменений: лаунчер-установщик, новости и галерея скриншотов из v0.3.8 и синхронизатор текста из v0.3.7 полностью на месте.",
    ]),
    ("0.3.8", [
        "Лаунчер обновлён до версии 2.0: установка плеера происходит прямо в окне лаунчера — с прогресс-баром, выбором папки и созданием ярлыков на рабочем столе и в меню «Пуск».",
        "Проверка обновлений выполняется при запуске лаунчера (на время проверки кнопка запуска плеера заблокирована) и по кнопке «Проверить обновления» в любой момент; рядом с прогресс-баром появилась кнопка «Открыть плеер».",
        "Обновления берутся с GitHub Releases — всегда актуальная версия (репозиторий указывается в launcher.ini, см. README); скачивание идёт с прогресс-баром и проверкой SHA-256, старый JSON-манифест тоже поддерживается.",
        "В лаунчер встроены новости о новых версиях и галерея скриншотов; добавлена подпись «Ранняя бета».",
        "Лаунчер работает автономно: без интернета он установит плеер из установщика Hox-Setup.exe, лежащего рядом, или из собственной копии файлов; деинсталлятор зарегистрирован и для установок, сделанных самим лаунчером.",
    ]),
    ("0.3.7", [
        "Новый раздел «Инструменты → Синхронизатор текста»: отдельное окно создаёт синхронизированный текст песни для трека — по готовому тексту (.txt/.lrc) выравниванием по вокалу или автопознаванием речи (Whisper) с пословными метками времени (enhanced LRC A2).",
        "Как это работает: вокал отделяется от музыки (Demucs), трек распознаётся по 30-секундным фрагментам с перекрытием и склейкой дублей, результат — LRC построчно и пословно; перед записью показывается предпросмотр, итог сохраняется в копию файла *_synced — оригинал не меняется.",
        "Запись в теги: MP3/WAV получают SYLT + TXXX:LYRICS_LRC, FLAC — LYRICS/LYRICS_LINE; плеер теперь читает эти теги, поэтому обработанный трек сразу играет с караоке-подсветкой и пословной заливкой — без сторонних .lrc файлов.",
        "Окно синхронизатора сразу показывает, какие компоненты найдены (FFmpeg, Demucs, Whisper, lyrics-transcriber, mutagen) и подсказывает команду установки; нехватка внешних компонентов не мешает остальным функциям плеера.",
        "Обработка идёт в фоне и не блокирует плеер: музыку можно слушать, пока синхронизатор работает; лог процесса, drag&drop файлов в поля и автоподстановка текущего трека плеера.",
    ]),
    ("0.3.6", [
        "Вкладка «Текст песни» убрана — весь текст песни теперь живёт на панели управления плеером, рядом с обложкой, кнопками и ползунком.",
        "Стриминговая синхронизация перенесена в караоке-блок: активная строка подсвечивается цветом темы сразу и заливается слева направо (пословно по меткам LRC или по длине строки), строки плавно скользят вверх при смене куплета.",
        "Клик по строке — мгновенная перемотка; колесо мыши — ручная прокрутка текста, автопрокрутка сама подхватывается через 3 секунды (как в Spotify).",
        "Правый клик по караоке — меню текста песни: загрузить .lrc/.txt вручную, сдвиг синхронизации ±0.5 с и сброс (кнопки переехали из убранной вкладки).",
        "Таймер песни — штатный, на панели управления: время, ползунок и длительность под текстом двигаются ровным ходом таймера 100 мс; в развёрнутом окне строки караоке крупнее.",
    ]),
    ("0.3.5", [
        "Вкладка «Текст песни» полностью переработана в стиле стриминговых сервисов: крупные строки, активная выделена цветом темы и плавно держится на уровне чуть выше центра, спетые — приглушены, будущие — читаемо-тусклые.",
        "Таймер песни: под текстом теперь текущее время, ползунок прогресса и длительность — обновляются в реальном времени, перемотка перетаскиванием ползунка.",
        "Плавная синхронизация: внутренний таймер 100 мс ведёт текст и время ровным ходом, даже если системный сигнал позиции приходит редко; активная строка заливается слева направо — пословно (по меткам LRC A2) или по длине строки.",
        "Колесо мыши — ручная прокрутка текста, как в Spotify: автопрокрутка сама подхватывается через 3 секунды; клик по строке — мгновенная перемотка.",
        "Честный режим для текста без таймингов: строки показываются аккуратным статичным списком без ложной подсветки «не в такт».",
    ]),
    ("0.3.4", [
        "Глобальное обновление: увеличена высота визуализатора (60 px), столбики стали толще — теперь он выглядит как полноценный эквалайзер.",
        "Все оставшиеся эмодзи в интерфейсе заменены на векторные иконки (вкладки, карточки альбомов, тулбар, таблица плейлиста).",
        "Умный таймер в караоке: «Мини-ИИ» анализирует тишину в треке (через огибающую аудио). Если начинается проигрыш и до вокала далеко — включается обратный отсчёт.",
        "Вкладка «Текст песни» обзавелась кнопками сдвига синхронизации [-0.5с] и [+0.5с] для быстрой подстройки LRC-файла на лету.",
        "Анимированный экран приветствия: прогресс-бар стоит 2 секунды, а затем плавно заполняется за 3 секунды.",
        "Снижена нагрузка на ЦП: кэширование цветов в таблице плейлиста, остановка таймеров в фоне и оптимизация отрисовки.",
        "Исправлена ошибка запуска анимированного приветствия (QPainter.clear не существует) — присланный код падал при старте.",
    ]),
    ("0.3.3", [
        "Векторные иконки кнопок вместо эмодзи: play/pause, prev/next, стоп, повтор, свернуть/развернуть, закрыть, папка/файл/настройки/наушники — все рисуются QPainter в цвете активной темы. Одинаково выглядят на всех системах и шрифтах; перекрашиваются при смене темы и при переключении play/pause без пересоздания виджетов.",
        "Сортировка больше не дёргает таблицу: сохраняются прокрутка и текущее выделение, миниплеер синхронизируется отложенно и только когда он виден.",
        "Разворачивание на весь экран без дёрганья: перестройка левой колонки и вписывание окна в рабочую область выполняются по таймеру-дебаунсу (40–60 мс) — несколько resizeEvent подряд от Qt схлопываются в одну перестройку.",
        "Визуализатор спектра снизил частоту до ~11 FPS (было 14) и автоматически усыпляет таймер, когда на паузе все столбики затихли, — CPU простаивает, пока музыка не играет; по нажатию Play анимация возобновляется без задержки.",
    ]),
    ("0.3.2", [
        "Страницы «Исполнители и альбомы» и «Текст песни» теперь окрашиваются активной темой: альбомные карточки, списки треков альбома, вкладка текста песни, статистика исполнителя и деталь-панель альбомов больше не остаются в старой сине-циановой палитре (а деталь-панель и вовсе была светлой) при выборе Midnight Ember, Закат-ретро, Изумруда и других тем.",
        "Исправлено сравнение версий в окне «Что нового»: теперь числовое — версия 0.3.10 корректно считается новее 0.3.9 (раньше сравнение шло по строкам). Спасибо за присланное исправление — оно вошло в этот релиз.",
        "Техническое: жёстко зашитые inline-цвета этих страниц перенесены в общий шаблон тем (AlbumCard, AlbumTrackList, LyricsList и др.) — стили больше не дублируются и меняются вместе с темой; вёрстка и поведение не изменились.",
    ]),
    ("0.3.1", [
        "Новый скин «Midnight Ember» по вашему референсу: глубокий тёплый уголь вместо синевы, янтарно-оранжевые акценты — кнопка Play, активная строка плейлиста, вкладки и караоке подсвечены янтарём; лёгкое тёплое свечение фона.",
        "Скин выбирается в ⚙ Настройках → «Тема оформления» (теперь 5 тем: Aurora Neon, Midnight Ember, Hox Neon, Закат-ретро, Изумруд), применяется мгновенно с живым предпросмотром и запоминается между запусками.",
        "Перекраска затрагивает всё: плейлист, караоке-блок, визуализатор спектра, свечения обложки и Play, миниплеер и заголовок окна; вёрстка и адаптивное сжатие v0.2.1–0.2.2 не изменились. Заодно исправлена давняя мелочь: inline-прозрачность контейнера перекрывала градиент кнопки Play — теперь во всех темах кнопка ярко-акцентная с тёмным символом, как в референсе.",
    ]),
    ("0.3.0", [
        "Новый дизайн «Aurora Neon»: аврора-подсветка фона, полупрозрачные «стеклянные» панели, градиентные акценты и мягкие свечения — по концепции-макету.",
        "Темы оформления переключаются в ⚙ Настройках и применяются мгновенно: Aurora Neon (новая), Hox Neon (классика), Закат-ретро и Изумруд; выбор сохраняется между запусками.",
        "С темой перекрашивается всё: плейлист, караоке-блок, визуализатор спектра, свечение обложки и кнопки Play, миниплеер и заголовок окна; фоновая аврора рисуется из кэша — на производительность не влияет.",
    ]),
    ("0.2.2", [
        "Оконный режим: текст песни и визуализатор сжаты так же, как при развёрнутом окне — в обычном окне караоке-блок сразу компактный (56 px вместо 112), спектр — 36 px вместо 78; при развёрнутом окне размеры прежние (112/78).",
        "Исправлено: нижняя часть плеера больше не уходит за панель задач. Окно при запуске, перетаскивании и изменении размера автоматически вписывается в рабочую область экрана — рамка сужается и подтягивается так, чтобы весь плеер оставался видимым.",
        "Минимальная высота окна больше не превышает свободную высоту экрана: на ноутбуках 1366x768 и экранах с масштабом 125% окно можно ужать до полностью видимого состояния; пределы адаптивного сжатия глубже (караоке до 40, визуализатор до 24, обложка до 104 px, интервалы до 2 px).",
    ]),
    ("0.2.1", [
        "Исправлено: в оконном режиме (когда окно не развёрнуто на весь экран) нижняя часть левой панели — ползунок громкости и визуализатор — обрезалась нижним краем окна. Левая колонка теперь адаптивная: при нехватке высоты последовательно ужимаются караоке-блок (112→60 px), визуализатор (78→44 px) и обложка (192→128 px вместе со свечением) — все элементы остаются видимыми при любом размере окна.",
        "Окно больше нельзя растянуть меньше, чем влезает интерфейс: минимальный размер вычисляется из полностью сжатой компоновки, обрезание элементов исключено в принципе.",
        "При развёрнутом окне и на больших мониторах вид ровно тот же, что в v0.2.0: обложка 192 px со свечением, полный караоке-блок и визуализатор.",
    ]),
    ("0.2.0", [
        "Главное — производительность: устранены подтормаживания системы во время работы плеера. Живые неоновые свечения обложки и кнопки Play заменены на мгновенно отрисованные подложки — раньше эффект пересчитывался при каждом наведении, нажатии и обновлении интерфейса, заметно грузя процессор на слабых ПК.",
        "Визуализатор спектра стал вдвое экономнее: ~14 кадров/с вместо 25 (визуально то же самое), общий градиент полос кэшируется вместо создания 24 новых на каждый кадр, а таймер анимации автоматически останавливается, когда виджет скрыт (например, при сворачивании в миниплеер).",
        "Караоке-блок оптимизирован: таймер анимации теперь работает только при воспроизведении синхронизированного текста и видимом виджете; расписание пословной подсветки и шрифты кэшируются вместо пересчёта на каждом кадре — плавность заливки слов сохранена.",
        "Ползунок громкости больше не пишет настройки на диск при каждом движении: сохранение откладывается на 0,6 с после последнего изменения — раньше перетаскивание ползунка вызывало десятки дисковых записей подряд.",
        "Внешний вид не изменился: та же неоновая тема, свечение обложки и кнопки Play выглядят как прежде.",
    ]),
    ("0.1.9", [
        "Тёмный заголовок окна: шапка и рамка главного окна и миниплеера окрашены в цвет темы плеера — на Windows 11 точный цвет #10131f, на Windows 10 включается тёмный режим заголовка.",
        "Караоке «слово за словом»: в активной строке спетые слова ярко-циановые, текущее слово плавно заливается слева направо в такт музыке. Метки <мм:ss.xx> из расширенных LRC берутся напрямую; для обычных LRC слова равномерно распределяются по длительности строки.",
        "Ручной выбор аудиовыхода: кнопка 🎧 у ползунка громкости открывает список устройств (наушники, Bluetooth-колонка, динамики). Выбор запоминается и автоматически возвращается при переподключении устройства; пункт «Авто» возвращает режим следования за системным выходом.",
    ]),
    ("0.1.8", [
        "Обновлён дизайн в стиле логотипа: тёмная неоновая тема главного окна и миниплеера — градиентные панели, неоновые слайдеры с градиентом циан→индиго, круглая светящаяся кнопка Play, обложка в циановой рамке со свечением, тёмный плейлист с подсветкой играющего трека, вкладки с циановым подчёркиванием.",
        "Караоке-текст на главном экране: блок текста песни теперь расположен под именем исполнителя — предыдущая, активная (циановая) и следующая строки, синхронизация с музыкой по таймкодам LRC.",
        "Обратный отсчёт до начала вокала: во время вступлений и пауз между куплетами караоке-блок показывает «♪ вокал через N с», а за 3 секунды — крупные цифры 3-2-1 перед строкой (для синхронизированных LRC-текстов).",
    ]),
    ("0.1.7", [
        "Поддержка Windows 7 SP1: появилась отдельная сборка Hox-Setup-Win7.exe (Python 3.8 + Qt 5.15) — устанавливайте именно её на Windows 7, обычный установщик — на Windows 8.1/10/11.",
        "Один и тот же исходный код для всех версий Windows: новый слой совместимости подменяет PyQt6 на PyQt5 только в Win7-сборке, логика плеера не менялась.",
        "Windows 7: при установке автоматически проверяется наличие системных библиотек (Universal CRT) и предлагается их установка из комплекта, если их нет.",
        "Windows XP и Vista не поддерживаются: их предел — Python 3.4/3.7 и Qt 5.5–5.8, на которых плеер технически не работает.",
    ]),
    ("0.1.6", [
        "Исправлено: в меню «Открыть с помощью» плеер теперь отображается как «Hox Music Player» с логотипом, а не «Python» — установщик регистрирует плеер для всех поддерживаемых форматов (mp3, flac, ogg, wav, m4a, aac, opus, wma, aiff).",
        "Из списка «Открыть с помощью» для аудиофайлов убраны устаревшие записи «Python», оставшиеся от старых версий плеера, запускавшихся через Python.",
        "Внутренняя регистрация плеера из настроек тоже теперь использует Hox.exe — и в меню, и в Диспетчере задач остаётся наш процесс с логотипом, без возврата к Python.",
    ]),
    ("0.1.5", [
        "Мультимедиа-клавиши клавиатуры (Play/Pause, Стоп, Предыдущий/Следующий трек) управляют плеером глобально — даже когда он не в фокусе: в других программах, играх, полноэкранном режиме.",
        "В Настройках появился переключатель «Держать миниплеер поверх всех окон» (по умолчанию выключен).",
        "Окно «Что нового» теперь показывает полный список изменений всех версий — самая новая вверху; подсказка напоминает, что список можно открыть заново через Настройки.",
    ]),
    ("0.1.4", [
        "Миниплеер больше не держится поверх всех окон: при переключении на другую программу он уходит на задний план вместе с плеером и не мешает работе.",
    ]),
    ("0.1.3", [
        "Процесс плеера теперь называется Hox.exe и имеет логотип плеера вместо стандартной питоновской иконки — в Диспетчере задач и Проводнике отображается «Hox Music Player».",
        "Установщик удаляет папку старой установки (AppData\\Local\\Hox), чтобы не оставлять два дублирующихся экземпляра плеера.",
    ]),
    ("0.1.2", [
        "Исправлен баг: при закрытии плеера из миниплеера окна исчезали, но процесс оставался висеть в Диспетчере задач и музыка продолжала играть. Теперь плеер завершается полностью.",
    ]),
    ("0.1.1", [
        "Установщик и лаунчер теперь сами надёжно закрывают запущенный плеер перед обновлением — обновление больше не срывается с ошибкой «Невозможно открыть файл для записи».",
        "Если что-то всё же держит файлы, установщик покажет понятное окно с кнопкой «Повторить» вместо сбоя посреди установки.",
    ]),
    ("0.1.0", [
        "Плеер теперь мгновенно переключается на наушники, Bluetooth-колонку и другие внешние аудиоустройства прямо во время воспроизведения — без перезапуска.",
        "При отключении внешнего устройства (например, выдернули наушники) звук автоматически возвращается на прежний выход.",
    ]),
    ("0.0.9", [
        "Сворачивание главного окна теперь по умолчанию работает обычным образом — окно отправляется в панель задач.",
        "Авто-открытие миниплеера при сворачивании включается галочкой «Открывать миниплеер при сворачивании окна» в Настройках.",
        "Подсказки при первом использовании функций объясняют, где включить или выключить ту или иную возможность.",
        "У подсказок есть кнопка «Больше не показывать», а в Настройках — общий переключатель подсказок.",
        "При запуске новой версии открывается окно «Что нового»; в Настройках появилась кнопка для его повторного просмотра.",
    ]),
    ("0.0.8", [
        "Исправлено: сворачивание развёрнутого миниплеера возвращало его схлопнувшимся — теперь размер, позиция и открытая панель плейлиста сохраняются.",
    ]),
    ("0.0.7", [
        "В миниплеер добавлена кнопка «🗕» — сворачивание в панель задач; клик по кнопке панели задач возвращает миниплеер на экран.",
    ]),
    ("0.0.6", [
        "Плеер запоминает трек и позицию воспроизведения — после запуска песня продолжается с того же места.",
        "Если закрыть плеер в миниплеере, при следующем запуске он откроется сразу в миниплеере.",
        "Визуализатор спектра можно отключать в настройках (режим для слабых ПК).",
        "Миниплеер: добавлены визуализатор, кнопка закрытия и изменение размера мышью.",
        "Клик по имени исполнителя открывает его страницу (работает и в миниплеере).",
        "Исправлены зависания миниплеера на больших библиотеках.",
    ]),
    ("0.0.5", [
        "Восстановлен парсер таймкодов .lrc — синхронизированный караоке-текст снова работает.",
        "Исправлены аварийные завершения при быстром переключении треков и при закрытии плеера.",
        "Сканирование папки больше не падает, если файлы исчезают во время проверки.",
    ]),
    ("0.0.4", [
        "Базовая версия: библиотека музыки со сканированием папок и перетаскиванием файлов.",
        "Караоке-текст с синхронизацией (.lrc и теги), страница «Исполнители и альбомы».",
        "Визуализатор спектра, миниплеер, поиск, сортировка и повтор трека.",
    ]),
]


def _ver_tuple(v: str) -> tuple:
    """Числовое представление версии для корректного сравнения
    (0.3.10 > 0.3.9). Исправление пользователя (v0.3.2): раньше
    сравнение шло по строкам и «0.3.10» считалась СТАРЕШЕ «0.3.9»."""
    try:
        return tuple(int(p) for p in v.split("."))
    except ValueError:
        return (0,)


def changelog_html(since_version: str = "") -> str:
    """Собирает HTML списка изменений: все версии новее since_version
    (пустая строка — показать всё, с 0.0.4 до текущей версии)."""
    sections = []
    since = _ver_tuple(since_version) if since_version else None
    for ver, items in CHANGELOG:
        if since is not None and _ver_tuple(ver) <= since:
            continue
        rows = "".join(f"<li>{item}</li>" for item in items)
        sections.append(
            f"<h3 style='color:{T['sub1']};'>Версия {ver}</h3>"
            f"<ul style='margin-top:0px;'>{rows}</ul>"
        )
    if not sections:
        sections.append("<p>У вас самая свежая версия — новых изменений нет.</p>")
    return "".join(sections)


class WhatsNewDialog(QDialog):
    """Диалог «Что нового»: список изменений по версиям."""

    def __init__(self, html: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        # Аналогично подсказкам: диалог может быть открыт, когда главное окно
        # скрыто (запуск сразу в миниплеере) — его закрытие не должно гасить
        # приложение через quitOnLastWindowClosed
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.setWindowTitle(f"{APP_FULL_TITLE} — Что нового")
        self.setMinimumSize(620, 500)
        # v0.3.0: цвета диалога — из активной темы
        self.setStyleSheet(f"""
            QDialog {{ background-color: {T['wn_bg']}; }}
            QLabel#WhatsNewTitle {{ color: {T['sub1']}; font-size: 15px; font-weight: bold; }}
            QLabel#WhatsNewHint {{ color: {T['muted']}; font-size: 11px; }}
            QTextBrowser {{
                background-color: {T['wn_browser_bg']}; border: 1px solid {T['wn_browser_border']};
                border-radius: 6px; padding: 6px;
            }}
            QPushButton {{
                background-color: {T['btn_bg']}; color: {T['btn_text']};
                border: 1px solid {T['btn_border']}; border-radius: 5px; padding: 6px 16px;
            }}
            QPushButton:hover {{ border-color: {T['sub1']}; }}
        """)
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        title = QLabel(f"🎉 {APP_NAME} — что нового")
        title.setObjectName("WhatsNewTitle")
        layout.addWidget(title)
        hint = QLabel("(Список изменений можно открыть ещё раз: Настройки → кнопка «Что нового»)")
        hint.setObjectName("WhatsNewHint")
        layout.addWidget(hint)
        browser = QTextBrowser()
        pal = browser.palette()
        pal.setColor(QPalette.ColorRole.Text, QColor(T["text"]))
        browser.setPalette(pal)
        browser.setHtml(html)
        layout.addWidget(browser, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        close_btn = QPushButton("Понятно")
        close_btn.setFixedHeight(30)
        close_btn.clicked.connect(self.accept)
        row.addWidget(close_btn)
        layout.addLayout(row)


class SettingsDialog(QDialog):
    def __init__(self, main_win: "MediaPlayerWindow"):
        super().__init__(main_win)
        self.main_win = main_win
        self.setWindowTitle(f"⚙ Настройки — {APP_FULL_TITLE}")
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        folder_row = QHBoxLayout()
        self.folder_edit = QLineEdit(self.main_win.watched_folder or "")
        btn_browse = QPushButton("Обзор...")
        btn_browse.clicked.connect(self._browse_folder)
        folder_row.addWidget(self.folder_edit, 1)
        folder_row.addWidget(btn_browse)
        form.addRow("Отслеживаемая папка музыки:", folder_row)

        self.chk_auto_watch = QCheckBox("Автоматически проверять папку на новые треки")
        self.chk_auto_watch.setChecked(self.main_win.auto_watch_enabled)
        form.addRow("", self.chk_auto_watch)

        self.spin_interval = QSpinBox()
        self.spin_interval.setRange(5, 3600)
        self.spin_interval.setSuffix(" сек.")
        self.spin_interval.setValue(self.main_win.watch_interval_sec)
        form.addRow("Интервал автосканирования:", self.spin_interval)

        self.chk_mini_on_min = QCheckBox("Открывать миниплеер при сворачивании окна")
        self.chk_mini_on_min.setChecked(self.main_win.minimize_to_mini)
        form.addRow("", self.chk_mini_on_min)

        self.chk_mini_on_top = QCheckBox("Держать миниплеер поверх всех окон")
        self.chk_mini_on_top.setChecked(self.main_win.mini_on_top)
        form.addRow("", self.chk_mini_on_top)

        self.chk_load_on_start = QCheckBox("Загружать отслеживаемую папку при запуске")
        self.chk_load_on_start.setChecked(self.main_win.load_folder_on_startup)
        form.addRow("", self.chk_load_on_start)

        perf_label = QLabel("Производительность и память (для слабых ПК):")
        perf_label.setObjectName("SettingsSection")
        form.addRow("", perf_label)

        self.chk_visualizer = QCheckBox("Визуализатор спектра в главном окне")
        self.chk_visualizer.setChecked(self.main_win.visualizer_enabled)
        form.addRow("", self.chk_visualizer)

        self.chk_mini_viz = QCheckBox("Визуализатор в миниплеере")
        self.chk_mini_viz.setChecked(self.main_win.mini_visualizer_enabled)
        form.addRow("", self.chk_mini_viz)

        memory_label = QLabel("Память между запусками:")
        memory_label.setObjectName("SettingsSection")
        form.addRow("", memory_label)

        self.chk_restore_pos = QCheckBox("Запоминать трек и позицию — продолжить при следующем запуске")
        self.chk_restore_pos.setChecked(self.main_win.restore_last_track)
        form.addRow("", self.chk_restore_pos)

        self.chk_remember_mini = QCheckBox("Открываться в миниплеере, если закрыли в миниплеере")
        self.chk_remember_mini.setChecked(self.main_win.remember_mini_mode)
        form.addRow("", self.chk_remember_mini)

        tips_label = QLabel("Подсказки и что нового:")
        tips_label.setObjectName("SettingsSection")
        form.addRow("", tips_label)

        self.chk_show_tips = QCheckBox("Показывать подсказки при первом использовании функций")
        self.chk_show_tips.setChecked(self.main_win.show_tips)
        form.addRow("", self.chk_show_tips)

        self.btn_whatsnew = QPushButton("Что нового — список изменений по версиям")
        self.btn_whatsnew.setFixedHeight(30)
        self.btn_whatsnew.clicked.connect(lambda: self.main_win.open_whats_new_dialog())
        form.addRow("", self.btn_whatsnew)

        # v0.3.0: выбор темы оформления — мгновенный предпросмотр
        theme_label = QLabel("Оформление (тема):")
        theme_label.setObjectName("SettingsSection")
        form.addRow("", theme_label)

        self.combo_theme = QComboBox()
        for _tk in THEME_ORDER:
            self.combo_theme.addItem(THEMES[_tk]["label"], _tk)
        _cur = getattr(self.main_win, "theme_key", "aurora")
        if _cur not in THEME_ORDER:
            _cur = "aurora"
        self._theme_saved_key = _cur
        self.combo_theme.setCurrentIndex(THEME_ORDER.index(_cur))
        self.combo_theme.currentIndexChanged.connect(self._preview_theme)
        form.addRow("Тема оформления:", self.combo_theme)

        layout.addLayout(form)

        btn_default_os = QPushButton(f"Сделать {APP_NAME} плеером по умолчанию")
        btn_default_os.setFixedHeight(34)
        btn_default_os.clicked.connect(lambda: register_as_default_player(self))
        layout.addWidget(btn_default_os)

        bottom_btns = QHBoxLayout()
        bottom_btns.addStretch()
        btn_save = QPushButton("Сохранить")
        btn_cancel = QPushButton("Отмена")
        btn_save.clicked.connect(self.save_and_close)
        btn_cancel.clicked.connect(self.reject)
        bottom_btns.addWidget(btn_save)
        bottom_btns.addWidget(btn_cancel)
        layout.addLayout(bottom_btns)

    def _browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Выберите папку для мониторинга")
        if folder:
            self.folder_edit.setText(folder)

    def _preview_theme(self, _idx: int):
        """v0.3.0: живой предпросмотр — тема применяется сразу при выборе."""
        key = self.combo_theme.currentData()
        if key and key != self.main_win.theme_key:
            self.main_win.set_theme(key, save=False)

    def reject(self):
        """Отмена: вернуть тему, которая была активна при открытии."""
        saved = getattr(self, "_theme_saved_key", None)
        if saved and self.main_win.theme_key != saved:
            self.main_win.set_theme(saved, save=False)
        super().reject()

    def save_and_close(self):
        self.main_win.watched_folder = self.folder_edit.text().strip()
        self.main_win.auto_watch_enabled = self.chk_auto_watch.isChecked()
        self.main_win.watch_interval_sec = self.spin_interval.value()
        self.main_win.minimize_to_mini = self.chk_mini_on_min.isChecked()
        self.main_win.mini_on_top = self.chk_mini_on_top.isChecked()
        self.main_win.load_folder_on_startup = self.chk_load_on_start.isChecked()
        self.main_win.visualizer_enabled = self.chk_visualizer.isChecked()
        self.main_win.mini_visualizer_enabled = self.chk_mini_viz.isChecked()
        self.main_win.restore_last_track = self.chk_restore_pos.isChecked()
        self.main_win.remember_mini_mode = self.chk_remember_mini.isChecked()
        self.main_win.show_tips = self.chk_show_tips.isChecked()
        # v0.3.0: тема применяется при выборе (предпросмотр), здесь только
        # гарантируем СОХРАНЕНИЕ выбранного варианта — вызов идемпотентен
        _tk = self.combo_theme.currentData()
        if _tk:
            self.main_win.set_theme(_tk, save=True)
        self.main_win.save_settings()
        self.main_win.apply_visualizer_settings()
        self.main_win.apply_mini_on_top()
        self.main_win.setup_folder_watcher()
        self.accept()


# --- v0.3.7: Синхронизатор текста (перенос пользовательской утилиты) --------
# Идея: отдельное окно «Инструменты → Синхронизатор текста» готовит
# синхронизированный текст для трека (Demucs -> Whisper / выравнивание по
# готовому тексту) и пишет его в теги результата. Плеер читает эти теги
# (extract_lyrics), поэтому обработанный трек сразу играет с караоке.
# Тяжёлые зависимости (demucs, whisper, lyrics_transcriber, ffmpeg) в
# комплект плеера НЕ входят: они проверяются на месте, а их отсутствие
# не мешает остальным функциям плеера.

SYNC_INSTALL_HINT = ("Не хватает компонентов: {missing}.\n"
                     "Установите их в Python плеера командой:\n"
                     "python -m pip install demucs openai-whisper "
                     "lyrics-transcriber imageio-ffmpeg")


def _resolve_ffmpeg() -> str:
    """Путь к ffmpeg: сначала системный, затем пакет imageio-ffmpeg.

    Импорт imageio_ffmpeg ленивый — пакета может не быть, это не ошибка,
    а просто «компонент не найден» (сообщение покажет рабочий поток).
    """
    sys_ff = shutil.which("ffmpeg")
    if sys_ff:
        return sys_ff
    import imageio_ffmpeg  # необязательный пакет
    return imageio_ffmpeg.get_ffmpeg_exe()


def probe_sync_deps() -> Dict[str, bool]:
    """Быстрый статус компонентов синхронизатора без импорта тяжёлых библиотек.

    importlib.util.find_spec только ищет модуль на диске — torch/demucs
    не загружаются, поэтому открытие окна остаётся мгновенным даже на
    слабых машинах. Полная проверка (с реальным импортом) выполняется
    в рабочем потоке при запуске обработки.
    """
    import importlib.util
    has_ffmpeg = bool(shutil.which("ffmpeg")) or (
        importlib.util.find_spec("imageio_ffmpeg") is not None)
    return {
        "ffmpeg": has_ffmpeg,
        "demucs": importlib.util.find_spec("demucs") is not None,
        "whisper": importlib.util.find_spec("whisper") is not None,
        "lyrics_transcriber": importlib.util.find_spec("lyrics_transcriber") is not None,
        "mutagen": importlib.util.find_spec("mutagen") is not None,
    }


class DropLineEdit(QLineEdit):
    """Поле ввода с drag&drop: принимает только «свои» расширения файлов."""

    def __init__(self, allowed_exts, placeholder: str = "", parent=None):
        super().__init__(parent)
        self.allowed_exts = tuple(e.lower() for e in allowed_exts)
        self.setPlaceholderText(placeholder)
        self.setAcceptDrops(True)

    @staticmethod
    def pick_dropped_path(paths, exts) -> str:
        """Первый подходящий путь из перетащенных (вынесено для тестов)."""
        for p in paths:
            if p and Path(p).suffix.lower() in exts:
                return p
        return ""

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            paths = [u.toLocalFile() for u in event.mimeData().urls()]
            if self.pick_dropped_path(paths, self.allowed_exts):
                event.acceptProposedAction()
                return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls()]
        path = self.pick_dropped_path(paths, self.allowed_exts)
        if path:
            self.setText(path)
            event.acceptProposedAction()
            return
        super().dropEvent(event)


class LyricsSyncWorker(QThread):
    """Рабочий поток синхронизатора: Demucs -> Whisper/LyricsTranscriber -> LRC -> теги.

    Все тяжёлые импорты выполняются лениво внутри методов — загрузка
    модуля плеером не замедляется, а нехватка компонентов даёт понятное
    сообщение (SYNC_INSTALL_HINT), а не падение.
    Сигнал называется done (не finished): QThread уже имеет встроенный
    сигнал finished() — перекрытие имён источник тихих ошибок.
    """

    progress = pyqtSignal(int)
    log = pyqtSignal(str)
    lrc_ready = pyqtSignal(str, str)   # line_lrc, word_lrc (второй может быть пустым)
    done = pyqtSignal(bool, str)

    def __init__(self, audio_path, lyrics_path, model_size, save_path):
        super().__init__()
        self.audio_path = audio_path
        self.lyrics_path = lyrics_path
        self.model_size = model_size
        self.save_path = save_path
        self.ffmpeg_path = ""
        # Рабочая папка: временные чанки и LRC. Не CWD (он у установленного
        # плеера непредсказуем), а профиль пользователя.
        base_tmp = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        self.work_dir = os.path.join(base_tmp, "Hoxmusicp", "sync_tmp")

    # -- helpers ---------------------------------------------------------
    def _get_duration(self, path):
        import mutagen as _mg
        f = _mg.File(path)
        if f is None or f.info is None:
            raise RuntimeError(f"Не удалось прочитать длительность: {path}")
        return float(f.info.length)

    def _parse_lrc_to_sylt(self, lrc_path):
        """Строки LRC -> [(текст, мс)] для кадра SYLT (пословные метки <..> срезаются)."""
        frames = []
        with open(lrc_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("[") and "]" not in line:
                    continue
                line = re.sub(r"<[^>]+>", "", line)
                if "]" in line and line.startswith("["):
                    tp, text = line.split("]", 1)
                    try:
                        m, s = tp.strip("[]").split(":")
                        ms = int((float(m) * 60 + float(s)) * 1000)
                        frames.append((text.strip(), ms))
                    except ValueError:
                        continue
        return frames

    def _separate_vocals(self):
        os.makedirs(self.work_dir, exist_ok=True)
        self.log.emit("Отделение вокала (Demucs)...")
        stem = Path(self.audio_path).stem
        subprocess.run(
            ["demucs", "--two-stems=vocals", "-o", self.work_dir, self.audio_path],
            check=True, capture_output=True, env=os.environ.copy()
        )
        vocal = Path(self.work_dir) / "htdemucs" / stem / "vocals.wav"
        if not vocal.exists():
            raise FileNotFoundError(f"Вокал не найден: {vocal}")
        self.log.emit(f"Вокал отделён: {vocal}")
        return str(vocal)

    def _transcribe_word_level(self, audio_path):
        import whisper
        duration = self._get_duration(audio_path)
        self.log.emit(f"Длительность: {duration:.1f} сек ({duration/60:.2f} мин)")
        self.log.emit(f"Загрузка модели Whisper '{self.model_size}'...")
        model = whisper.load_model(self.model_size)
        self.log.emit("Слушаю песню от начала до конца...")

        chunk_size, overlap = 30, 1.0
        all_words = []
        starts = list(range(0, int(duration), int(chunk_size - overlap)))

        for i, start in enumerate(starts):
            end = min(start + chunk_size, int(duration))
            if start >= duration:
                break
            chunk_path = os.path.join(self.work_dir, f"_chunk_{i:04d}.wav")
            subprocess.run([
                self.ffmpeg_path, "-y", "-loglevel", "error",
                "-ss", str(start), "-i", audio_path, "-to", str(end - start),
                "-ar", "16000", "-ac", "1", chunk_path
            ], check=True, capture_output=True)

            res = model.transcribe(chunk_path, language="ru",
                                   word_timestamps=True, verbose=False)
            new_words = []
            for seg in res["segments"]:
                for w in seg.get("words", []):
                    new_words.append({
                        "word": w["word"],
                        "start": w["start"] + start,
                        "end": w["end"] + start,
                    })

            if all_words:
                # Склейка перекрытия: убираем дубли слов из зоны перекрытия
                ov_end = start + overlap
                tail = [w for w in all_words[-30:] if w["start"] > ov_end - 2.0]
                filtered = []
                for w in new_words:
                    if w["start"] < ov_end and any(
                        pw["word"].strip().lower() == w["word"].strip().lower()
                        and abs(pw["start"] - w["start"]) < 1.0
                        for pw in tail
                    ):
                        continue
                    filtered.append(w)
                new_words = filtered

            all_words.extend(new_words)
            pct = min(int((end / duration) * 70), 70)
            self.progress.emit(pct)
            self.log.emit(f"Прослушано {end:.0f}/{duration:.0f} сек ({pct}%)")

            try:
                os.remove(chunk_path)
            except OSError:
                pass

        self.log.emit(f"Распознано слов: {len(all_words)}")
        return all_words

    def _words_to_lrc(self, words, word_level):
        """Слова Whisper -> LRC-файл (по строкам по паузе > 1.5 с)."""
        os.makedirs(self.work_dir, exist_ok=True)  # самодостаточность (и для тестов)
        lines, cur, prev_end = [], [], 0.0
        for w in words:
            text = w["word"].strip()
            if not text:
                continue
            if cur and w["start"] - prev_end > 1.5:
                lines.append(cur)
                cur = []
            cur.append(w)
            prev_end = w["end"]
        if cur:
            lines.append(cur)

        out_path = os.path.join(
            self.work_dir, "lyrics_word.lrc" if word_level else "lyrics_line.lrc"
        )
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("[ti:Synchronized]\n[by:Hox sync]\n")
            for line in lines:
                start = line[0]["start"]
                mm = int(start // 60)
                ss = start % 60
                if word_level:
                    parts = []
                    for w in line:
                        wm = int(w["start"] // 60)
                        ws = w["start"] % 60
                        parts.append(f"<{wm:02d}:{ws:05.2f}>{w['word'].strip()}")
                    f.write(f"[{mm:02d}:{ss:05.2f}]{''.join(parts)}\n")
                else:
                    text = " ".join(w["word"].strip() for w in line)
                    f.write(f"[{mm:02d}:{ss:05.2f}]{text}\n")
        return out_path

    def _align_with_text(self, vocal_path):
        self.log.emit(f"Выравнивание по готовому тексту (Whisper {self.model_size})...")
        os.environ["WHISPER_MODEL"] = self.model_size
        from lyrics_transcriber import LyricsTranscriber
        tr = LyricsTranscriber(audio_filepath=vocal_path,
                               lyrics_filepath=self.lyrics_path)
        res = tr.generate()
        lrc = res.get("midico_lrc_filepath") or res.get("lrc_filepath")
        if not lrc or not os.path.exists(lrc):
            raise RuntimeError("LRC не создан.")
        self.log.emit(f"LRC готов: {lrc}")
        return lrc

    def _embed(self, target_path, line_lrc, word_lrc=None):
        """Запись синхронного текста в теги результата (оригинал не трогаем).

        MP3/WAV: SYLT (построчный синхронный текст) + TXXX:LYRICS_LRC
        (пословный enhanced LRC — его плеер предпочитает всем остальным).
        FLAC: LYRICS + LYRICS_LINE (Vorbis-комментарии).
        """
        from mutagen.id3 import ID3, SYLT, TXXX, Encoding
        ext = Path(target_path).suffix.lower()
        if ext == ".mp3":
            from mutagen.mp3 import MP3
            audio = MP3(target_path, ID3=ID3)
            if audio.tags is None:
                audio.add_tags()
            audio.tags.delall("SYLT")
            audio.tags.add(SYLT(
                encoding=Encoding.UTF8, lang="rus", format=2, type=1,
                desc="Synchronized lyrics",
                text=self._parse_lrc_to_sylt(line_lrc)
            ))
            if word_lrc:
                audio.tags.delall("TXXX:LYRICS_LRC")
                audio.tags.add(TXXX(
                    encoding=Encoding.UTF8, desc="LYRICS_LRC",
                    text=Path(word_lrc).read_text(encoding="utf-8")
                ))
            audio.save(v2_version=3)
            self.log.emit("MP3: записаны SYLT + TXXX:LYRICS_LRC.")
            return
        if ext == ".flac":
            audio = FLAC(target_path)
            audio["LYRICS"] = Path(word_lrc or line_lrc).read_text(encoding="utf-8")
            audio["LYRICS_LINE"] = Path(line_lrc).read_text(encoding="utf-8")
            audio.save()
            self.log.emit("FLAC: записаны LYRICS + LYRICS_LINE.")
            return
        if ext == ".wav":
            from mutagen.wave import WAVE
            audio = WAVE(target_path)
            if audio.tags is None:
                audio.add_tags()
            if "SYLT" in audio.tags:
                del audio.tags["SYLT"]
            audio.tags.add(SYLT(
                encoding=Encoding.UTF8, lang="rus", format=2, type=1,
                desc="Synchronized lyrics",
                text=self._parse_lrc_to_sylt(line_lrc)
            ))
            if word_lrc:
                if "TXXX:LYRICS_LRC" in audio.tags:
                    del audio.tags["TXXX:LYRICS_LRC"]
                audio.tags.add(TXXX(
                    encoding=Encoding.UTF8, desc="LYRICS_LRC",
                    text=Path(word_lrc).read_text(encoding="utf-8")
                ))
            audio.save()
            self.log.emit("WAV: записаны SYLT + TXXX:LYRICS_LRC.")
            return
        raise ValueError(f"Формат {ext} не поддерживается.")

    def _check_environment(self):
        """Реальная проверка компонентов (с импортом). Возвращает список недостающих."""
        missing = []
        try:
            self.ffmpeg_path = _resolve_ffmpeg()
        except Exception:
            missing.append("ffmpeg (или пакет imageio-ffmpeg)")
        try:
            import demucs  # noqa: F401
        except Exception:
            missing.append("demucs")
        try:
            import whisper  # noqa: F401
        except Exception:
            missing.append("openai-whisper")
        if self.lyrics_path:
            try:
                import lyrics_transcriber  # noqa: F401
            except Exception:
                missing.append("lyrics-transcriber")
        return missing

    def run(self):
        try:
            src_ext = Path(self.audio_path).suffix.lower()
            dst_ext = Path(self.save_path).suffix.lower()
            if src_ext != dst_ext:
                raise ValueError(
                    f"Расширение результата ({dst_ext}) должно совпадать "
                    f"с исходным ({src_ext}).")

            self.log.emit("Проверка компонентов...")
            os.makedirs(self.work_dir, exist_ok=True)
            missing = self._check_environment()
            if missing:
                raise RuntimeError(SYNC_INSTALL_HINT.format(missing=", ".join(missing)))
            self.log.emit(f"FFmpeg: {self.ffmpeg_path}")

            vocal = self._separate_vocals()
            self.progress.emit(10)

            if self.lyrics_path:
                line_lrc = self._align_with_text(vocal)
                word_lrc = None
            else:
                words = self._transcribe_word_level(vocal)
                if not words:
                    raise RuntimeError("Не удалось распознать слова.")
                line_lrc = self._words_to_lrc(words, word_level=False)
                word_lrc = self._words_to_lrc(words, word_level=True)
            self.progress.emit(80)

            # Предпросмотр в окно до записи тегов
            line_content = Path(line_lrc).read_text(encoding="utf-8")
            word_content = Path(word_lrc).read_text(encoding="utf-8") if word_lrc else ""
            self.lrc_ready.emit(line_content, word_content)

            self.log.emit(f"Копирование оригинала -> {self.save_path}")
            if Path(self.audio_path).resolve() != Path(self.save_path).resolve():
                shutil.copy2(self.audio_path, self.save_path)
            else:
                self.log.emit("Путь совпадает с оригиналом — правки будут в исходном файле.")

            self.log.emit("Запись тегов в результат...")
            self._embed(self.save_path, line_lrc, word_lrc)
            self.progress.emit(100)
            self.done.emit(
                True,
                f"Готово! Файл сохранён:\n{self.save_path}\n"
                f"Оригинал не изменён: {self.audio_path}"
            )
        except Exception as e:
            self.log.emit(f"Ошибка: {e}")
            self.done.emit(False, str(e))


class LyricsSyncDialog(QDialog):
    """Окно «Синхронизатор текста» (v0.3.7, немодальное).

    Перенос пользовательской утилиты в плеер: поля аудио/текста с
    drag&drop, выбор модели Whisper, прогресс, лог и предпросмотр LRC.
    Стили берутся из глобальной темы приложения (никаких inline-стилей
    на контейнерах — урок v0.3.1), поэтому окно перекрашивается вместе
    со сменой темы без дополнительного кода.
    """

    AUDIO_EXTS = (".mp3", ".flac", ".wav")
    TEXT_EXTS = (".txt", ".lrc")

    def __init__(self, main_win: "MediaPlayerWindow"):
        super().__init__(main_win)
        self.main_win = main_win
        self.setWindowTitle(f"Синхронизатор текста — {APP_FULL_TITLE}")
        self.resize(760, 700)
        self.setMinimumSize(620, 560)
        self.worker: Optional[LyricsSyncWorker] = None

        root = QVBoxLayout(self)

        # Статус компонентов + подсказка установки (обновляется при открытии)
        self.lbl_deps = QLabel(self._deps_text())
        self.lbl_deps.setObjectName("SyncDeps")
        self.lbl_deps.setWordWrap(True)
        root.addWidget(self.lbl_deps)

        g = QGroupBox("Входные файлы  ·  файлы можно перетащить прямо в поля")
        gl = QVBoxLayout(g)

        row_a = QHBoxLayout()
        row_a.addWidget(QLabel("Аудио:"))
        self.audio_edit = DropLineEdit(
            self.AUDIO_EXTS, "MP3 / FLAC / WAV — перетащите или «Обзор…»")
        row_a.addWidget(self.audio_edit, 1)
        b1 = QPushButton("Обзор...")
        b1.clicked.connect(self._browse_audio)
        row_a.addWidget(b1)
        gl.addLayout(row_a)

        row_t = QHBoxLayout()
        row_t.addWidget(QLabel("Текст:"))
        self.lyrics_edit = DropLineEdit(
            self.TEXT_EXTS, ".txt / .lrc — необязательно (иначе автораспознавание)")
        row_t.addWidget(self.lyrics_edit, 1)
        b2 = QPushButton("Обзор...")
        b2.clicked.connect(self._browse_lyrics)
        row_t.addWidget(b2)
        gl.addLayout(row_t)
        root.addWidget(g)

        sg = QGroupBox("Настройки")
        sl = QHBoxLayout(sg)
        sl.addWidget(QLabel("Модель Whisper:"))
        self.model_combo = QComboBox()
        self.model_combo.addItems(["tiny", "base", "small", "medium", "large-v3"])
        self.model_combo.setCurrentText("medium")
        sl.addWidget(self.model_combo)
        sl.addStretch(1)
        root.addWidget(sg)

        self.btn_start = QPushButton("Запустить (спросит, куда сохранить)")
        self.btn_start.setObjectName("SyncStart")
        self.btn_start.setMinimumHeight(40)
        self.btn_start.clicked.connect(self._start)
        root.addWidget(self.btn_start)

        self.progress = QProgressBar()
        self.progress.setValue(0)
        root.addWidget(self.progress)

        self.tabs = QTabWidget()
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.preview_view = QPlainTextEdit()
        self.preview_view.setReadOnly(True)
        self.preview_view.setPlaceholderText(
            "Здесь появится синхронизированный текст (enhanced LRC) после обработки.")
        self.tabs.addTab(self.log_view, "Лог")
        self.tabs.addTab(self.preview_view, "Предпросмотр LRC")
        root.addWidget(self.tabs, 1)

        # Главный сценарий: обработать трек, который уже открыт в плеере
        cur = self._current_track_path()
        if cur:
            self.audio_edit.setText(cur)
            self.log_view.append(f"Аудио подставлено из плеера: {cur}")

    # -- helpers ---------------------------------------------------------
    def _current_track_path(self) -> str:
        """Путь текущего трека плеера, если формат поддержан синхронизатором."""
        try:
            idx = self.main_win.current_index
            tracks = self.main_win.tracks
            if 0 <= idx < len(tracks):
                p = tracks[idx].filepath
                if p and Path(p).suffix.lower() in self.AUDIO_EXTS:
                    return str(p)
        except Exception:
            pass
        return ""

    def _deps_text(self) -> str:
        st = probe_sync_deps()

        def mark(ok: bool) -> str:
            return "найден" if ok else "не найден"

        return ("Компоненты: FFmpeg — {ff} · Demucs — {dm} · Whisper — {ws} · "
                "lyrics-transcriber — {lt} · mutagen — {mg}. Установка: "
                "python -m pip install demucs openai-whisper lyrics-transcriber "
                "imageio-ffmpeg").format(
            ff=mark(st["ffmpeg"]), dm=mark(st["demucs"]), ws=mark(st["whisper"]),
            lt=mark(st["lyrics_transcriber"]), mg=mark(st["mutagen"]))

    def _suggest_save_path(self, audio_path: str) -> str:
        p = Path(audio_path)
        ext = p.suffix.lower()
        filter_map = {
            ".mp3": "MP3 (*.mp3)",
            ".flac": "FLAC (*.flac)",
            ".wav": "WAV (*.wav)",
        }
        default = str(p.with_name(f"{p.stem}_synced{ext}"))
        save_path, _ = QFileDialog.getSaveFileName(
            self, "Куда сохранить результат?", default,
            filter_map.get(ext, "Все файлы (*)"))
        return save_path

    # -- слоты -----------------------------------------------------------
    def _browse_audio(self):
        p, _ = QFileDialog.getOpenFileName(
            self, "Аудио", "", "Аудио (*.mp3 *.flac *.wav);;Все файлы (*)")
        if p:
            self.audio_edit.setText(p)

    def _browse_lyrics(self):
        p, _ = QFileDialog.getOpenFileName(
            self, "Текст", "", "Текст (*.txt *.lrc);;Все файлы (*)")
        if p:
            self.lyrics_edit.setText(p)

    def _start(self):
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.information(
                self, "Занято", "Обработка уже идёт — дождитесь завершения.")
            return
        a = self.audio_edit.text().strip()
        if not a or not os.path.exists(a):
            QMessageBox.warning(self, "Ошибка", "Выберите существующий аудиофайл.")
            return
        l = self.lyrics_edit.text().strip() or None
        if l and not os.path.exists(l):
            QMessageBox.warning(self, "Ошибка", "Файл текста не найден.")
            return

        save_path = self._suggest_save_path(a)
        if not save_path:
            return
        src_ext = Path(a).suffix.lower()
        if Path(save_path).suffix.lower() != src_ext:
            save_path = str(Path(save_path).with_suffix(src_ext))

        self.btn_start.setEnabled(False)
        self.progress.setValue(0)
        self.log_view.clear()
        self.preview_view.clear()
        self.tabs.setCurrentWidget(self.log_view)
        self.log_view.append(f"Запуск...\nРезультат: {save_path}\n")

        self.worker = LyricsSyncWorker(
            a, l, self.model_combo.currentText(), save_path)
        self.worker.log.connect(self.log_view.append)
        self.worker.progress.connect(self.progress.setValue)
        self.worker.lrc_ready.connect(self._on_lrc_ready)
        self.worker.done.connect(self._on_done)
        self.worker.start()

    def _on_lrc_ready(self, line_content: str, word_content: str):
        """Показывает LRC в предпросмотре и переключается на вкладку."""
        self.preview_view.setPlainText(word_content if word_content else line_content)
        self.tabs.setCurrentWidget(self.preview_view)

    def _on_done(self, ok: bool, msg: str):
        self.btn_start.setEnabled(True)
        self.log_view.append(("\n[OK] " if ok else "\n[ОШИБКА] ") + msg)
        (QMessageBox.information if ok else QMessageBox.critical)(
            self, "Готово" if ok else "Ошибка", msg)

    def closeEvent(self, event):
        if self.worker is not None and self.worker.isRunning():
            # Поток прерывать нельзя (Whisper/Demucs не поддерживают отмену) —
            # окно просто прячется, обработка продолжается в фоне.
            event.ignore()
            self.hide()
            try:
                self.main_win.status_bar.showMessage(
                    "Синхронизатор продолжает работу в фоне.", 4000)
            except Exception:
                pass
            return
        if getattr(self.main_win, "_sync_dialog", None) is self:
            self.main_win._sync_dialog = None
        super().closeEvent(event)


# --- Обновлённый Миниплеер с временем трека, текущим плейлистом и синхро-текстом ---
class MiniPlayerWindow(QWidget):
    COMPACT_H = 184
    EXPANDED_H = 400
    WIDTH = 395
    MAX_W = 900
    MAX_H_EXPANDED = 760
    # Биты, отличающие Qt.Tool от Qt.Window: сам Tool — составной флаг
    # (Window | Dialog | Popup), поэтому снимать нужно только Dialog|Popup,
    # иначе вместе с Tool срежется бит Window и окно перестанет быть окном.
    _TOOL_EXTRA = (Qt.WindowType.Dialog | Qt.WindowType.Popup) & ~Qt.WindowType.Window

    def __init__(self, main_window: "MediaPlayerWindow"):
        super().__init__()
        self.main_win = main_window
        self._drag_pos: Optional[QPoint] = None
        self.is_expanded = False
        self._last_lyric_row: int = -1
        # Геометрия на момент сворачивания в панель задач (QRect)
        self._pre_minimize_geo = None
        # v0.3.3: плейлист изменился, пока миниплеер был скрыт — обновить при показе
        self._playlist_dirty = False

        self.setWindowTitle(f"{APP_FULL_TITLE} — Mini")
        # Поведение «поверх всех окон» управляется настройкой mini_on_top
        # (Настройки, v0.1.5): по умолчанию выключено — при переключении на
        # другую программу миниплеер уходит на задний план вместе с остальными
        # окнами плеера (изменение 0.1.4).
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
        )
        self._apply_size_constraints()
        # v0.3.0: стили миниплеера строятся из активной темы (шаблон выше)
        self.setStyleSheet(build_mini_qss())
        self.setObjectName("MiniRoot")
        self._init_ui()
        self._move_to_bottom_right()

    def _init_ui(self):
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(8, 8, 8, 8)
        outer_layout.setSpacing(6)

        # Верхняя часть миниплеера
        top_part = QHBoxLayout()
        top_part.setSpacing(10)

        self.cover_label = QLabel()
        self.cover_label.setFixedSize(90, 90)
        self.cover_label.setObjectName("MiniCover")
        self.cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top_part.addWidget(self.cover_label)

        right_col = QVBoxLayout()
        right_col.setSpacing(3)

        title_row = QHBoxLayout()
        self.title_label = QLabel(APP_FULL_TITLE)
        self.title_label.setFont(QFont("Arial", 9, QFont.Weight.Bold))
        # Уменьшенный минимум, чтобы в заголовке поместились все 4 кнопки:
        # заголовок всё равно растягивается (stretch = 1) на свободное место
        self.title_label.setMinimumWidth(100)
        self.title_label.setObjectName("MiniTitle")

        self.btn_drawer = QPushButton(" Плейлист / Текст")
        self.btn_drawer.setCheckable(True)
        self.btn_drawer.setToolTip("Показать/скрыть текущий плейлист и слова песни")
        self.btn_drawer.setFixedHeight(22)
        # v0.3.3: иконка-меню вместо символа ☰ (шрифтозависимого)
        self.btn_drawer.setIcon(make_icon("menu", QColor(T["sub1"]), 24))
        self.btn_drawer.setIconSize(QSize(12, 12))
        self.btn_drawer.toggled.connect(self._toggle_drawer)

        self.btn_restore = QPushButton()
        self.btn_restore.setToolTip("Развернуть полное окно")
        self.btn_restore.setFixedSize(26, 22)
        self.btn_restore.setIcon(make_icon("maximize", QColor(T["sub1"]), 24))
        self.btn_restore.setIconSize(QSize(12, 12))
        self.btn_restore.clicked.connect(self.main_win.restore_from_mini)

        self.btn_minimize = QPushButton()
        self.btn_minimize.setToolTip("Свернуть в панель задач")
        self.btn_minimize.setFixedSize(26, 22)
        self.btn_minimize.setIcon(make_icon("minimize", QColor(T["sub1"]), 24))
        self.btn_minimize.setIconSize(QSize(12, 12))
        self.btn_minimize.clicked.connect(self.minimize_to_taskbar)

        self.btn_close = QPushButton()
        self.btn_close.setToolTip("Закрыть плеер")
        self.btn_close.setFixedSize(26, 22)
        self.btn_close.setIcon(make_icon("close", QColor(T["sub1"]), 24))
        self.btn_close.setIconSize(QSize(12, 12))
        self.btn_close.clicked.connect(self.main_win.close)

        title_row.addWidget(self.title_label, 1)
        title_row.addWidget(self.btn_drawer)
        title_row.addWidget(self.btn_restore)
        title_row.addWidget(self.btn_minimize)
        title_row.addWidget(self.btn_close)
        right_col.addLayout(title_row)

        self.artist_label = ClickableLabel("")
        self.artist_label.setObjectName("MiniArtist")
        self.artist_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.artist_label.clicked.connect(self._open_artist_page)
        right_col.addWidget(self.artist_label)

        # Ползунок + Время проигрывания трека (00:00 / 00:00)
        slider_row = QHBoxLayout()
        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setObjectName("MiniTime")
        self.position_slider = QSlider(Qt.Orientation.Horizontal)
        self.position_slider.setRange(0, 0)
        self.position_slider.sliderPressed.connect(
            lambda: setattr(self.main_win, 'slider_pressed', True)
        )
        self.position_slider.sliderReleased.connect(self._on_slider_released)
        slider_row.addWidget(self.position_slider, 1)
        slider_row.addWidget(self.time_label)
        right_col.addLayout(slider_row)

        # Кнопки управления (v0.3.3: векторные иконки вместо эмодзи)
        btn_row = QHBoxLayout()
        self.btn_prev = QPushButton()
        self.btn_play = QPushButton()
        self.btn_next = QPushButton()
        self.btn_repeat = QPushButton()
        self.btn_repeat.setCheckable(True)

        for b in (self.btn_prev, self.btn_play, self.btn_next, self.btn_repeat):
            b.setFixedHeight(24)

        self.btn_prev.setIcon(make_icon("prev", QColor(T["sub1"]), 24))
        self.btn_prev.setIconSize(QSize(12, 12))
        self.btn_play.setIcon(make_icon("play", QColor(T["sub1"]), 24))
        self.btn_play.setIconSize(QSize(12, 12))
        self.btn_next.setIcon(make_icon("next", QColor(T["sub1"]), 24))
        self.btn_next.setIconSize(QSize(12, 12))
        self._update_repeat_icon()
        self.btn_prev.clicked.connect(self.main_win.prev_track)
        self.btn_play.clicked.connect(self.main_win.toggle_play)
        self.btn_next.clicked.connect(self.main_win.next_track)
        self.btn_repeat.clicked.connect(
            lambda checked: self.main_win.btn_repeat.setChecked(checked)
        )

        btn_row.addWidget(self.btn_prev)
        btn_row.addWidget(self.btn_play)
        btn_row.addWidget(self.btn_next)
        btn_row.addWidget(self.btn_repeat)
        right_col.addLayout(btn_row)

        top_part.addLayout(right_col, 1)
        outer_layout.addLayout(top_part)

        # Мини-визуализатор спектра (отключается в настройках для слабых ПК)
        self.viz_widget = AudioVisualizerWidget(self, num_bars=16)
        self.viz_widget.setFixedHeight(40)  # v0.3.4
        outer_layout.addWidget(self.viz_widget)

        # Синхронизированная строка текущих слов песни прямо в компактном режиме
        self.live_lyric_label = QLabel("Слова песни появятся здесь при проигрывании...")
        self.live_lyric_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.live_lyric_label.setObjectName("MiniLyric")
        self.live_lyric_label.setFixedHeight(22)
        outer_layout.addWidget(self.live_lyric_label)

        # Выдвижная нижняя панель: вкладки «Текущий плейлист» и «Слова песни (Караоке)»
        self.drawer_tabs = QTabWidget()
        self.drawer_tabs.setVisible(False)

        self.mini_playlist_widget = QListWidget()
        self.mini_playlist_widget.itemDoubleClicked.connect(self._on_mini_playlist_dbl_click)
        self.drawer_tabs.addTab(self.mini_playlist_widget, "Текущий плейлист")

        self.mini_lyrics_widget = QListWidget()
        self.mini_lyrics_widget.itemClicked.connect(self._on_mini_lyric_clicked)
        self.drawer_tabs.addTab(self.mini_lyrics_widget, "Слова песни")

        outer_layout.addWidget(self.drawer_tabs, 1)

        # Уголок для изменения размера окна мышью (нижний правый край)
        self.size_grip = QSizeGrip(self)
        self.size_grip.setFixedSize(16, 16)
        self.size_grip.move(self.width() - 18, self.height() - 18)
        self.size_grip.raise_()

    def _apply_size_constraints(self):
        """Миниплеер можно растягивать мышью: в компактном режиме — только ширину,
        в раскрытом (с плейлистом) — ширину и высоту."""
        if self.is_expanded:
            self.setMinimumSize(self.WIDTH, self.EXPANDED_H)
            self.setMaximumSize(self.MAX_W, self.MAX_H_EXPANDED)
        else:
            self.setMinimumSize(self.WIDTH, self.COMPACT_H)
            self.setMaximumSize(self.MAX_W, self.COMPACT_H)

    def _toggle_drawer(self, checked: bool):
        self.is_expanded = checked
        old_bottom = self.geometry().bottom()
        new_h = self.EXPANDED_H if checked else self.COMPACT_H
        self.drawer_tabs.setVisible(checked)
        self._apply_size_constraints()
        self.resize(max(self.WIDTH, self.width()), new_h)
        self.move(self.x(), old_bottom - new_h + 1)
        self.size_grip.move(self.width() - 18, self.height() - 18)
        self.size_grip.raise_()

    def _update_repeat_icon(self):
        """v0.3.3: иконка повтора миниплеера — repeat/repeat_one по флагу окна."""
        name = "repeat_one" if getattr(self.main_win, "repeat_one", False) else "repeat"
        self.btn_repeat.setIcon(make_icon(name, QColor(T["sub1"]), 24))
        self.btn_repeat.setIconSize(QSize(12, 12))

    def refresh_mini_playlist(self, tracks: List[TrackInfo], active_fp: Optional[Path], is_playing: bool):
        # v0.3.3: список перерисован — отложенная пометка «грязно» не нужна
        self._playlist_dirty = False
        self.mini_playlist_widget.blockSignals(True)
        self.mini_playlist_widget.clear()
        active_row = -1
        for i, t in enumerate(tracks):
            is_act = (active_fp is not None and t.filepath == active_fp)
            prefix = ("▶ " if is_playing else "⏸ ") if is_act else f"{i + 1}. "
            item = QListWidgetItem(f"{prefix}{t.title} — {t.artist} ({t.dur_str})")
            if is_act:
                item.setForeground(QColor(T["act_text"]))
                active_row = i
            self.mini_playlist_widget.addItem(item)
        if active_row >= 0:
            self.mini_playlist_widget.setCurrentRow(active_row)
            self.mini_playlist_widget.scrollToItem(
                self.mini_playlist_widget.item(active_row),
                QAbstractItemView.ScrollHint.PositionAtCenter
            )
        self.mini_playlist_widget.blockSignals(False)

    def mark_playlist_dirty(self):
        """v0.3.3: список изменился, пока миниплеер был скрыт, — обновить
        при следующем показе (см. showEvent), а не тратить CPU сейчас."""
        self._playlist_dirty = True

    def set_mini_lyrics(self, lines: List[LyricLine]):
        self._last_lyric_row = -1
        self.mini_lyrics_widget.clear()
        if not lines:
            self.live_lyric_label.setText("Текст песни не найден")
            self.mini_lyrics_widget.addItem("Текст песни (.lrc или тег) отсутствует для этого трека.")
            return
        for ln in lines:
            item = QListWidgetItem(ln.text)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item.setData(Qt.ItemDataRole.UserRole, ln.time_ms)
            self.mini_lyrics_widget.addItem(item)
        self.live_lyric_label.setText(f"{lines[0].text}")

    def highlight_mini_lyric_row(self, row: int, line_text: str):
        # Обновляем только при смене строки — иначе лишние перерисовки несколько раз в секунду
        if row == self._last_lyric_row:
            return
        self._last_lyric_row = row
        self.live_lyric_label.setText(line_text)
        if 0 <= row < self.mini_lyrics_widget.count():
            self.mini_lyrics_widget.setCurrentRow(row)
            self.mini_lyrics_widget.scrollToItem(
                self.mini_lyrics_widget.item(row),
                QAbstractItemView.ScrollHint.PositionAtCenter
            )

    def _on_mini_playlist_dbl_click(self, _item: QListWidgetItem):
        row = self.mini_playlist_widget.currentRow()
        if row >= 0:
            self.main_win.play_index(row)

    def _on_mini_lyric_clicked(self, item: QListWidgetItem):
        t_ms = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(t_ms, int):
            # v0.3.4: клик по строке учитывает ручной сдвиг синхронизации
            self.main_win.player.setPosition(max(0, t_ms + self.main_win.lyric_offset_ms))

    def _move_to_bottom_right(self):
        screen = QApplication.primaryScreen()
        if screen:
            geom = screen.availableGeometry()
            self.move(geom.right() - self.width() - 20, geom.bottom() - self.height() - 20)

    def restore_saved_geometry(self):
        """Восстанавливает размер, положение и раскрытость миниплеера из настроек."""
        expanded = self.main_win.settings.value("mini_expanded", False, type=bool)
        if expanded and not self.is_expanded:
            self.btn_drawer.setChecked(True)  # вызовет _toggle_drawer
        geo_str = self.main_win.settings.value("mini_geometry", "", type=str) or ""
        placed = False
        if geo_str:
            try:
                x, y, w, h = (int(v) for v in geo_str.split(","))
                min_h = self.EXPANDED_H if self.is_expanded else self.COMPACT_H
                w = max(self.WIDTH, min(w, self.MAX_W))
                h = max(min_h, min(h, self.MAX_H_EXPANDED))
                self.resize(w, h)
                self.move(x, y)
                placed = True
            except (ValueError, TypeError):
                placed = False
        if not placed:
            self._move_to_bottom_right()
        screen = QApplication.primaryScreen()
        if screen and not screen.availableGeometry().intersects(self.geometry()):
            self._move_to_bottom_right()

    def _open_artist_page(self):
        self.main_win.restore_from_mini()
        self.main_win.open_current_artist_page()

    def showEvent(self, event: QEvent):
        # Тёмный заголовок миниплеера в цвет темы (v0.1.9, Windows 10/11)
        apply_dark_title_bar(self)
        super().showEvent(event)
        # v0.3.3: пока миниплеер был скрыт, плейлист мог измениться
        if self._playlist_dirty:
            self._playlist_dirty = False
            QTimer.singleShot(0, self.main_win._refresh_mini_playlist_async)

    def apply_on_top(self, enabled: bool):
        """Настройка «поверх всех окон» (v0.1.5, по умолчанию выключена).

        setWindowFlags пересоздаёт нативное окно, поэтому при видимом
        миниплеере показываем его заново и поднимаем наверх.
        """
        enabled = bool(enabled)
        self.main_win.mini_on_top = enabled
        has = bool(self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        if enabled == has:
            return
        flags = self.windowFlags()
        if enabled:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowType.WindowStaysOnTopHint
        was_visible = self.isVisible()
        self.setWindowFlags(flags)
        if was_visible:
            self.show()
            self.raise_()

    def minimize_to_taskbar(self):
        """Сворачивает миниплеер в панель задач.

        Окно фреймлесс и несёт флаг Qt.Tool — у таких окон нет кнопки в панели
        задач, и в Alt+Tab они тоже не попадают, поэтому свернутое окно нельзя
        было бы вернуть. На время сворачивания снимаем Tool:
        в панели задач появляется кнопка, кликом по которой окно возвращается
        на экран (флаги восстанавливаются в changeEvent).
        """
        # Запоминаем нормальную геометрию ДО сворачивания: при возврате флагов
        # нативное окно пересоздаётся, и Windows может вернуть его со сбитым
        # размером/позицией — тогда развёрнутый плеер «схлопывается» обратно
        # в компактную полоску.
        if not bool(self.windowState() & Qt.WindowState.WindowMinimized):
            self._pre_minimize_geo = self.geometry()
        flags = self.windowFlags() & ~MiniPlayerWindow._TOOL_EXTRA
        self.setWindowFlags(flags)
        self.showMinimized()

    def _restore_overlay_flags(self):
        """Возвращает флаги Tool-окна после восстановления из свернутого
        состояния. WindowStaysOnTop возвращается только если включена
        настройка «поверх всех окон» (mini_on_top, по умолчанию выкл.)."""
        if (self.isVisible()
                and not bool(self.windowState() & Qt.WindowState.WindowMinimized)
                and not bool(self.windowFlags() & MiniPlayerWindow._TOOL_EXTRA)):
            geo = self._pre_minimize_geo
            self._pre_minimize_geo = None
            flags = self.windowFlags() | MiniPlayerWindow._TOOL_EXTRA
            if getattr(self.main_win, "mini_on_top", False):
                # Настройка «поверх всех окон» включена — возвращаем и его
                flags |= Qt.WindowType.WindowStaysOnTopHint
            self.setWindowFlags(flags)  # скрывает и пересоздаёт нативное окно
            self.showNormal()
            self._apply_size_constraints()
            if geo is not None:
                # Пересозданное окно Windows может вернуть со сбитым размером
                # или в iconic-позиции (-32000,-32000) — закрепляем геометрию,
                # снятую в момент сворачивания: развёрнутый плеер с открытой
                # панелью плейлиста возвращается именно развёрнутым.
                self.setGeometry(geo)
            self.raise_()
            self.activateWindow()

    def changeEvent(self, event: QEvent):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            if not bool(self.windowState() & Qt.WindowState.WindowMinimized):
                # Окно вернули из свернутого состояния — возвращаем флаги
                # (отложенно, чтобы не пересоздавать окно внутри события)
                QTimer.singleShot(0, self._restore_overlay_flags)

    def closeEvent(self, event):
        # Закрытие миниплеера любым системным способом (Alt+F4, «Закрыть окно»
        # в панели задач в свернутом состоянии) должно закрывать весь плеер
        # с сохранением настроек, а не оставлять приложение без окон.
        event.accept()
        if not self.main_win.isVisible():
            self.main_win.close()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "size_grip"):
            self.size_grip.move(self.width() - 18, self.height() - 18)
            self.size_grip.raise_()

    def _on_slider_released(self):
        self.main_win.slider_pressed = False
        self.main_win.player.setPosition(self.position_slider.value())

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._drag_pos = None
        event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self.main_win.restore_from_mini()


# --- Виджет страницы исполнителя и его альбомов ---
class ArtistsAlbumsView(QWidget):
    def __init__(self, main_win: "MediaPlayerWindow"):
        super().__init__(main_win)
        self.main_win = main_win
        self.artists_map: Dict[str, Dict[str, List[TrackInfo]]] = {}
        self._init_ui()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Поиск исполнителя...")
        self.search_edit.textChanged.connect(self._filter_artists)
        left_layout.addWidget(self.search_edit)

        self.artist_list = QListWidget()
        self.artist_list.currentItemChanged.connect(self._on_artist_selected)
        left_layout.addWidget(self.artist_list, 1)

        splitter.addWidget(left_widget)

        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(8, 0, 0, 0)

        header_bar = QHBoxLayout()
        self.artist_title = QLabel("Выберите исполнителя")
        self.artist_title.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        self.artist_stats = QLabel("")
        # v0.3.2: цвет из темы (было #888 — не перекрашивалось темами)
        self.artist_stats.setObjectName("ArtistStats")

        self.btn_play_artist = QPushButton("Играть все треки исполнителя")
        self.btn_play_artist.setIcon(make_icon("play", QColor(T["sub1"]), 20))
        self.btn_play_artist.setVisible(False)
        self.btn_play_artist.clicked.connect(self._play_all_artist_tracks)

        header_bar.addWidget(self.artist_title)
        header_bar.addWidget(self.artist_stats)
        header_bar.addStretch()
        header_bar.addWidget(self.btn_play_artist)
        right_layout.addLayout(header_bar)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        # v0.3.2: деталь-панель альбомов прозрачна для темы (раньше
        # QScrollArea оставалась светлой в любой теме оформления)
        self.scroll_area.setObjectName("AlbumsScroll")
        self.albums_container = QWidget()
        self.albums_container.setObjectName("AlbumsContainer")
        self.albums_layout = QVBoxLayout(self.albums_container)
        self.albums_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll_area.setWidget(self.albums_container)

        right_layout.addWidget(self.scroll_area, 1)
        splitter.addWidget(right_widget)
        splitter.setSizes([240, 560])

        layout.addWidget(splitter)

    def rebuild_catalog(self, tracks: List[TrackInfo]):
        current_artist = (
            self.artist_list.currentItem().data(Qt.ItemDataRole.UserRole)
            if self.artist_list.currentItem() else None
        )

        self.artists_map.clear()
        for t in tracks:
            art = t.artist or "Неизвестный исполнитель"
            alb = t.album or "Без альбома"
            self.artists_map.setdefault(art, {}).setdefault(alb, []).append(t)

        for alb_dict in self.artists_map.values():
            for alb_tracks in alb_dict.values():
                alb_tracks.sort(key=lambda x: (x.folder_key, x.natural_file_key))

        self._populate_artist_list(current_artist)

    def _populate_artist_list(self, select_artist: Optional[str] = None):
        self.artist_list.blockSignals(True)
        self.artist_list.clear()
        query = self.search_edit.text().strip().casefold()

        selected_row = -1
        for art_name in sorted(self.artists_map.keys(), key=lambda s: s.casefold()):
            if query and query not in art_name.casefold():
                continue
            alb_dict = self.artists_map[art_name]
            total_tracks = sum(len(v) for v in alb_dict.values())
            item = QListWidgetItem(f"{art_name}  ({len(alb_dict)} альб., {total_tracks} тр.)")
            item.setData(Qt.ItemDataRole.UserRole, art_name)
            self.artist_list.addItem(item)
            if select_artist and art_name == select_artist:
                selected_row = self.artist_list.count() - 1

        self.artist_list.blockSignals(False)

        if selected_row >= 0:
            self.artist_list.setCurrentRow(selected_row)
        elif self.artist_list.count() > 0:
            self.artist_list.setCurrentRow(0)
        else:
            self._clear_albums_view()

    def focus_artist(self, artist_name: str):
        """Открывает страницу исполнителя (сбрасывая поисковый фильтр)."""
        self.search_edit.blockSignals(True)
        self.search_edit.clear()
        self.search_edit.blockSignals(False)
        self._populate_artist_list(select_artist=artist_name)

    def _filter_artists(self):
        self._populate_artist_list()

    def _clear_albums_view(self):
        while self.albums_layout.count():
            child = self.albums_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        self.artist_title.setText("Выберите исполнителя")
        self.artist_stats.setText("")
        self.btn_play_artist.setVisible(False)

    def _on_artist_selected(self, current: Optional[QListWidgetItem], _prev: Optional[QListWidgetItem]):
        if not current:
            self._clear_albums_view()
            return

        artist_name = current.data(Qt.ItemDataRole.UserRole)
        alb_dict = self.artists_map.get(artist_name, {})

        while self.albums_layout.count():
            child = self.albums_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        total_tracks = sum(len(v) for v in alb_dict.values())
        self.artist_title.setText(artist_name)
        self.artist_stats.setText(f"— Альбомов: {len(alb_dict)} | Треков: {total_tracks}")
        self.btn_play_artist.setVisible(total_tracks > 0)

        for album_name in sorted(alb_dict.keys(), key=natural_sort_key):
            album_tracks = alb_dict[album_name]
            card = self._create_album_card(album_name, album_tracks)
            self.albums_layout.addWidget(card)

    def _create_album_card(self, album_name: str, album_tracks: List[TrackInfo]) -> QFrame:
        frame = QFrame()
        # v0.3.2: стиль карточки — из шаблона тем (QFrame#AlbumCard)
        frame.setObjectName("AlbumCard")
        v_layout = QVBoxLayout(frame)

        top_row = QHBoxLayout()
        cover_lbl = QLabel()
        cover_lbl.setFixedSize(90, 90)
        cover_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cover_lbl.setObjectName("AlbumCover")  # v0.3.2: из темы

        cover_bytes = extract_cover_bytes(album_tracks[0].filepath) if album_tracks else None
        if cover_bytes:
            img = QImage.fromData(cover_bytes)
            if not img.isNull():
                cover_lbl.setPixmap(
                    QPixmap.fromImage(img).scaled(
                        90, 90,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation
                    )
                )
            else:
                # v0.3.4: векторная иконка диска вместо эмодзи
                cover_lbl.setPixmap(make_icon("album", QColor(T["muted"]), 64).pixmap(48, 48))
        else:
            cover_lbl.setPixmap(make_icon("album", QColor(T["muted"]), 64).pixmap(48, 48))

        top_row.addWidget(cover_lbl)

        info_col = QVBoxLayout()
        lbl_alb = QLabel(album_name)
        lbl_alb.setFont(QFont("Arial", 13, QFont.Weight.Bold))
        lbl_alb.setObjectName("AlbumTitle")  # v0.3.2: из темы

        folder_str = str(album_tracks[0].filepath.parent) if album_tracks else ""
        lbl_folder = QLabel(f"Папка: {folder_str}  •  Треков: {len(album_tracks)}")
        lbl_folder.setObjectName("AlbumInfo")  # v0.3.2: из темы

        btn_play_album = QPushButton("Воспроизвести альбом")
        btn_play_album.setFixedWidth(190)
        btn_play_album.setIcon(make_icon("play", QColor(T["sub1"]), 20))
        btn_play_album.clicked.connect(lambda _=False, tr=album_tracks: self._play_track_subset(tr))

        info_col.addWidget(lbl_alb)
        info_col.addWidget(lbl_folder)
        info_col.addWidget(btn_play_album)
        info_col.addStretch()

        top_row.addLayout(info_col, 1)
        v_layout.addLayout(top_row)

        track_list_widget = QListWidget()
        # v0.3.2: стиль из шаблона тем (QListWidget#AlbumTrackList)
        track_list_widget.setObjectName("AlbumTrackList")
        row_h = 26
        track_list_widget.setFixedHeight(min(240, max(60, len(album_tracks) * row_h + 10)))

        active_fp = self.main_win.playlist_model.playing_filepath
        for idx, t in enumerate(album_tracks, start=1):
            num_display = t.num_str if t.num_str != "-" else str(idx)
            is_act = (active_fp is not None and t.filepath == active_fp)
            prefix = "▶ " if is_act else ""
            item = QListWidgetItem(f"{prefix}{num_display}. {t.title}   —   [{t.filename}]   ({t.dur_str})")
            if is_act:
                item.setForeground(QColor(T["act_text"]))
            item.setData(Qt.ItemDataRole.UserRole, t.filepath)
            track_list_widget.addItem(item)

        track_list_widget.itemDoubleClicked.connect(self._on_album_track_double_clicked)
        v_layout.addWidget(track_list_widget)

        return frame

    def _on_album_track_double_clicked(self, item: QListWidgetItem):
        fpath: Path = item.data(Qt.ItemDataRole.UserRole)
        self.main_win.play_by_filepath(fpath)

    def _play_track_subset(self, subset: List[TrackInfo]):
        if subset:
            self.main_win.play_by_filepath(subset[0].filepath)

    def _play_all_artist_tracks(self):
        current = self.artist_list.currentItem()
        if not current:
            return
        artist_name = current.data(Qt.ItemDataRole.UserRole)
        alb_dict = self.artists_map.get(artist_name, {})
        for album_name in sorted(alb_dict.keys(), key=natural_sort_key):
            tracks = alb_dict[album_name]
            if tracks:
                self.main_win.play_by_filepath(tracks[0].filepath)
                break


class KaraokeLyricsWidget(QWidget):
    """Текст песни на панели управления плеером (v0.3.6).

    Единственное место, где живёт текст песни: отдельная вкладка «Текст
    песни» убрана, все стриминговые доработки v0.3.4–0.3.6 перенесены
    сюда. В окне караоке три строки: предыдущая (приглушённая), активная
    (акцент темы, пословная заливка) и следующая. При смене активной
    строки блок плавно скользит вверх, как в стриминговых сервисах:
    новая строка «подъезжает» из нижнего слота в средний. Клик по строке —
    перемотка к ней; колесо мыши — ручная прокрутка текста (автопрокрутка
    подхватывается через 3 с, как в Spotify); правый клик — меню текста
    песни: загрузить .lrc/.txt вручную, сдвиг синхронизации ±0.5 с, сброс.
    Для синхронизированных LRC-текстов во время вступлений и пауз между
    куплетами рисует обратный отсчёт до начала вокала: «♪ вокал через N с»,
    а за 3 секунды — крупные цифры «N… текст»; по огибающей громкости
    (v0.3.4) распознаёт проигрыш: «♪ Играет проигрыш • вокал через N с».
    Текст без таймингов (v0.3.6, честный режим) показывается нейтральными
    цветами без ложной подсветки «не в такт» — листается колесом мыши.
    """

    GAP_MIN_MS = 2500   # пауза короче — отсчёт не показываем
    LEAD_MS = 3000      # за 3 секунды до вокала включаются крупные цифры
    WORD_FILL_MAX_SPAN_MS = 15000  # предохранитель автораспределения слов
    MANUAL_SCROLL_S = 3.0    # пауза автопрокрутки после колеса мыши
    SLIDE_S = 0.28           # длительность скольжения при смене строки
    ACCENT_BASE_ALPHA = 90   # неспетая часть активной строки: акцент втусклую

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(112)
        self.lines: List[LyricLine] = []
        self.is_synced: bool = False
        self.position_ms: int = -1
        self.is_playing: bool = False
        # v0.3.4: огибающая громкости трека — для «умного» отсчёта (тишина → проигрыш)
        self.envelope: List[float] = []
        # v0.2.0: весь фон закрашивается непрозрачным — дешевле композиция
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Клик по строке — перемотка · колесо — прокрутка текста "
                        "· правый клик — меню текста песни")
        # Кэши (v0.2.0): расписание слов активной строки и шрифты — иначе
        # пересоздаются на каждый кадр по несколько раз
        self._wt_cache: Dict[int, List[Tuple[int, str]]] = {}
        self._font_cache: Dict[Tuple[int, bool], QFont] = {}
        # v0.3.6: ручная прокрутка колесом — центр окна строк; пока активен
        # ручной режим (3 с после последнего колеса), автоследование стоит
        self._manual_center: Optional[int] = None
        self._manual_until: float = 0.0
        # v0.3.6: скольжение при смене центральной строки (стриминговая
        # анимация): содержимое плавно сдвигается на один слот
        self._auto_center: int = -1      # центр окна в авт режиме (активная строка)
        self._slide_active: bool = False
        self._slide_from: int = 0
        self._slide_to: int = 0
        self._slide_t0: float = 0.0
        # Тикает во время игры: смены цифр отсчёта и плавная пословная
        # заливка активной строки (v0.1.9), даже если positionChanged редкий.
        # v0.2.0: запускается только когда есть что анимировать и виджет видим
        self._cd_timer = QTimer(self)
        self._cd_timer.timeout.connect(self._on_cd_tick)
        # v0.3.6: таймер скольжения ~60 FPS — плавная смена активной строки
        self._slide_timer = QTimer(self)
        self._slide_timer.setInterval(16)
        self._slide_timer.timeout.connect(self._on_slide_tick)
        self._update_timer_state()

    # --- состояние ------------------------------------------------------
    def _should_animate(self) -> bool:
        """Анимация нужна только при игре, видимости и синхронном тексте."""
        return bool(self.is_playing and self.isVisible()
                    and self.is_synced and self.lines)

    def set_envelope(self, env: List[float]):
        """v0.3.4: огибающая от EnvelopeWorker (точка ≈ 50 мс) — по ней
        караоке распознаёт тишину/проигрыш и показывает «Играет проигрыш»."""
        self.envelope = env

    def _update_timer_state(self):
        need = self._should_animate()
        if need and not self._cd_timer.isActive():
            self._cd_timer.start(150)
        elif not need and self._cd_timer.isActive():
            self._cd_timer.stop()

    def showEvent(self, event):
        super().showEvent(event)
        self._update_timer_state()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._update_timer_state()

    def _on_cd_tick(self):
        if (self.is_synced and self._manual_center is not None
                and time.monotonic() >= self._manual_until):
            # v0.3.6: ручная прокрутка истекла — плавно возвращаемся
            # к активной строке (как автопрокрутка в стриминговых сервисах)
            from_c = self._manual_center
            self._manual_center = None
            self._manual_until = 0.0
            row = self._current_row()
            if row != from_c:
                self._animate_to(row, from_center=from_c)
        if self._should_animate():
            self.update()

    def _on_slide_tick(self):
        if not self._slide_active:
            self._slide_timer.stop()
            return
        raw = (time.monotonic() - self._slide_t0) / self.SLIDE_S
        if raw >= 1.0:
            self._slide_active = False
            self._slide_timer.stop()
        self.update()

    # --- данные ---------------------------------------------------------
    def set_lyrics(self, lines: List[LyricLine], is_synced: bool):
        self.lines = list(lines or [])
        self.is_synced = bool(is_synced)
        self.position_ms = -1
        self._wt_cache = {}  # v0.2.0: сброс кэша расписаний слов
        # v0.3.6: сброс ручной прокрутки и скольжения при смене трека
        self._manual_center = None if is_synced else min(1, max(0, len(self.lines) - 1))
        self._manual_until = 0.0
        self._auto_center = -1
        self._slide_active = False
        self._slide_timer.stop()
        self.update()
        self._update_timer_state()

    def set_playing(self, playing: bool):
        self.is_playing = playing
        # Мгновенно перерисовываем: спрятать «вокал через N с» на паузе и наоборот
        self.update()
        self._update_timer_state()

    def sync_position(self, pos_ms: int) -> Tuple[int, str]:
        """v0.3.6: синхронизация от таймера 100 мс главного окна.

        Возвращает (индекс активной строки, текст) — их же использует
        миниплеер для подсветки строки в «Словах песни»."""
        self.position_ms = pos_ms
        idx = self._current_row()
        if self.is_synced:
            prev = self._auto_center
            self._auto_center = idx
            if not self._manual_active() and idx != prev:
                self._animate_to(idx, from_center=prev)
        if self.isVisible():  # v0.2.0: не дёргаем перерисовку у скрытого виджета
            self.update()
        txt = self.lines[idx].text if 0 <= idx < len(self.lines) else ""
        return idx, txt

    # --- геометрия окна строк -------------------------------------------
    def _current_row(self) -> int:
        idx = -1
        # v0.3.4: поиск активной строки с учётом ручного сдвига синхронизации
        actual_pos = self.position_ms - getattr(self.window(), "lyric_offset_ms", 0)
        for i, ln in enumerate(self.lines):
            if actual_pos >= ln.time_ms:
                idx = i
            else:
                break
        return idx

    def _manual_active(self) -> bool:
        """Ручная прокрутка активна: после колеса прошло меньше 3 с."""
        return bool(self.is_synced and self._manual_center is not None
                    and time.monotonic() < self._manual_until)

    def _visual_center(self) -> int:
        """Какая строка сейчас в среднем слоте (с учётом скольжения)."""
        if self._slide_active:
            return self._slide_to
        if self.is_synced:
            if self._manual_active():
                return self._manual_center
            return self._auto_center
        return self._manual_center if self._manual_center is not None else 1

    def _animate_to(self, target: int, from_center: Optional[int] = None):
        """Плавно сдвинуть окно строк к target (на один слот).

        Большой скачок (перемотка/новый трек) и переход из вступления —
        мгновенно, как «большой прыжок» в стриминговом виде v0.3.5."""
        cur = self._visual_center() if from_center is None else from_center
        if not self.lines:
            return
        target = max(0, min(len(self.lines) - 1, int(target)))
        if target == cur and not self._slide_active:
            return
        if cur < 0 or target == cur or abs(target - cur) > 1:
            self._slide_active = False
            self._slide_timer.stop()
        else:
            self._slide_from = cur
            self._slide_to = target
            self._slide_t0 = time.monotonic()
            self._slide_active = True
            self._slide_timer.start()
        if self.is_synced and not self._manual_active():
            self._auto_center = target

    def _slide_dy(self, slot_h: float) -> float:
        """Вертикальный сдвиг содержимого при скольжении, px (easeOutCubic).

        Положительный — контент начинается ниже и скользит вверх (смена на
        следующую строку), отрицательный — выше и опускается (назад)."""
        if not self._slide_active:
            return 0.0
        raw = (time.monotonic() - self._slide_t0) / self.SLIDE_S
        if raw >= 1.0:
            self._slide_active = False
            self._slide_timer.stop()
            return 0.0
        p = 1.0 - (1.0 - raw) ** 3  # easeOutCubic — быстро стартует, мягко доезжает
        return (self._slide_to - self._slide_from) * (1.0 - p) * slot_h

    # --- мышь -------------------------------------------------------------
    def wheelEvent(self, event):
        """v0.3.6 (перенос из стриминговой вкладки): колесо — ручная
        прокрутка текста по одной строке на щелчок; у синхронного текста
        автопрокрутка возобновляется через 3 с."""
        delta = event.angleDelta().y()
        if delta == 0 or not self.lines:
            return
        step = 1 if delta < 0 else -1  # вниз — к следующим строкам
        cur = self._visual_center()
        if self.is_synced:
            self._manual_center = int(max(0, min(
                len(self.lines) - 1, cur + step)))
            self._manual_until = time.monotonic() + self.MANUAL_SCROLL_S
        else:
            base = self._manual_center if self._manual_center is not None else 1
            self._manual_center = int(max(0, min(
                len(self.lines) - 1, base + step)))
        self._animate_to(self._manual_center, from_center=cur)
        self.update()

    def mouseReleaseEvent(self, event):
        """v0.3.6 (перенос): клик по строке — перемотка к её времени."""
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if not (self.is_synced and self.lines):
            return
        center = self._visual_center()
        if center < 0:
            return
        slot_h = self.height() / 3.0
        pt = event.position() if hasattr(event, "position") else event.localPos()
        r = int(pt.y() // slot_h) if slot_h > 0 else 1
        i = center - 1 + r
        if 0 <= i < len(self.lines):
            self.window().player.setPosition(int(self.lines[i].time_ms))

    def _build_text_menu(self) -> QMenu:
        """Меню текста песни (v0.3.6): бывшие кнопки убранной вкладки —
        загрузка .lrc/.txt и сдвиг синхронизации ±0.5 с. Вынесено отдельным
        методом, чтобы тесты могли проверить состав меню без exec()."""
        menu = QMenu(self)
        off = int(getattr(self.window(), "lyric_offset_ms", 0))
        hdr = menu.addAction("Сдвиг текста: нет" if not off
                             else f"Сдвиг текста: {off / 1000.0:+.1f} с")
        hdr.setEnabled(False)
        f = menu.font()
        f.setItalic(True)
        hdr.setFont(f)
        menu.addSeparator()
        a_load = menu.addAction("Загрузить .lrc / .txt вручную…")
        a_minus = menu.addAction("Сдвинуть текст назад (−0.5 с)")
        a_plus = menu.addAction("Сдвинуть текст вперёд (+0.5 с)")
        a_reset = menu.addAction("Сбросить сдвиг")
        a_reset.setEnabled(bool(off))
        # v0.3.6: действия подключены сигналами — работают и из exec(),
        # и при прямом trigger() в тестах
        a_load.triggered.connect(self._open_manual_lrc)
        a_minus.triggered.connect(lambda: self._adjust_sync(-500))
        a_plus.triggered.connect(lambda: self._adjust_sync(500))
        a_reset.triggered.connect(
            lambda: self._adjust_sync(
                -int(getattr(self.window(), "lyric_offset_ms", 0))))
        menu._hox_actions = {"load": a_load, "minus": a_minus,
                             "plus": a_plus, "reset": a_reset}
        return menu

    def contextMenuEvent(self, event):
        menu = self._build_text_menu()
        menu.exec(event.globalPos())

    def _adjust_sync(self, delta_ms: int):
        """v0.3.4→v0.3.6: ручной сдвиг синхронизации текста относительно
        звука; действует на караоке и миниплеер до смены трека."""
        win = self.window()
        win.lyric_offset_ms = int(getattr(win, "lyric_offset_ms", 0)) + delta_ms
        win.on_position_changed(win.player.position())

    def _open_manual_lrc(self):
        fpath, _ = QFileDialog.getOpenFileName(
            self, "Выберите файл текста песни", "",
            "Lyrics (*.lrc *.txt);;Все файлы (*.*)"
        )
        if fpath:
            for enc in ('utf-8', 'utf-8-sig', 'cp1251'):
                try:
                    raw = Path(fpath).read_text(encoding=enc)
                    dur = max(1.0, self.window().player.duration() / 1000.0)
                    lines, is_sync = parse_lyrics_text(raw, dur)
                    self.set_lyrics(lines, is_sync)
                    self.window().mini_player.set_mini_lyrics(lines)
                    break
                except Exception:
                    pass

    # --- отрисовка --------------------------------------------------------
    def _word_times_for(self, idx: int) -> List[Tuple[int, str]]:
        """Расписание слов активной строки: [(time_ms, слово)].

        Есть пословные метки A2 (<mm:ss.xx>) — берём их; для синхронизированной
        строки без меток слова распределяются пропорционально длине слов по
        длительности строки (до следующей строки, не дольше 15 с). Для
        несинхронизированного текста пословной подсветки нет.
        """
        if not (0 <= idx < len(self.lines)):
            return []
        ln = self.lines[idx]
        if ln.words:
            return ln.words
        if not self.is_synced:
            return []
        if idx + 1 < len(self.lines):
            span = self.lines[idx + 1].time_ms - ln.time_ms
        else:
            span = max(2000, len(ln.text) * 55)  # оценка для последней строки
        span = min(span, self.WORD_FILL_MAX_SPAN_MS)
        toks = [t for t in ln.text.split(" ") if t]
        if len(toks) < 2 or span <= 0:
            return []
        # v0.2.0: расписание слов кэшируется по номеру строки — раньше
        # пересчитывалось на каждом кадре отрисовки (~12 раз/с)
        cached = self._wt_cache.get(idx)
        if cached is not None:
            return cached
        weights = [max(2, len(t) + 1) for t in toks]
        total_w = sum(weights)
        out: List[Tuple[int, str]] = []
        acc = 0
        for tok, wgt in zip(toks, weights):
            out.append((ln.time_ms + int(span * acc / total_w), tok))
            acc += wgt
        self._wt_cache[idx] = out
        return out

    def _draw_active_line(self, p: QPainter, idx: int, rect: QRectF,
                          base_size: int = 13):
        """Активная строка с пословной подсветкой (v0.1.9) и стриминговой
        базой (v0.3.6): неспетая часть — приглушённый акцент (строка
        «загорается» сразу, без «моргания» на границах строк), спетые
        слова — полный акцент, текущее слово плавно заливается слева
        направо. Длинные строки ужимаются по размеру шрифта."""
        text = self.lines[idx].text
        wt = self._word_times_for(idx) if (self.is_synced and self.position_ms >= 0) else []
        if not wt:
            fm0 = QFontMetrics(self._font(base_size, True))
            p.setFont(self._font(base_size, True))
            p.setPen(QColor(T["kar_active"]))
            elided = fm0.elidedText(text, Qt.TextElideMode.ElideRight,
                                    int(rect.width()))
            p.drawText(rect, Qt.AlignmentFlag.AlignHCenter
                       | Qt.AlignmentFlag.AlignVCenter, elided)
            return

        words = [w for _, w in wt]
        full = " ".join(words)
        avail = rect.width()
        # Подбор размера шрифта: строка должна влезать целиком
        size = base_size
        fm_mid = QFontMetrics(self._font(size, True))
        while size > max(9, base_size - 4) and fm_mid.horizontalAdvance(full) > avail:
            size -= 1
            fm_mid = QFontMetrics(self._font(size, True))
        p.setFont(self._font(size, True))
        baseline = rect.y() + rect.height() / 2.0 + fm_mid.ascent() / 2.0 - 1

        total_w = float(fm_mid.horizontalAdvance(full))
        fits = total_w <= avail
        if fits:
            draw_text = full
            k = 1.0
        else:
            # Крайний случай: эллипсис, пословные границы — пропорционально
            draw_text = fm_mid.elidedText(full, Qt.TextElideMode.ElideRight,
                                          int(avail))
            k = float(fm_mid.horizontalAdvance(draw_text)) / total_w if total_w else 1.0

        # Префиксные границы слов (масштаб k при эллипсисе)
        starts: List[float] = []
        ends: List[float] = []
        for i in range(len(words)):
            prefix = " ".join(words[:i]) + (" " if i else "")
            starts.append(float(fm_mid.horizontalAdvance(prefix)) * k)
            ends.append(starts[i] + float(fm_mid.horizontalAdvance(words[i])) * k)
        x0 = rect.x() + (avail - float(fm_mid.horizontalAdvance(draw_text))) / 2.0

        # 1) Базовый слой — v0.3.6: акцент втусклую (как в стриминговых
        #    сервисах): строка подсвечена сразу на старте строки
        base_c = QColor(T["kar_active"])
        base_c.setAlpha(self.ACCENT_BASE_ALPHA)
        p.setPen(base_c)
        p.drawText(QPointF(x0, baseline), draw_text)

        # 2) Спетый слой — полный акцент, клип до текущей позиции
        #    (v0.3.4: позиция с учётом ручного сдвига синхронизации)
        actual_pos = self.position_ms - getattr(self.window(), "lyric_offset_ms", 0)
        cur_i = -1
        for i, (t_ms, _w) in enumerate(wt):
            if actual_pos >= t_ms:
                cur_i = i
            else:
                break
        sung_px = 0.0
        if cur_i >= 0:
            t0 = wt[cur_i][0]
            t1 = wt[cur_i + 1][0] if cur_i + 1 < len(wt) else None
            if t1 is None or actual_pos >= t1:
                sung_px = x0 + ends[cur_i]         # слово спето целиком
            else:
                frac = max(0.0, min(1.0, (actual_pos - t0) / max(1, t1 - t0)))
                sung_px = x0 + starts[cur_i] + (ends[cur_i] - starts[cur_i]) * frac
        if sung_px > x0:
            p.save()
            p.setClipRect(QRectF(x0 - 1, rect.y(),
                                 sung_px - x0 + 1, rect.height()))
            p.setPen(QColor(T["kar_active"]))
            p.drawText(QPointF(x0, baseline), draw_text)
            p.restore()

    def _font(self, size: int, bold: bool) -> QFont:
        # v0.2.0: шрифты кэшируются — QFont создавался заново на каждый кадр
        key = (size, bold)
        f = self._font_cache.get(key)
        if f is None:
            f = QFont()
            f.setPointSize(size)
            f.setBold(bold)
            self._font_cache[key] = f
        return f

    def _is_instrumental_now(self, actual_pos: int) -> bool:
        """v0.3.4: «умный» таймер — если в ближайшие ~2 с по огибающей
        тишина (средняя громкость < 0.1), сейчас играет проигрыш."""
        if not self.envelope:
            return False
        env_idx = int(actual_pos / 50)
        future_idx = min(len(self.envelope) - 1, env_idx + 40)
        if env_idx < 0 or env_idx >= len(self.envelope) or future_idx <= 0:
            return False
        current_vol = sum(self.envelope[env_idx:future_idx]) \
            / max(1, future_idx - env_idx)
        return current_vol < 0.1

    def _draw_countdown(self, p: QPainter, gap_ms: int, next_text: str,
                        rect: QRectF, is_instrumental: bool = False):
        """Отсчёт до вокала: > 3 с — «♪ вокал через N с», ≤ 3 с — «3… текст».
        v0.3.4: если по огибающей слышна тишина — «♪ Играет проигрыш • вокал через N с»."""
        remain_s = max(1, int(math.ceil(gap_ms / 1000.0)))
        if gap_ms > self.LEAD_MS:
            p.setPen(QColor(T["kar_hint"]))
            p.setFont(self._font(11, False))
            text = (f"♪ Играет проигрыш • вокал через {remain_s} с"
                    if is_instrumental else f"♪ вокал через {remain_s} с")
            p.drawText(rect, Qt.AlignmentFlag.AlignHCenter
                       | Qt.AlignmentFlag.AlignVCenter, text)
        else:
            fm = QFontMetrics(self._font(14, True))
            text = fm.elidedText(next_text, Qt.TextElideMode.ElideRight,
                                 int(rect.width()) - 52)
            p.setPen(QColor(T["kar_active"]))
            p.setFont(self._font(14, True))
            p.drawText(rect, Qt.AlignmentFlag.AlignHCenter
                       | Qt.AlignmentFlag.AlignVCenter,
                       f"{remain_s}… {text}")

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor(T["viz_bg"]))
        p.setPen(QColor(T["viz_border"]))
        p.drawRoundedRect(0, 0, w - 1, h - 1, 10, 10)

        if not self.lines:
            p.setPen(QColor(T["kar_hint"]))
            p.setFont(self._font(11, False))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "Текст песни не найден (.lrc или тег в файле)")
            p.end()
            return

        slot_h = h / 3.0
        # v0.3.6: адаптивные размеры — в развёрнутом окне (112 px) строки
        # крупнее, в компактном профиле (56 px) — прежние 13/11 pt
        big = slot_h >= 28.0
        side_size = 13 if big else 11
        act_size = 16 if slot_h >= 34.0 else (14 if big else 13)
        flags = (Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        actual_pos = self.position_ms - getattr(self.window(), "lyric_offset_ms", 0)

        # Вступление до первого куплета (только синхронный текст, не ручной режим)
        if (self.is_synced and not self._manual_active()
                and (self._visual_center() < 0
                     or actual_pos < self.lines[0].time_ms)):
            mid = QRectF(8, slot_h + 2, w - 16, slot_h - 4)
            bot = QRectF(8, 2 * slot_h + 4, w - 16, slot_h - 8)
            if actual_pos >= 0 and actual_pos < self.lines[0].time_ms:
                gap = self.lines[0].time_ms - actual_pos
                if gap >= self.GAP_MIN_MS:
                    # v0.3.4: «умный» отсчёт по огибающей (тишина → проигрыш)
                    self._draw_countdown(p, gap, self.lines[0].text, mid,
                                         self._is_instrumental_now(actual_pos))
                else:
                    p.setPen(QColor(T["kar_next"]))
                    p.setFont(self._font(side_size, False))
                    p.drawText(mid, flags, self.lines[0].text)
            if 1 < len(self.lines):
                p.setPen(QColor(T["kar_next"]))
                p.setFont(self._font(side_size, False))
                p.drawText(bot, flags, self.lines[1].text)
            p.end()
            return

        center = self._visual_center()
        dy = self._slide_dy(slot_h)
        row = self._current_row()  # реальная активная строка (для стиля слотов)
        fm_side = QFontMetrics(self._font(side_size, False))

        for r in range(-1, 4):
            i = center - 1 + r
            if not (0 <= i < len(self.lines)):
                continue
            y = r * slot_h + dy
            if y + slot_h < 0 or y > h:
                continue
            rect = QRectF(8, y + 2, w - 16, slot_h - 4)
            if self.is_synced and i == row:
                # Активная строка — пословная подсветка акцентом темы
                self._draw_active_line(p, i, rect, act_size)
            elif not self.is_synced:
                # v0.3.6 честный режим: текст без таймингов — нейтральные
                # цвета, без ложной подсветки «не в такт»
                p.setPen(QColor(T["kar_next"]))
                p.setFont(self._font(side_size, False))
                p.drawText(rect, flags,
                           fm_side.elidedText(self.lines[i].text,
                                              Qt.TextElideMode.ElideRight,
                                              int(w - 16)))
            elif i < row:
                p.setPen(QColor(T["kar_prev"]))
                p.setFont(self._font(side_size, False))
                p.drawText(rect, flags,
                           fm_side.elidedText(self.lines[i].text,
                                              Qt.TextElideMode.ElideRight,
                                              int(w - 16)))
            else:
                # Следующая строка — либо текст, либо обратный отсчёт до неё
                gap = self.lines[i].time_ms - actual_pos
                if (i == row + 1 and not self._manual_active()
                        and self.is_synced and gap >= self.GAP_MIN_MS):
                    self._draw_countdown(p, gap, self.lines[i].text, rect,
                                         self._is_instrumental_now(actual_pos))
                else:
                    p.setPen(QColor(T["kar_next"]))
                    p.setFont(self._font(side_size, False))
                    p.drawText(rect, flags,
                               fm_side.elidedText(self.lines[i].text,
                                                  Qt.TextElideMode.ElideRight,
                                                  int(w - 16)))
        p.end()

# ======================================================================
# v0.3.0: Система тем оформления — «Aurora Neon» и переключаемые темы.
# Все цвета интерфейса собраны в словарях THEMES: QSS строится из
# шаблона подстановкой значений (string.Template), а рисуемые вручную
# виджеты (визуализатор, караоке, свечения обложки) читают цвета из
# глобального словаря T активной темы. Выбор темы — в ⚙ Настройках,
# применяется мгновенно и сохраняется между запусками.
# ======================================================================
THEME_ORDER = ["aurora", "ember", "classic", "sunset", "emerald"]


def _theme(**over):
    """Базовая палитра (классическая Hox Neon) с перекрытиями темы."""
    base = dict(
        label="", aurora="0",
        # Окно и фон
        bg="#10131f", text="#e8ecfa", muted="#8b93b2",
        central1="#10131f", central2="#10131f", central3="#10131f",
        glow1="#00e5ff", glow2="#536dfe",
        # Левая карточка «Now Playing»
        card_top="#171c33", card_bot="#10131f", card_border="#242b47",
        # Кнопки
        btn_bg="#1a2036", btn_text="#dfe6ff", btn_border="#2b3352",
        btn_hover_bg="#1e2540", btn_pressed="#26305a",
        btn_dis_text="#5a6076", btn_dis_border="#232941", btn_dis_bg="#161b2e",
        round_text="#cfe9ff", round_border="#323b5e", round_hover_bg="#212a4a",
        play_text="#071318",
        play_g1="#00e5ff", play_g2="#0a9cc9",
        play_hover_g1="#2beaff", play_hover_g2="#14b4e6",
        play_press_g1="#00c8e8", play_press_g2="#0880aa",
        chip_text="#aeb6d8", chip_border="#2b3352", chip_on_bg="#0f2430",
        # Слайдеры
        groove="#222840", sub1="#00e5ff", sub2="#536dfe",
        handle_border="#00e5ff", handle_hover="#dffbff",
        # Вкладки
        pane_bg="#12162a", pane_border="#242b47",
        tab_text="#8b93b2", tab_sel="#00e5ff", tab_hover="#c9d2f5",
        # Таблица плейлиста
        table_bg="#12162a", table_alt="#161b31", table_grid="#1d2340",
        table_text="#dfe4f7", sel_bg="#27406b",
        header_bg="#141930", header_border="#242b47", header_text="#8b93b2",
        # Комбобоксы, прогресс, статус-бар, скроллы, тултипы, поля ввода
        combo_bg="#1a2036", combo_border="#2b3352", combo_text="#dfe4f7",
        progress_bg="#1a2036", progress_border="#2b3352",
        status_bg="#0d101d", status_text="#8b93b2", status_border="#1f2540",
        scroll_h="#2b3352", scroll_hov="#00e5ff",
        tip_bg="#1a2036", tip_text="#e8ecfa", tip_border="#00e5ff",
        input_bg="#12162a", input_border="#2b3352", input_sel="#27406b",
        check_text="#c9d2f5", check_border="#2b3352", check_bg="#12162a",
        check_on="#00e5ff",
        menu_bg="#161b31", menu_border="#2b3352", menu_sel="#27406b",
        group_border="#242b47", group_text="#c9d2f5", group_title="#00e5ff",
        list_bg="#12162a", list_border="#242b47", list_text="#dfe4f7",
        wn_bg="#141622", wn_browser_bg="#191c2b", wn_browser_border="#2e344e",
        # Рисуемые вручную виджеты
        viz_bg="#0d1120", viz_border="#262d49",
        bar1="#00e5ff", bar2="#536dfe", bar3="#ff4081", peak="#9feaff",
        kar_active="#00e5ff", kar_base="#39406a", kar_prev="#4a5068",
        kar_next="#8b93b2", kar_hint="#6d7389",
        act_text="#00e5ff", act_bg="#1e2746",
        # Мини-плеер
        mini_g1="#151a30", mini_g2="#0d101d",
        mini_lyric_bg="#1b1f32",
    )
    base.update(over)
    return base


THEMES: Dict[str, Dict[str, str]] = {
    # Новая тема по макету «Aurora Neon»: аврора-подсветка фона,
    # полупрозрачные «стеклянные» карточки, мягкие свечения
    "aurora": _theme(
        label="Aurora Neon — аврора-неон (новая)",
        aurora="1",
        bg="#0a0d18", text="#eef1fb", muted="#8a91ad",
        central1="#10142a", central2="#0d1122", central3="#0a0d18",
        glow1="#00e5ff", glow2="#536dfe",
        card_top="rgba(26,32,62,205)", card_bot="rgba(13,17,34,220)",
        card_border="rgba(0,229,255,40)",
        btn_bg="rgba(26,32,56,190)", btn_border="rgba(0,229,255,42)",
        btn_hover_bg="rgba(0,229,255,26)",
        round_border="rgba(0,229,255,60)", round_hover_bg="rgba(0,229,255,22)",
        chip_border="rgba(0,229,255,36)", chip_on_bg="rgba(0,229,255,24)",
        groove="rgba(255,255,255,26)",
        pane_bg="rgba(18,22,42,200)", pane_border="rgba(0,229,255,26)",
        table_bg="rgba(18,22,42,196)", table_alt="rgba(255,255,255,6)",
        table_grid="rgba(255,255,255,10)",
        header_bg="rgba(20,25,48,230)", header_border="rgba(0,229,255,28)",
        input_bg="rgba(13,17,34,200)", input_border="rgba(0,229,255,34)",
        check_bg="rgba(13,17,34,200)", check_border="rgba(0,229,255,34)",
        menu_bg="rgba(22,27,49,240)", menu_border="rgba(0,229,255,40)",
        list_bg="rgba(18,22,42,196)", list_border="rgba(0,229,255,26)",
        status_bg="rgba(8,11,20,220)", status_border="rgba(0,229,255,20)",
        scroll_h="rgba(255,255,255,26)",
        combo_bg="rgba(26,32,56,190)", combo_border="rgba(0,229,255,42)",
        progress_bg="rgba(26,32,56,190)", progress_border="rgba(0,229,255,42)",
        viz_bg="rgba(10,13,24,190)", viz_border="rgba(0,229,255,30)",
        mini_g1="rgba(21,26,48,250)", mini_g2="rgba(13,16,29,250)",
        mini_lyric_bg="rgba(0,229,255,26)",
        wn_bg="#10131f", wn_browser_bg="#12162a", wn_browser_border="#242b47",
    ),
    # v0.3.1: «Midnight Ember» — скин по референсу пользователя:
    # глубокий угольный (тёплый, без синевы) фон и янтарно-оранжевые
    # акценты; активная строка плейлиста и караоке подсвечены янтарём.
    "ember": _theme(
        label="Midnight Ember — янтарные угли",
        aurora="1",
        bg="#0d0c0a", text="#f1ece2", muted="#8f887b",
        central1="#171310", central2="#100e0b", central3="#0b0a08",
        glow1="#f5a623", glow2="#d96c2f",
        card_top="rgba(38,31,22,215)", card_bot="rgba(20,16,11,225)",
        card_border="rgba(245,166,35,42)",
        btn_bg="rgba(40,34,26,205)", btn_text="#e8e2d6",
        btn_border="rgba(245,166,35,36)",
        btn_hover_bg="rgba(245,166,35,24)", btn_pressed="rgba(245,166,35,36)",
        btn_dis_text="#6a645a", btn_dis_border="#2a251e", btn_dis_bg="#1a1712",
        round_text="#e8e2d6", round_border="rgba(245,166,35,50)",
        round_hover_bg="rgba(245,166,35,18)",
        play_text="#231503",
        play_g1="#f7b733", play_g2="#e08e1b",
        play_hover_g1="#ffc95e", play_hover_g2="#f0a12f",
        play_press_g1="#e0a226", play_press_g2="#c67a12",
        chip_text="#b5ac9c", chip_border="rgba(245,166,35,34)",
        chip_on_bg="rgba(245,166,35,24)",
        groove="rgba(255,255,255,24)", sub1="#f5a623", sub2="#e07b39",
        handle_border="#f5a623", handle_hover="#ffe9c2",
        pane_bg="rgba(26,23,18,228)", pane_border="rgba(245,166,35,26)",
        tab_text="#8f887b", tab_sel="#f5a623", tab_hover="#d9d2c4",
        table_bg="rgba(24,21,16,228)", table_alt="rgba(255,255,255,6)",
        table_grid="rgba(255,255,255,9)", table_text="#e8e2d6",
        sel_bg="#3f2d13",
        header_bg="rgba(29,25,19,242)", header_border="rgba(245,166,35,26)",
        header_text="#8f887b",
        combo_bg="rgba(30,26,20,222)", combo_border="rgba(245,166,35,40)",
        combo_text="#e8e2d6",
        progress_bg="rgba(30,26,20,222)", progress_border="rgba(245,166,35,40)",
        status_bg="rgba(13,11,8,238)", status_text="#8f887b",
        status_border="rgba(245,166,35,20)",
        scroll_h="rgba(255,255,255,24)", scroll_hov="#f5a623",
        tip_bg="rgba(30,26,20,235)", tip_text="#f1ece2", tip_border="#f5a623",
        input_bg="rgba(19,16,12,222)", input_border="rgba(245,166,35,32)",
        input_sel="#3f2d13",
        check_text="#d9d2c4", check_border="rgba(245,166,35,32)",
        check_bg="rgba(19,16,12,222)", check_on="#f5a623",
        menu_bg="rgba(27,23,18,246)", menu_border="rgba(245,166,35,38)",
        menu_sel="#3f2d13",
        group_border="rgba(245,166,35,30)", group_text="#d9d2c4",
        group_title="#f5a623",
        list_bg="rgba(24,21,16,228)", list_border="rgba(245,166,35,26)",
        list_text="#e8e2d6",
        wn_bg="#121009", wn_browser_bg="#16130e",
        wn_browser_border="#2c261d",
        viz_bg="rgba(15,13,9,200)", viz_border="rgba(245,166,35,30)",
        bar1="#f5a623", bar2="#e07b39", bar3="#ffd166", peak="#ffedd0",
        kar_active="#f5b04a", kar_base="#4a4438", kar_prev="#453f34",
        kar_next="#a89f8d", kar_hint="#8f887b",
        act_text="#f5b04a", act_bg="#2b2013",
        mini_g1="rgba(30,26,20,250)", mini_g2="rgba(16,13,9,250)",
        mini_lyric_bg="rgba(245,166,35,26)",
    ),
    # Классика v0.1.8–0.2.2 — прежний вид плеера без авроры
    "classic": _theme(label="Hox Neon — классика (как в 0.2.2)"),
    # Закат-ретро: маджента и янтарь на фиолетовом
    "sunset": _theme(
        label="Закат-ретро — маджента и янтарь",
        aurora="1",
        bg="#120818", text="#fdf1f7", muted="#a08ba0",
        central1="#1d0e29", central2="#170a20", central3="#100615",
        glow1="#ff4081", glow2="#ff8a65",
        card_top="rgba(50,22,58,210)", card_bot="rgba(28,11,36,225)",
        card_border="rgba(255,79,216,45)",
        play_text="#1a0510",
        play_g1="#ff4fd8", play_g2="#c2185b",
        play_hover_g1="#ff7ce4", play_hover_g2="#d9327a",
        play_press_g1="#e643c2", play_press_g2="#9c1450",
        sub1="#ff4fd8", sub2="#ff8a65", handle_border="#ff4fd8",
        handle_hover="#ffd9f4", scroll_hov="#ff4fd8",
        tab_sel="#ff4fd8", check_on="#ff4fd8", group_title="#ff4fd8",
        tip_border="#ff4fd8",
        act_text="#ff8ae4", act_bg="#3a1636",
        kar_active="#ff8ae4", kar_base="#63415e", kar_prev="#5c4258",
        kar_next="#b393ae", kar_hint="#a08ba0",
        bar1="#ff8a65", bar2="#ff4fd8", bar3="#ffd166", peak="#ffe4f5",
        viz_border="rgba(255,79,216,36)",
        btn_border="rgba(255,79,216,40)", btn_hover_bg="rgba(255,79,216,24)",
        round_border="rgba(255,79,216,56)", round_hover_bg="rgba(255,79,216,20)",
        chip_border="rgba(255,79,216,36)",
        sel_bg="#5c1b4e",
        pane_bg="rgba(38,17,46,225)", pane_border="rgba(255,79,216,28)",
        table_bg="rgba(35,15,43,225)", table_alt="rgba(255,255,255,7)",
        table_grid="rgba(255,255,255,12)", header_bg="rgba(43,19,52,240)",
        header_border="rgba(255,79,216,30)",
        input_bg="rgba(30,13,37,220)", input_border="rgba(255,79,216,34)",
        check_bg="rgba(30,13,37,220)", check_border="rgba(255,79,216,34)",
        menu_bg="rgba(40,18,48,245)", menu_border="rgba(255,79,216,40)",
        list_bg="rgba(35,15,43,225)", list_border="rgba(255,79,216,28)",
        status_bg="rgba(18,8,24,235)", status_border="rgba(255,79,216,22)",
        scroll_h="rgba(255,255,255,26)",
        combo_bg="rgba(44,20,54,220)", combo_border="rgba(255,79,216,42)",
        progress_bg="rgba(44,20,54,220)", progress_border="rgba(255,79,216,42)",
        mini_g1="rgba(40,16,46,250)", mini_g2="rgba(24,9,30,250)",
        mini_lyric_bg="rgba(255,79,216,30)",
    ),
    # Изумруд: зелёный неон на тёмном чае
    "emerald": _theme(
        label="Изумруд — зелёный неон",
        aurora="1",
        bg="#06120e", text="#eafcf3", muted="#7fa394",
        central1="#0c1f19", central2="#091712", central3="#050f0b",
        glow1="#2ee6a8", glow2="#00b0ff",
        card_top="rgba(14,42,34,210)", card_bot="rgba(7,19,15,225)",
        card_border="rgba(46,230,168,45)",
        play_text="#04170f",
        play_g1="#2ee6a8", play_g2="#00a37e",
        play_hover_g1="#5cffc9", play_hover_g2="#12c498",
        play_press_g1="#25c791", play_press_g2="#00856a",
        sub1="#2ee6a8", sub2="#00b0ff", handle_border="#2ee6a8",
        handle_hover="#d2ffef", scroll_hov="#2ee6a8",
        tab_sel="#2ee6a8", check_on="#2ee6a8", group_title="#2ee6a8",
        tip_border="#2ee6a8",
        act_text="#7fffd4", act_bg="#12352c",
        kar_active="#7fffd4", kar_base="#3b5c50", kar_prev="#375247",
        kar_next="#8fb8a8", kar_hint="#7fa394",
        bar1="#2ee6a8", bar2="#00b0ff", bar3="#b2ff59", peak="#e4fff2",
        viz_border="rgba(46,230,168,36)",
        btn_border="rgba(46,230,168,40)", btn_hover_bg="rgba(46,230,168,22)",
        round_border="rgba(46,230,168,56)", round_hover_bg="rgba(46,230,168,20)",
        chip_border="rgba(46,230,168,36)",
        sel_bg="#0f4a3a",
        pane_bg="rgba(10,32,24,225)", pane_border="rgba(46,230,168,28)",
        table_bg="rgba(9,29,22,225)", table_alt="rgba(255,255,255,6)",
        table_grid="rgba(255,255,255,10)", header_bg="rgba(12,36,27,240)",
        header_border="rgba(46,230,168,30)",
        input_bg="rgba(8,24,18,220)", input_border="rgba(46,230,168,34)",
        check_bg="rgba(8,24,18,220)", check_border="rgba(46,230,168,34)",
        menu_bg="rgba(12,34,26,245)", menu_border="rgba(46,230,168,40)",
        list_bg="rgba(9,29,22,225)", list_border="rgba(46,230,168,28)",
        status_bg="rgba(5,14,11,235)", status_border="rgba(46,230,168,22)",
        scroll_h="rgba(255,255,255,24)",
        combo_bg="rgba(11,34,26,220)", combo_border="rgba(46,230,168,42)",
        progress_bg="rgba(11,34,26,220)", progress_border="rgba(46,230,168,42)",
        mini_g1="rgba(13,36,29,250)", mini_g2="rgba(6,17,13,250)",
        mini_lyric_bg="rgba(46,230,168,28)",
    ),
}

# Активная тема (глобально). По умолчанию — новая Aurora Neon.
T: Dict[str, str] = THEMES["aurora"]


def _accent_qc(alpha: int = 255) -> QColor:
    """Акцентный цвет активной темы с заданной альфой (для свечений)."""
    c = QColor(T["sub1"] if T.get("sub1") else "#00e5ff")
    c.setAlpha(max(0, min(255, alpha)))
    return c


def theme_key_from_settings(settings: QSettings) -> str:
    """Читает сохранённую тему; неизвестный ключ -> Aurora Neon."""
    key = str(settings.value("theme", "aurora"))
    return key if key in THEMES else "aurora"


APP_QSS_TEMPLATE = string.Template("""
QMainWindow, QDialog { background-color: $bg; }
QWidget { color: $text; }
QLabel { background: transparent; color: $text; }

QFrame#LeftCard {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 $card_top, stop:1 $card_bot);
    border: 1px solid $card_border; border-radius: 14px;
}

QPushButton {
    background-color: $btn_bg; color: $btn_text;
    border: 1px solid $btn_border; border-radius: 8px; padding: 6px 10px;
}
QPushButton:hover { border-color: $sub1; color: #ffffff; background-color: $btn_hover_bg; }
QPushButton:pressed { background-color: $btn_pressed; }
QPushButton:disabled { color: $btn_dis_text; border-color: $btn_dis_border; background-color: $btn_dis_bg; }

QPushButton#roundBtn {
    border-radius: 23px; padding: 0; font-size: 15px; color: $round_text;
    background-color: $btn_bg; border: 1px solid $round_border;
}
QPushButton#roundBtn:hover { border-color: $sub1; color: #ffffff; background-color: $round_hover_bg; }
QPushButton#playBig {
    border-radius: 29px; padding: 0; font-size: 20px; color: $play_text; border: none;
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 $play_g1, stop:1 $play_g2);
}
QPushButton#playBig:hover {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 $play_hover_g1, stop:1 $play_hover_g2);
}
QPushButton#playBig:pressed {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 $play_press_g1, stop:1 $play_press_g2);
}
QPushButton#chipBtn {
    background-color: transparent; border: 1px solid $chip_border; border-radius: 14px;
    color: $chip_text; font-size: 11px; padding: 5px 8px;
}
QPushButton#chipBtn:hover { border-color: $sub1; color: #ffffff; }
QPushButton#chipBtn:checked {
    background-color: $chip_on_bg; border-color: $sub1; color: $sub1; font-weight: bold;
}

QSlider { min-height: 22px; background: transparent; }
QSlider::groove:horizontal { height: 5px; background: $groove; border-radius: 2px; }
QSlider::sub-page:horizontal {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 $sub1, stop:1 $sub2);
    border-radius: 2px;
}
QSlider::handle:horizontal {
    width: 14px; height: 14px; margin: -5px 0; border-radius: 7px;
    background: #ffffff; border: 2px solid $handle_border;
}
QSlider::handle:horizontal:hover { background: $handle_hover; }
QSlider::add-page:horizontal { background: $groove; border-radius: 2px; }

QTabWidget::pane {
    border: 1px solid $pane_border; border-radius: 10px;
    background-color: $pane_bg; top: -1px;
}
QTabBar::tab {
    background: transparent; color: $tab_text;
    padding: 7px 14px; margin-right: 4px;
    border-bottom: 2px solid transparent;
    border-top-left-radius: 8px; border-top-right-radius: 8px;
}
QTabBar::tab:selected { color: $tab_sel; border-bottom: 2px solid $tab_sel; }
QTabBar::tab:hover:!selected { color: $tab_hover; }

QTableView {
    background-color: $table_bg; alternate-background-color: $table_alt;
    gridline-color: $table_grid; color: $table_text; border: none;
    selection-background-color: $sel_bg; selection-color: #ffffff;
}
QTableView::item { padding: 2px 6px; }
QHeaderView::section {
    background-color: $header_bg; color: $header_text; font-weight: bold; font-size: 10px;
    border: none; border-bottom: 1px solid $header_border; padding: 5px 6px;
}
QTableCornerButton::section { background-color: $header_bg; border: none; }

QComboBox {
    background-color: $combo_bg; border: 1px solid $combo_border; border-radius: 8px;
    padding: 4px 26px 4px 10px; color: $combo_text;
}
QComboBox:hover { border-color: $sub1; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox::down-arrow {
    border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-top: 5px solid $sub1; margin: 0 6px;
}
QComboBox QAbstractItemView {
    background-color: $combo_bg; border: 1px solid $combo_border;
    selection-background-color: $sel_bg; selection-color: #ffffff; outline: none;
}

QProgressBar {
    background-color: $progress_bg; border: 1px solid $progress_border; border-radius: 8px;
    text-align: center; color: $table_text; min-height: 14px;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 $sub1, stop:1 $sub2);
    border-radius: 7px;
}

QStatusBar { background-color: $status_bg; color: $status_text; border-top: 1px solid $status_border; }
QStatusBar::item { border: none; }
QStatusBar QLabel { color: $status_text; }

QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: $scroll_h; border-radius: 4px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: $scroll_hov; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: $scroll_h; border-radius: 4px; min-width: 30px; }
QScrollBar::handle:horizontal:hover { background: $scroll_hov; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QToolTip { background-color: $tip_bg; color: $tip_text; border: 1px solid $tip_border; padding: 4px; }
QLineEdit, QSpinBox {
    background-color: $input_bg; border: 1px solid $input_border; border-radius: 8px;
    padding: 4px 8px; color: $text; selection-background-color: $input_sel;
}
QLineEdit:focus, QSpinBox:focus { border-color: $sub1; }
QCheckBox { color: $check_text; spacing: 8px; background: transparent; }
QCheckBox::indicator {
    width: 16px; height: 16px; border-radius: 4px;
    border: 1px solid $check_border; background-color: $check_bg;
}
QCheckBox::indicator:hover { border-color: $sub1; }
QCheckBox::indicator:checked { background-color: $check_on; border-color: $check_on; }
QMenu { background-color: $menu_bg; border: 1px solid $menu_border; padding: 4px; color: $text; }
QMenu::item { padding: 5px 18px; border-radius: 6px; }
QMenu::item:selected { background-color: $menu_sel; color: #ffffff; }
QGroupBox {
    border: 1px solid $group_border; border-radius: 10px; margin-top: 12px; color: $group_text;
}
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 4px; color: $group_title; }
QListWidget {
    background-color: $list_bg; border: 1px solid $list_border; border-radius: 10px; color: $list_text;
}

/* v0.3.7: окно «Синхронизатор текста» — лог и предпросмотр LRC */
QTextEdit, QPlainTextEdit {
    background-color: $input_bg; color: $table_text;
    border: 1px solid $input_border; border-radius: 8px;
    font-family: "Consolas", "Courier New", monospace; font-size: 9pt;
    selection-background-color: $input_sel;
}
QTextEdit:focus, QPlainTextEdit:focus { border-color: $sub1; }
QLabel#SyncDeps { color: $muted; font-size: 9pt; }
QPushButton#SyncStart { font-size: 11pt; font-weight: bold; padding: 9px 14px; }
QPushButton#SyncStart:hover { border-color: $sub1; }
QListWidget::item { padding: 5px; border-radius: 6px; }
QListWidget::item:selected { background-color: $sel_bg; color: #ffffff; }

/* --- v0.3.0: именованные элементы — цвета задаёт активная тема --- */
QLabel#BrandSub { color: $muted; font-size: 8px; font-weight: bold; letter-spacing: 3px; background: transparent; }
QLabel#VerChip { color: $play_text; background-color: $sub1; border-radius: 9px; padding: 2px 9px; font-size: 11px; font-weight: bold; }
QLabel#CoverFrame { border: 2px solid $sub1; border-radius: 16px; background-color: $bg; }
QLabel#TitleNow { color: #ffffff; font-size: 14px; font-weight: bold; background: transparent; }
QLabel#ArtistLink { color: $sub1; font-size: 12px; background: transparent; }
QLabel#AlbumLabel { color: $muted; font-size: 11px; }
QLabel#TimeCur { color: $sub1; font-weight: bold; background: transparent; }
QLabel#TimeTot { color: $muted; background: transparent; }
QLabel#VolIcon { color: $sub1; background: transparent; }
QLabel#VisTitle { color: $muted; font-size: 10px; letter-spacing: 2px; font-weight: bold; background: transparent; }
QLabel#SettingsSection { color: $sub1; font-weight: bold; margin-top: 6px; }

QLabel#MiniCover { border: 2px solid $sub1; border-radius: 12px; background-color: $bg; }
QLabel#MiniTitle { color: $sub1; font-weight: bold; background: transparent; }
QLabel#MiniArtist { color: $muted; font-size: 11px; text-decoration: underline; background: transparent; }
QLabel#MiniTime { color: $sub1; font-size: 11px; font-weight: bold; background: transparent; }
QLabel#MiniLyric {
    color: $sub1; font-size: 11px; font-weight: bold;
    background-color: $mini_lyric_bg; border-radius: 4px; padding: 3px;
}

/* v0.3.2 (патч пользователя): альбомные карточки и вкладка
   «Текст песни» — цвета из активной темы вместо жёстко зашитых */
QFrame#AlbumCard {
    background-color: $table_bg; border: 1px solid $pane_border;
    border-radius: 8px; padding: 8px; margin-bottom: 8px;
}
QLabel#AlbumCover {
    border: 1px solid $pane_border; background-color: $bg;
}
QLabel#AlbumTitle { color: #ffffff; font-size: 13px; font-weight: bold; }
QLabel#AlbumInfo  { color: $muted; font-size: 11px; }
QListWidget#AlbumTrackList {
    background-color: $table_alt; color: $text;
    border: 1px solid $pane_border; border-radius: 4px;
}
QListWidget#AlbumTrackList::item { padding: 4px; }
QListWidget#AlbumTrackList::item:hover { background-color: $btn_hover_bg; }
QListWidget#AlbumTrackList::item:selected { background-color: $sel_bg; color: #ffffff; }

QLabel#ArtistStats { color: $muted; }
QScrollArea#AlbumsScroll, QScrollArea#AlbumsScroll > QWidget > QWidget {
    background: transparent; border: none;
}
QWidget#AlbumsContainer { background: transparent; }
""")

MINI_QSS_TEMPLATE = string.Template("""
QWidget#MiniRoot {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 $mini_g1, stop:1 $mini_g2);
    border: 1px solid $sub1;
    border-radius: 12px;
}
QLabel { color: $text; background: transparent; }
QPushButton {
    background-color: $btn_bg; color: $btn_text;
    border: 1px solid $btn_border; border-radius: 6px; padding: 3px 8px;
}
QPushButton:hover { border-color: $sub1; color: #ffffff; }
QPushButton:checked { background-color: $chip_on_bg; border-color: $sub1; color: $sub1; }
QListWidget {
    background-color: $table_bg; color: $text;
    border: 1px solid $pane_border; border-radius: 8px;
}
QListWidget::item { padding: 5px; border-radius: 6px; }
QListWidget::item:selected { background-color: $sel_bg; color: #ffffff; font-weight: bold; }
QSlider { min-height: 18px; background: transparent; }
QSlider::groove:horizontal { height: 4px; background: $groove; border-radius: 2px; }
QSlider::sub-page:horizontal {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 $sub1, stop:1 $sub2);
    border-radius: 2px;
}
QSlider::handle:horizontal {
    width: 12px; height: 12px; margin: -4px 0;
    border-radius: 6px; background: #ffffff; border: 2px solid $handle_border;
}
""")


def build_qss() -> str:
    """QSS всего приложения для активной темы."""
    return APP_QSS_TEMPLATE.safe_substitute(T)


def build_mini_qss() -> str:
    """QSS миниплеера для активной темы."""
    return MINI_QSS_TEMPLATE.safe_substitute(T)


def apply_theme(key: str) -> None:
    """Включает тему по ключу и применяет QSS ко всему приложению.

    Перекраску рисованных вручную элементов (визуализатор, караоке,
    свечения) выполняет MediaPlayerWindow._refresh_theme_dependents().
    """
    global T
    T = THEMES.get(key, THEMES["aurora"])
    app = QApplication.instance()
    if app is not None:
        app.setStyleSheet(build_qss())


class AuroraCentral(QWidget):
    """v0.3.0: центральный виджет с фоном активной темы.

    Базовый градиент и «аврора»-свечения пререндерятся в кэш-пикмап:
    перерисовка только при изменении размера/dpi/темы — постоянной
    нагрузки на CPU нет (принцип производительности v0.2.0 сохранён).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bg_key = None
        self._bg_pix: Optional[QPixmap] = None

    def invalidate(self):
        self._bg_key = None
        self.update()

    def _cur_key(self):
        return (T.get("label", ""), self.width(), self.height(),
                self.devicePixelRatioF())

    def _rebuild_bg(self):
        dpr = max(1.0, self.devicePixelRatioF())
        w = max(1, self.width())
        h = max(1, self.height())
        pm = QPixmap(int(w * dpr), int(h * dpr))
        pm.setDevicePixelRatio(dpr)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        grad = QLinearGradient(0.0, 0.0, w * 0.35, h)
        grad.setColorAt(0.0, QColor(T["central1"]))
        grad.setColorAt(0.55, QColor(T["central2"]))
        grad.setColorAt(1.0, QColor(T["central3"]))
        p.fillRect(self.rect(), QBrush(grad))
        if T.get("aurora") == "1":
            # Аврора-свечения: мягкие радиальные пятна в углах (макет:
            # циан у верхнего левого, индиго у нижнего правого)
            for cx_f, cy_f, rad_f, hexcol, alpha in (
                    (0.10, -0.10, 0.80, T["glow1"], 30),
                    (1.08, 1.10, 0.75, T["glow2"], 38)):
                col = QColor(hexcol)
                col.setAlpha(alpha)
                rg = QRadialGradient(QPointF(w * cx_f, h * cy_f), w * rad_f)
                rg.setColorAt(0.0, col)
                col0 = QColor(col)
                col0.setAlpha(0)
                rg.setColorAt(1.0, col0)
                p.fillRect(self.rect(), QBrush(rg))
        p.end()
        self._bg_pix = pm
        self._bg_key = self._cur_key()

    def paintEvent(self, _event):
        if self._bg_key != self._cur_key() or self._bg_pix is None:
            self._rebuild_bg()
        if self._bg_pix is not None:
            p = QPainter(self)
            p.drawPixmap(0, 0, self._bg_pix)
            p.end()


class MediaPlayerWindow(QMainWindow):
    # v0.2.1: адаптивная левая колонка — пределы сжатия блоков.
    # v0.2.2: в оконном режиме караоке и визуализатор сразу компактные
    # (как при развёрнутом окне на небольшом экране), пределы сжатия глубже,
    # окно никогда не заходит за панель задач (clamp к availableGeometry).
    MIN_WINDOW_W = 940
    IDEAL_COVER = 192
    MIN_COVER = 104
    IDEAL_KARAOKE_H = 112
    MIN_KARAOKE_H = 40
    IDEAL_VIZ_H = 60      # v0.3.4: новая высота визуализатора
    MIN_VIZ_H = 30
    WIN_KARAOKE_H = 56    # компактный профиль оконного режима
    WIN_VIZ_H = 40
    MIN_WINDOW_H_FLOOR = 430  # абсолютный пол (экстренно маленькие экраны)
    theme_key: str = "aurora"   # v0.3.0: ключ активной темы оформления

    def __init__(self, initial_args: Optional[List[str]] = None):
        super().__init__()
        # v0.2.2: флаги clamp задаются ДО первого resize — resizeEvent,
        # который он триггерит, обращается к ним синхронно
        self._clamp_busy: bool = False
        self._screen_conn_done: bool = False
        self.setWindowTitle(APP_FULL_TITLE)
        self.setWindowIcon(create_hox_icon())
        self._set_initial_size()
        self.setAcceptDrops(True)

        self.settings = QSettings("HoxMedia", "HoxMusicPlayer")

        # v0.2.1: адаптивная левая колонка (см. _relayout_left_column)
        self._cover_base: Optional[QPixmap] = None
        self._cover_size: int = 192
        self._cover_halo_cache: Dict[int, QPixmap] = {}
        self._min_window_h: int = 0
        self._min_window_w: int = 0
        # v0.3.4: ручной сдвиг синхронизации текста (мс); сбрасывается при смене трека
        self.lyric_offset_ms = 0
        # v0.2.2: окно не заходит за панель задач (clamp к availableGeometry)
        # флаги _clamp_busy/_screen_conn_done инициализированы выше —
        # до _set_initial_size(), чтобы resizeEvent их уже видел

        # v0.3.3: debounce для перестройки левой колонки (40 мс) и вписывания
        # окна в рабочую область (60 мс). Qt при maximize/resize шлёт несколько
        # событий подряд — по таймеру они схлопываются в одну перестройку.
        self._relayout_timer = QTimer(self)
        self._relayout_timer.setSingleShot(True)
        self._relayout_timer.setInterval(40)
        self._relayout_timer.timeout.connect(self._do_relayout)
        self._clamp_timer = QTimer(self)
        self._clamp_timer.setSingleShot(True)
        self._clamp_timer.setInterval(60)
        self._clamp_timer.timeout.connect(self._do_clamp)
        # v0.3.3: последняя обработанная высота под левую колонку —
        # чтобы не перестраивать её заново без реального изменения высоты
        self._last_avail_h = 0

        self.known_paths: Set[Path] = set()
        self.current_index: int = -1
        self.repeat_one: bool = False
        self.slider_pressed: bool = False
        # v0.3.5: последняя опрошенная позиция для ровного хода текста
        self._last_sync_pos: int = -1
        self.scan_worker: Optional[ScanWorker] = None
        self.env_worker: Optional[EnvelopeWorker] = None
        self.old_env_workers: List[EnvelopeWorker] = []
        self.folder_check_worker: Optional[FolderCheckWorker] = None

        # Настройки производительности и памяти между запусками
        self.visualizer_enabled: bool = True
        self.mini_visualizer_enabled: bool = True
        self.restore_last_track: bool = True
        self.remember_mini_mode: bool = True

        # Восстановление последнего трека и позиции
        self._restore_done: bool = False
        self._restore_path: Optional[Path] = None
        self._restore_ms: int = 0
        self._restore_playing: bool = False
        self._pending_seek_ms: int = -1

        self.watched_folder: str = ""
        self.auto_watch_enabled: bool = True
        self.watch_interval_sec: int = 15
        self.minimize_to_mini: bool = False
        # «Поверх всех окон» для миниплеера (v0.1.5): по умолчанию выключено
        self.mini_on_top: bool = False
        # Ручной выбор аудиовыхода (v0.1.9): пустой ключ = «Авто (системный)»
        self.audio_out_key: str = ""
        self.audio_out_name: str = ""
        self.show_tips: bool = True
        self.load_folder_on_startup: bool = True
        self.load_settings()

        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)
        # Авто-переключение аудиовыхода (наушники/Bluetooth/динамики) на лету:
        # следим за появлением/исчезновением устройств и сразу переставляем вывод
        self.media_devices_watcher = QMediaDevices(self)
        self.media_devices_watcher.audioOutputsChanged.connect(
            self._on_audio_outputs_changed)

        self.mini_player = MiniPlayerWindow(self)
        self.mini_player.setWindowIcon(self.windowIcon())
        # Применяем настройку «поверх всех окон» (mini_on_top, v0.1.5)
        self.apply_mini_on_top()

        self.fs_watcher = QFileSystemWatcher(self)
        self.watch_timer = QTimer(self)
        self.watch_timer.timeout.connect(self.check_folder_for_new_tracks)

        self._init_ui()
        self._connect_signals()
        self._set_default_cover()
        self._apply_saved_ui_state()
        self.apply_visualizer_settings()
        self.setup_folder_watcher()

        if initial_args:
            arg_paths = [Path(a) for a in initial_args if Path(a).exists()]
            arg_files = [p for p in arg_paths if p.is_file()]
            arg_dirs = [p for p in arg_paths if p.is_dir()]
            if arg_files:
                # Открыты конкретные файлы — играем их, восстановление последнего
                # трека не нужно (пользователь явно выбрал, что слушать)
                self._restore_done = True
                self.start_background_scan(arg_paths, clear_existing=False, auto_play_first=True)
            elif arg_dirs:
                # Открыта папка (ярлык/«открыть с помощью») — загружаем её как
                # библиотеку и восстанавливаем последний трек как при обычном
                # запуске; раньше здесь всегда играл первый трек папки, из-за
                # чего при каждом запуске звучала одна и та же песня
                self.start_background_scan(arg_dirs, clear_existing=True, auto_play_first=False)
        elif self.load_folder_on_startup and self.watched_folder:
            folder_p = Path(self.watched_folder)
            if folder_p.is_dir():
                self.start_background_scan([folder_p], clear_existing=True, auto_play_first=False)

        # Подготовка восстановления трека/позиции с прошлого запуска
        last_path = self.settings.value("last_track_path", "", type=str) or ""
        if self.restore_last_track and last_path:
            self._restore_path = Path(last_path)
            self._restore_ms = self.settings.value("last_position_ms", 0, type=int)
            self._restore_playing = self.settings.value("last_was_playing", False, type=bool)
        else:
            self._restore_done = True

    def _on_audio_outputs_changed(self):
        """Смена списка аудиоустройств: в режиме «Авто» перескакиваем на новый
        выход по умолчанию (наушники, Bluetooth-колонка, HDMI и т.д.), при
        ручном выборе — возвращаем выбранное устройство (например, после
        переподключения Bluetooth-наушников)."""
        self.apply_audio_output_pref()

    # --- Ручной выбор аудиовыхода (v0.1.9) -------------------------------
    def apply_audio_output_pref(self):
        """Применяет сохранённый выбор аудиовыхода.

        Ручной выбор найден в списке устройств — выводим звук на него;
        «Авто» или устройство исчезло — системный выход по умолчанию
        (выбор сохраняется: при возвращении устройства он применится снова).
        """
        if not hasattr(self, "audio_output"):
            return
        try:
            if self.audio_out_key:
                for dev in QMediaDevices.audioOutputs():
                    if _audio_dev_key(dev) == self.audio_out_key:
                        if self.audio_output.device() != dev:
                            self.audio_output.setDevice(dev)
                        return
            new_dev = QMediaDevices.defaultAudioOutput()
            if not new_dev.isNull() and self.audio_output.device() != new_dev:
                self.audio_output.setDevice(new_dev)
        except Exception:
            pass

    def save_audio_out_pref(self):
        self.settings.setValue("audio_out_key", self.audio_out_key)
        self.settings.setValue("audio_out_name", self.audio_out_name)

    def select_audio_output(self, key: str, name: str = ""):
        """Переключает аудиовыход вручную (пустой key — режим «Авто»)."""
        self.audio_out_key = key
        self.audio_out_name = name
        self.save_audio_out_pref()
        self.apply_audio_output_pref()
        label = name or "Авто (системный)"
        self.btn_audio_out.setToolTip(f"Аудиовыход: {label}")
        self.status_bar.showMessage(f"Аудиовыход: {label}", 4000)

    def show_audio_output_menu(self):
        """Меню выбора аудиовыхода: «Авто (системный)» + все устройства."""
        if not _AUDIO_OUTPUT_SELECT:
            return
        menu = QMenu(self)
        act_auto = menu.addAction("Авто (системный выход)")
        act_auto.setCheckable(True)
        act_auto.setChecked(not self.audio_out_key)
        act_auto.triggered.connect(lambda: self.select_audio_output(""))
        try:
            devs = list(QMediaDevices.audioOutputs())
        except Exception:
            devs = []
        if devs:
            menu.addSeparator()
            for dev in devs:
                key = _audio_dev_key(dev)
                label = _audio_dev_label(dev)
                act = menu.addAction(label)
                act.setCheckable(True)
                act.setChecked(key == self.audio_out_key)
                act.triggered.connect(
                    lambda _checked=False, k=key, nm=label: self.select_audio_output(k, nm))
        menu.exec(self.btn_audio_out.mapToGlobal(
            QPoint(0, self.btn_audio_out.height())))

    @property
    def tracks(self) -> List[TrackInfo]:
        return self.playlist_model.tracks

    def _init_ui(self):
        central = AuroraCentral()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)

        # --- Левая панель: бренд + версия, обложка, управление, громкость и визуализатор ---
        left_layout = QVBoxLayout()
        left_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        left_layout.setSpacing(6)

        brand_row = QHBoxLayout()
        lbl_brand = QLabel("HOX")
        lbl_brand.setFont(QFont("Segoe UI", 17, QFont.Weight.Black))
        lbl_brand.setStyleSheet(
            "color: #ffffff; font-size: 17px; font-weight: 900;"
            "letter-spacing: 5px; background: transparent;")
        lbl_sub = QLabel("MUSIC PLAYER")
        lbl_sub.setObjectName("BrandSub")
        self.ver_chip = QLabel(f"v{APP_VERSION}")
        self.ver_chip.setObjectName("VerChip")
        brand_row.addWidget(lbl_brand)
        brand_row.addSpacing(4)
        brand_row.addWidget(lbl_sub, 1, Qt.AlignmentFlag.AlignVCenter)
        brand_row.addWidget(self.ver_chip, 0, Qt.AlignmentFlag.AlignVCenter)
        left_layout.addLayout(brand_row)

        self.cover_label = QLabel()
        self.cover_label.setFixedSize(192, 192)
        self.cover_label.setObjectName("CoverFrame")
        self.cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # v0.2.0: свечение обложки — статичная предрендеренная подложка вместо
        # живого QGraphicsDropShadowEffect (blur 44): эффект пересчитывался при
        # каждой перерисовке/resize и провоцировал фризы на слабых ПК.
        cover_halo = QLabel()
        cover_halo.setPixmap(make_glow_halo(192, 192, 16, _accent_qc(100), 44))
        cover_halo.setFixedSize(cover_halo.pixmap().size())
        cover_halo.setStyleSheet("background: transparent;")
        self._cover_halo = cover_halo  # v0.2.1: ссылка для адаптивного масштаба
        cover_wrap = QWidget()
        cover_wrap.setStyleSheet("background: transparent;")
        cover_grid = QGridLayout(cover_wrap)
        cover_grid.setContentsMargins(0, 0, 0, 0)
        cover_grid.addWidget(cover_halo, 0, 0, Qt.AlignmentFlag.AlignCenter)
        cover_grid.addWidget(self.cover_label, 0, 0, Qt.AlignmentFlag.AlignCenter)
        self.cover_label.raise_()
        left_layout.addWidget(cover_wrap, alignment=Qt.AlignmentFlag.AlignCenter)

        self.title_label = QLabel("Трек не выбран")
        self.title_label.setObjectName("TitleNow")
        self.title_label.setWordWrap(True)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left_layout.addWidget(self.title_label)

        self.artist_label = ClickableLabel("")
        self.artist_label.setObjectName("ArtistLink")
        self.artist_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.artist_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.artist_label.clicked.connect(self.open_current_artist_page)
        left_layout.addWidget(self.artist_label)

        self.album_label = QLabel("")
        self.album_label.setObjectName("AlbumLabel")
        self.album_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left_layout.addWidget(self.album_label)

        # Караоке-блок текста песни (v0.1.8): под именем исполнителя,
        # активная строка подсвечена, синхронизация по LRC-таймкодам
        self.karaoke = KaraokeLyricsWidget(self)
        left_layout.addWidget(self.karaoke)

        self.position_slider = QSlider(Qt.Orientation.Horizontal)
        self.position_slider.setRange(0, 0)
        left_layout.addWidget(self.position_slider)

        time_layout = QHBoxLayout()
        self.time_current = QLabel("00:00")
        self.time_current.setObjectName("TimeCur")
        self.time_total = QLabel("00:00")
        self.time_total.setObjectName("TimeTot")
        time_layout.addWidget(self.time_current)
        time_layout.addStretch()
        time_layout.addWidget(self.time_total)
        left_layout.addLayout(time_layout)

        controls_layout = QHBoxLayout()
        # v0.3.3: глифы кнопок — векторные иконки (см. _apply_button_icons),
        # а не текстовые эмодзи; у чипов остаётся только подпись
        self.btn_prev = QPushButton()
        self.btn_play = QPushButton()
        self.btn_stop = QPushButton(" Стоп")
        self.btn_next = QPushButton()
        self.btn_repeat = QPushButton(" Повтор: ВЫКЛ")
        self.btn_repeat.setCheckable(True)
        self.btn_mini = QPushButton(" В миниплеер")

        self.btn_prev.setObjectName("roundBtn")
        self.btn_next.setObjectName("roundBtn")
        self.btn_play.setObjectName("playBig")
        self.btn_stop.setObjectName("chipBtn")
        self.btn_repeat.setObjectName("chipBtn")
        self.btn_mini.setObjectName("chipBtn")
        self.btn_prev.setFixedSize(46, 46)
        self.btn_next.setFixedSize(46, 46)
        self.btn_play.setFixedSize(58, 58)
        for chip in (self.btn_stop, self.btn_repeat, self.btn_mini):
            chip.setFixedHeight(28)
        self._apply_button_icons()

        # v0.2.0: свечение кнопки Play — статичная предрендеренная подложка
        # вместо QGraphicsDropShadowEffect (blur 36), который пересчитывался
        # при каждом hover/нажатии и дёргал интерфейс.
        play_halo = QLabel()
        play_halo.setPixmap(make_circle_glow(58, _accent_qc(150), 36))
        self.play_halo = play_halo  # v0.3.0: ссылка для перекраски при смене темы
        play_halo.setFixedSize(play_halo.pixmap().size())
        play_halo.setStyleSheet("background: transparent;")
        play_wrap = QWidget()
        # v0.3.1: НЕ задаём play_wrap inline "background: transparent" —
        # оно каскадом глушит QSS-градиент кнопки-потомка btn_play (сквозь
        # кнопку просвечивало гало во всех темах). Обычный QWidget и так
        # не рисует фон, а кнопка снова ярко-акцентная, как в макете.
        play_grid = QGridLayout(play_wrap)
        play_grid.setContentsMargins(0, 0, 0, 0)
        play_grid.addWidget(play_halo, 0, 0, Qt.AlignmentFlag.AlignCenter)
        play_grid.addWidget(self.btn_play, 0, 0, Qt.AlignmentFlag.AlignCenter)
        self.btn_play.raise_()

        controls_layout.addStretch(1)
        controls_layout.addWidget(self.btn_prev)
        controls_layout.addSpacing(10)
        controls_layout.addWidget(play_wrap)
        controls_layout.addSpacing(10)
        controls_layout.addWidget(self.btn_next)
        controls_layout.addStretch(1)
        left_layout.addLayout(controls_layout)

        chips_row = QHBoxLayout()
        chips_row.setSpacing(8)
        chips_row.addWidget(self.btn_repeat, 1)
        chips_row.addWidget(self.btn_stop)
        chips_row.addWidget(self.btn_mini, 1)
        left_layout.addLayout(chips_row)

        # Ползунок громкости + кнопка выбора аудиовыхода (v0.1.9)
        vol_layout = QHBoxLayout()
        lbl_vol = QLabel()
        lbl_vol.setObjectName("VolIcon")
        # v0.3.3: иконка громкости вместо эмодзи 🔊; ссылка — для перекраски
        # при смене темы (см. _apply_top_icons)
        self.lbl_vol = lbl_vol
        lbl_vol.setPixmap(make_icon("volume", QColor(T["sub1"]), 32).pixmap(16, 16))
        lbl_vol.setFixedSize(18, 18)
        vol_layout.addWidget(lbl_vol)
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(70)
        self.vol_pct_label = QLabel("70%")
        self.vol_pct_label.setFixedWidth(36)
        vol_layout.addWidget(self.volume_slider, 1)
        vol_layout.addWidget(self.vol_pct_label)
        self.btn_audio_out = QPushButton()
        self.btn_audio_out.setObjectName("chipBtn")
        self.btn_audio_out.setFixedSize(28, 28)
        self.btn_audio_out.setIcon(make_icon("headphones", QColor(T["sub1"]), 32))
        self.btn_audio_out.setIconSize(QSize(16, 16))
        self.btn_audio_out.setToolTip("Аудиовыход: Авто (системный)")
        self.btn_audio_out.setVisible(_AUDIO_OUTPUT_SELECT)
        self.btn_audio_out.clicked.connect(self.show_audio_output_menu)
        vol_layout.addWidget(self.btn_audio_out)
        left_layout.addLayout(vol_layout)

        # Визуализатор в такт музыки под уровнем громкости (отключается в настройках)
        self.vis_title = QLabel("ВИЗУАЛИЗАТОР СПЕКТРА")
        self.vis_title.setObjectName("VisTitle")
        self.vis_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left_layout.addWidget(self.vis_title)

        self.visualizer = AudioVisualizerWidget(self)
        left_layout.addWidget(self.visualizer)

        left_layout.addStretch()
        left_card = QFrame()
        left_card.setObjectName("LeftCard")
        left_card.setLayout(left_layout)
        self.left_card = left_card  # v0.2.1: ссылка для замеров высоты
        main_layout.addWidget(left_card, 1)

        # --- Правая панель: управление библиотекой и 3 вкладки ---
        right_layout = QVBoxLayout()

        top_bar = QHBoxLayout()
        self.btn_open_files = QPushButton(" Добавить файлы")
        self.btn_open_folder = QPushButton(" Открыть папку")
        self.btn_rescan = QPushButton(" Проверить новые")
        self.btn_tools = QPushButton(" Инструменты")
        self.btn_settings = QPushButton(" Настройки")
        # v0.3.3: векторные иконки у кнопок верхней панели (цвет темы;
        # при смене темы вызовется повторно из _refresh_theme_dependents)
        self._apply_top_icons()

        self.sort_combo = QComboBox()
        self.sort_combo.addItems([
            "По номеру в папке (возр.)",
            "По номеру в папке (убыв.)",
            "По имени файла (А-Я)",
            "По имени файла (Я-А)",
            "По названию (А-Я)",
            "По названию (Я-А)",
            "По дате (сначала новые)",
            "По дате (сначала старые)",
        ])
        # Компактный размер комбобокса: текущий текст эллипсируется,
        # выпадающий список показывает полные строки — минимальная ширина
        # окна не распирается длинными подписями (v0.1.8)
        self.sort_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.sort_combo.setMinimumContentsLength(16)

        top_bar.addWidget(self.btn_open_files)
        top_bar.addWidget(self.btn_open_folder)
        top_bar.addWidget(self.btn_rescan)
        top_bar.addWidget(QLabel("Сортировка:"))
        top_bar.addWidget(self.sort_combo, 1)
        top_bar.addWidget(self.btn_tools)
        top_bar.addWidget(self.btn_settings)
        right_layout.addLayout(top_bar)

        self.tabs = QTabWidget()

        # Вкладка 1: Все треки (Плейлист)
        self.playlist_model = PlaylistTableModel(self)
        self.table = QTableView()
        self.table.setModel(self.playlist_model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(26)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Interactive)

        self.table.setColumnWidth(0, 82)
        self.table.setColumnWidth(2, 135)
        self.table.setColumnWidth(3, 135)
        self.table.setColumnWidth(5, 125)
        self.table.setColumnWidth(6, 70)

        self.tabs.addTab(self.table, "Все треки (Плейлист)")

        # Вкладка 2: Исполнители и альбомы
        self.artists_view = ArtistsAlbumsView(self)
        self.tabs.addTab(self.artists_view, "Исполнители и альбомы")

        # v0.3.6: вкладка «Текст песни» убрана — текст песни живёт в
        # караоке-блоке на панели управления (KaraokeLyricsWidget)

        right_layout.addWidget(self.tabs, 1)

        # Прогресс-бар сканирования
        self.progress_widget = QWidget()
        prog_layout = QHBoxLayout(self.progress_widget)
        prog_layout.setContentsMargins(0, 4, 0, 4)
        self.progress_label = QLabel("Загрузка треков...")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.btn_cancel_scan = QPushButton(" Отмена")
        self.btn_cancel_scan.setIcon(make_icon("close", QColor(T["sub1"]), 24))
        self.btn_cancel_scan.setIconSize(QSize(12, 12))
        prog_layout.addWidget(self.progress_label)
        prog_layout.addWidget(self.progress_bar, 1)
        prog_layout.addWidget(self.btn_cancel_scan)
        self.progress_widget.setVisible(False)
        right_layout.addWidget(self.progress_widget)

        main_layout.addLayout(right_layout, 2)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage(f"{APP_FULL_TITLE} готов к работе.")

    # ------------------------------------------------------------------
    # v0.3.3: векторные иконки кнопок вместо эмодзи
    # ------------------------------------------------------------------
    def _apply_button_icons(self):
        """Иконки кнопок управления в цвете активной темы.

        Вызывается при создании окна и при каждой смене темы: иконки
        рисуются QPainter, поэтому «перекраска» = перерисовка с новым
        QColor. Глиф Play берётся из play_text (тёмный на ярко-акцентном
        градиенте кнопки playBig), остальные — sub1.
        """
        accent = QColor(T["sub1"])
        play_col = QColor(T["play_text"])
        playing = (self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
                   if hasattr(self, "player") else False)
        self.btn_prev.setIcon(make_icon("prev", accent, 48))
        self.btn_prev.setIconSize(QSize(22, 22))
        self.btn_next.setIcon(make_icon("next", accent, 48))
        self.btn_next.setIconSize(QSize(22, 22))
        self.btn_play.setIcon(make_icon("pause" if playing else "play", play_col, 64))
        self.btn_play.setIconSize(QSize(28, 28))
        self.btn_stop.setIcon(make_icon("stop", accent, 32))
        self.btn_stop.setIconSize(QSize(13, 13))
        self.btn_mini.setIcon(make_icon("minimize_taskbar", accent, 32))
        self.btn_mini.setIconSize(QSize(13, 13))
        self._update_repeat_icon()

    def _update_repeat_icon(self):
        """Иконка повтора: обычный цикл или «повтор трека» (с единицей)."""
        name = "repeat_one" if self.repeat_one else "repeat"
        self.btn_repeat.setIcon(make_icon(name, QColor(T["sub1"]), 32))
        self.btn_repeat.setIconSize(QSize(13, 13))

    def _apply_top_icons(self):
        """Иконки верхней панели и служебных кнопок в цвете темы."""
        accent = QColor(T["sub1"])
        if hasattr(self, "btn_open_files"):
            self.btn_open_files.setIcon(make_icon("file", accent, 28))
            self.btn_open_files.setIconSize(QSize(14, 14))
        if hasattr(self, "btn_open_folder"):
            self.btn_open_folder.setIcon(make_icon("folder", accent, 28))
            self.btn_open_folder.setIconSize(QSize(14, 14))
        if hasattr(self, "btn_rescan"):
            self.btn_rescan.setIcon(make_icon("refresh", accent, 28))
            self.btn_rescan.setIconSize(QSize(14, 14))
        if hasattr(self, "btn_settings"):
            self.btn_settings.setIcon(make_icon("settings", accent, 28))
            self.btn_settings.setIconSize(QSize(14, 14))
        if hasattr(self, "btn_tools"):
            self.btn_tools.setIcon(make_icon("sync", accent, 28))
            self.btn_tools.setIconSize(QSize(14, 14))
        if hasattr(self, "btn_audio_out"):
            self.btn_audio_out.setIcon(make_icon("headphones", accent, 32))
            self.btn_audio_out.setIconSize(QSize(16, 16))
        if hasattr(self, "btn_cancel_scan"):
            self.btn_cancel_scan.setIcon(make_icon("close", accent, 24))
            self.btn_cancel_scan.setIconSize(QSize(12, 12))
        if hasattr(self, "lbl_vol"):
            self.lbl_vol.setPixmap(make_icon("volume", accent, 32).pixmap(16, 16))

    # ------------------------------------------------------------------
    # v0.2.1: адаптивная левая колонка — в оконном режиме всё влезает
    # ------------------------------------------------------------------

    def _apply_cover_pixmap(self):
        """Перерисовать pixmap обложки под текущий размер метки (v0.2.1)."""
        if self._cover_base is None or self._cover_size <= 0:
            return
        inner = max(48, self._cover_size - 4)  # минус рамка 2px с каждой стороны
        self.cover_label.setPixmap(rounded_pixmap(
            self._cover_base.scaled(
                inner, inner,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation), 12))

    def _set_initial_size(self):
        """v0.2.2: стартовый размер учитывает рабочую область экрана —
        на ноутбуках 1366x768 (свободно ~728 px по вертикали) и экранах
        с масштабом 125% окно сразу открывается целиком видимым,
        без ухода низа за панель задач."""
        w, h = 1200, 780
        scr = QGuiApplication.primaryScreen()
        if scr is not None:
            avail = scr.availableGeometry()
            w = max(640, min(w, avail.width() - 24))
            h = max(self.MIN_WINDOW_H_FLOOR, min(h, avail.height() - 24))
        self.resize(w, h)

    def _set_cover_size(self, size: int):
        """Адаптивный размер обложки (v0.2.1): метка, гало и pixmap меняются вместе."""
        size = max(self.MIN_COVER, min(self.IDEAL_COVER, int(size)))
        if size == self._cover_size:
            return
        self._cover_size = size
        self.cover_label.setFixedSize(size, size)
        pix = self._cover_halo_cache.get(size)
        if pix is None:
            pix = make_glow_halo(size, size, 16, _accent_qc(100),
                                 44 if size >= 176 else 32)
            if len(self._cover_halo_cache) >= 4:
                self._cover_halo_cache.clear()
            self._cover_halo_cache[size] = pix
        self._cover_halo.setPixmap(pix)
        self._cover_halo.setFixedSize(pix.size())
        self._apply_cover_pixmap()

    def _relayout_left_column(self):
        """Вписать левую колонку в высоту окна (v0.2.1, расширено в v0.2.2).

        Развёрнутое окно — идеальные пропорции v0.2.0: караоке 112,
        визуализатор 78, обложка 192. Оконный режим — компактный профиль:
        караоке 56 и визуализатор 36 (текст песни и спектр сжаты, как на
        развёрнутом окне небольшого экрана). Если компактный стек не
        влезает — последовательное сжатие до пределов 40/24/104 с
        уплотнением интервалов до 2 px; при избытке высоты размеры
        возвращаются к профильным.
        """
        card = getattr(self, "left_card", None)
        if card is None or self.centralWidget() is None:
            return
        central_layout = self.centralWidget().layout()
        if central_layout is None:
            return
        cm = central_layout.contentsMargins()
        avail = self.centralWidget().height() - cm.top() - cm.bottom()
        if avail <= 120:
            return
        # v0.3.3: высота не изменилась (±2 px) — перестраивать нечего
        if abs(avail - self._last_avail_h) <= 2 and self._cover_size > 0:
            return
        self._last_avail_h = avail
        lay = card.layout()
        maximized = self.isMaximized()
        k_t = self.IDEAL_KARAOKE_H if maximized else self.WIN_KARAOKE_H
        v_t = self.IDEAL_VIZ_H if maximized else self.WIN_VIZ_H
        sp_t = 6 if maximized else 4
        if self.karaoke.height() != k_t:
            self.karaoke.setFixedHeight(k_t)
        if self.visualizer.height() != v_t:
            self.visualizer.setFixedHeight(v_t)
        if lay.spacing() != sp_t:
            lay.setSpacing(sp_t)
        self._set_cover_size(self.IDEAL_COVER)
        lay.activate()
        deficit = card.minimumSizeHint().height() - avail
        k, v, c = k_t, v_t, self.IDEAL_COVER
        while deficit > 0 and (k > self.MIN_KARAOKE_H or v > self.MIN_VIZ_H
                               or c > self.MIN_COVER or lay.spacing() > 2):
            if k > self.MIN_KARAOKE_H:
                k = max(self.MIN_KARAOKE_H, k - 12)
                self.karaoke.setFixedHeight(k)
            if deficit > 0 and v > self.MIN_VIZ_H:
                v = max(self.MIN_VIZ_H, v - 8)
                self.visualizer.setFixedHeight(v)
            if deficit > 0 and c > self.MIN_COVER:
                c = max(self.MIN_COVER, c - 16)
                self._set_cover_size(c)
            if deficit > 0 and lay.spacing() > 2:
                lay.setSpacing(2)
            lay.activate()
            deficit = card.minimumSizeHint().height() - avail
        self._update_window_minimum_size()

    def _update_window_minimum_size(self):
        """Полностью сжатый стек левой карты задаёт минимум окна (v0.2.1):
        меньше этого размера окно не даст сделать — обрезаться нечему.
        v0.2.2: минимум дополнительно ограничен рабочей областью экрана —
        иначе на маленьких экранах (1366x768 с панелью задач, масштаб 125%)
        окно нельзя ужать и низ уходит за панель задач."""
        card = self.left_card
        if self._min_window_h <= 0:
            sk = self.karaoke.height()
            sv = self.visualizer.height()
            sc = self._cover_size
            sp = card.layout().spacing()
            self.karaoke.setFixedHeight(self.MIN_KARAOKE_H)
            self.visualizer.setFixedHeight(self.MIN_VIZ_H)
            self._set_cover_size(self.MIN_COVER)
            card.layout().setSpacing(2)
            card.layout().activate()
            sb_h = max(22, self.status_bar.height())
            # +20 = запас поверх центральных отступов, чтобы у полностью
            # сжатого стека не срезались последние 2 пикселя на минимуме
            natural = card.minimumSizeHint().height() + sb_h + 20
            # v0.2.2: кап по рабочей области (за вычетом рамки окна) —
            # минимум не должен мешать окну целиком влезть над панелью задач
            cap = 10 ** 6
            wcs = self._workarea_client_size()
            if wcs is not None:
                cap = wcs[1] - 2
            self._min_window_h = max(
                self.MIN_WINDOW_H_FLOOR,
                min(860, natural, max(cap, self.MIN_WINDOW_H_FLOOR)))
            self.karaoke.setFixedHeight(sk)
            self.visualizer.setFixedHeight(sv)
            self._set_cover_size(sc)
            card.layout().setSpacing(sp)
            card.layout().activate()
        if self._min_window_w <= 0:
            self._min_window_w = max(
                self.MIN_WINDOW_W,
                self.centralWidget().minimumSizeHint().width() + 24)
        cur = self.minimumSize()
        if (cur.width() != self._min_window_w
                or abs(cur.height() - self._min_window_h) > 1):
            self.setMinimumSize(self._min_window_w, self._min_window_h)

    def _workarea_client_size(self):
        """v0.2.2: доступный размер КЛИЕНТСКОЙ области окна в рабочей зоне
        экрана (за вычетом рамки/заголовка). None — экран неизвестен."""
        scr = self.screen()
        if scr is None:
            return None
        avail = scr.availableGeometry()
        wh = self.windowHandle()
        fm = wh.frameMargins() if wh is not None else None
        if fm is not None and not fm.isNull():
            w = avail.width() - fm.left() - fm.right()
            h = avail.height() - fm.top() - fm.bottom()
        else:
            w, h = avail.width(), avail.height() - 8
        return max(1, w), max(1, h)

    def _clamp_window_to_workarea(self):
        """v0.2.2: окно никогда не заходит за панель задач.

        Если окно (после запуска, перетаскивания, изменения размера или
        переноса на другой монитор) выходит за рабочую область экрана —
        рамка сужается до доступных размеров и окно подтягивается внутрь.
        Математика ведётся в КООРДИНАТАХ РАМКИ (frameGeometry + frameMargins):
        за экран не должна выходить и рамка с заголовком, иначе нижняя
        кромка всё равно скроется за панелью задач.
        Развёрнутое/свёрнутое окно не трогаем — им управляет система."""
        if (getattr(self, "_clamp_busy", False) or self.isMinimized()
                or self.isMaximized() or self.isFullScreen()):
            return
        scr = self.screen()
        if scr is None:
            return
        avail = scr.availableGeometry()
        self._clamp_busy = True
        try:
            # до 3 итераций: move() позиционирует рамку, geometry() — клиентскую
            # часть; на некоторых платформах есть офсет рамки — цикл сходится
            for _ in range(3):
                fg = self.frameGeometry()
                fw, fh = fg.width(), fg.height()
                if fw > avail.width() or fh > avail.height():
                    size = self._workarea_client_size()
                    if size is not None:
                        new_w = min(self.width(), size[0])
                        new_h = min(self.height(), size[1])
                        if new_w != self.width() or new_h != self.height():
                            self.resize(max(1, new_w), max(1, new_h))
                            fg = self.frameGeometry()
                            fw, fh = fg.width(), fg.height()
                nx = max(avail.left(), min(fg.x(), avail.right() - fw + 1))
                ny = max(avail.top(), min(fg.y(), avail.bottom() - fh + 1))
                if nx == fg.x() and ny == fg.y():
                    break
                self.move(nx, ny)
        finally:
            self._clamp_busy = False

    def _fit_window_on_show(self):
        """v0.2.2: после показа окна — вписать в рабочую область и ужать
        колонку под фактическую высоту. Минимум пересчитывается: только
        после показа известны реальные frameMargins (заголовок/тень)."""
        self._min_window_h = 0
        self._last_avail_h = 0
        self._clamp_window_to_workarea()
        self._relayout_left_column()

    def _on_screen_changed(self, _screen):
        """Окно перенесли на другой монитор (v0.2.2): пересчитать минимум
        под новую рабочую область и снова вписать окно в экран."""
        self._min_window_h = 0
        self._last_avail_h = 0
        QTimer.singleShot(0, self._fit_window_on_show)

    def moveEvent(self, event):
        super().moveEvent(event)
        # v0.2.2→v0.3.3: перетащили окно за панель задач — вернём в рабочую
        # область, но по таймеру-дебаунсу: при перетаскивании moveEvent сыплется
        # непрерывно, а перекладка clamp внутри себя двигает окно (moveEvent)
        if not (self.isMaximized() or self.isFullScreen() or self._clamp_busy):
            self._clamp_timer.start()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # v0.2.1→v0.3.3: левая колонка всегда в пределах окна — по дебаунсу
        # (40 мс), чтобы серию resizeEvent от maximize/resize ужать в одну
        # перестройку вместо дёрганья
        self._relayout_timer.start()

    def _do_relayout(self):
        """v0.3.3: отложенная перестройка левой колонки + вписывание окна."""
        if self.isMinimized():
            return
        self._relayout_left_column()
        if not (self.isMaximized() or self.isFullScreen()):
            self._do_clamp()

    def _do_clamp(self):
        """v0.3.3: отложенное вписывание окна в рабочую область."""
        if not (self.isMaximized() or self.isFullScreen() or self._clamp_busy):
            self._clamp_window_to_workarea()

    def _connect_signals(self):
        self.btn_open_files.clicked.connect(self.open_files)
        self.btn_open_folder.clicked.connect(self.open_folder)
        self.btn_rescan.clicked.connect(lambda: self.check_folder_for_new_tracks(manual=True))
        self.btn_settings.clicked.connect(self.open_settings_dialog)
        self.btn_tools.clicked.connect(self.show_tools_menu)
        self.btn_cancel_scan.clicked.connect(self.cancel_background_scan)
        self.sort_combo.currentIndexChanged.connect(self.apply_sorting)

        self.btn_play.clicked.connect(self.toggle_play)
        self.btn_stop.clicked.connect(self.stop_track)
        self.btn_prev.clicked.connect(self.prev_track)
        self.btn_next.clicked.connect(self.next_track)
        self.btn_repeat.toggled.connect(self.toggle_repeat)
        self.btn_mini.clicked.connect(self.switch_to_mini)

        self.position_slider.sliderPressed.connect(lambda: setattr(self, 'slider_pressed', True))
        self.position_slider.sliderReleased.connect(self.seek_position)
        self.volume_slider.valueChanged.connect(self.on_volume_changed)

        self.table.doubleClicked.connect(self.on_table_double_click)

        self.player.positionChanged.connect(self.on_position_changed)
        # v0.3.5: внутренний таймер синхронизации — опрашивает позицию каждые
        # 100 мс, чтобы текст песни и таймер двигались ровно даже при редких
        # positionChanged (гранулярность зависит от аудио-бэкенда)
        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(100)
        self._sync_timer.timeout.connect(self._on_sync_tick)
        self.player.durationChanged.connect(self.on_duration_changed)
        self.player.mediaStatusChanged.connect(self.on_media_status_changed)
        self.player.playbackStateChanged.connect(self.on_state_changed)

        self.fs_watcher.directoryChanged.connect(self._on_fs_directory_changed)

    # ------------------------------------------------------------------
    # v0.3.0: темы оформления — выбор в ⚙ Настройках, мгновенное применение
    # ------------------------------------------------------------------
    def set_theme(self, key: str, save: bool = True):
        """Применить тему оформления: QSS всего приложения + перекраска
        рисованных виджетов, свечений и миниплеера. save=True — записать
        выбор в QSettings (persist между запусками)."""
        if key not in THEMES:
            key = "aurora"
        self.theme_key = key
        apply_theme(key)
        self._refresh_theme_dependents()
        if save:
            self.settings.setValue("theme", key)

    def _refresh_theme_dependents(self):
        """Перекрасить то, что не перехватывается глобальным QSS:
        аврора-фон, визуализаторы, караоке, активные строки плейлиста,
        свечения обложки/Play и стили миниплеера."""
        cw = self.centralWidget()
        if isinstance(cw, AuroraCentral):
            cw.invalidate()
        for wdg in (getattr(self, "visualizer", None),
                    getattr(getattr(self, "mini_player", None),
                            "viz_widget", None)):
            if wdg is not None:
                wdg.refresh_theme()
        if getattr(self, "karaoke", None) is not None:
            self.karaoke.update()
        pm = getattr(self, "playlist_model", None)
        if pm is not None:
            pm.refresh_colors()
        if hasattr(self, "mini_player"):
            self.mini_player.setStyleSheet(build_mini_qss())
            # v0.3.3: иконки миниплеера тоже перекрашиваются с темой
            self.mini_player._update_repeat_icon()
        self._rebuild_halos()
        # v0.3.3: перерисовать векторные иконки кнопок в новых цветах
        self._apply_button_icons()
        self._apply_top_icons()
        if hasattr(self, "status_bar"):
            self.status_bar.showMessage(f"Тема оформления: {T['label']}", 4000)

    def _rebuild_halos(self):
        """v0.3.0: перекраска свечений обложки и кнопки Play под тему."""
        if getattr(self, "_cover_halo", None) is not None:
            size = self._cover_size
            pix = make_glow_halo(size, size, 16, _accent_qc(100),
                                 44 if size >= 176 else 32)
            self._cover_halo_cache = {size: pix}
            self._cover_halo.setPixmap(pix)
            self._cover_halo.setFixedSize(pix.size())
        if getattr(self, "play_halo", None) is not None:
            pix = make_circle_glow(58, _accent_qc(150), 36)
            self.play_halo.setPixmap(pix)
            self.play_halo.setFixedSize(pix.size())

    def load_settings(self):
        self.watched_folder = self.settings.value("watched_folder", "", type=str)
        self.auto_watch_enabled = self.settings.value("auto_watch_enabled", True, type=bool)
        self.watch_interval_sec = self.settings.value("watch_interval_sec", 15, type=int)
        self.minimize_to_mini = self.settings.value("minimize_to_mini", False, type=bool)
        self.mini_on_top = self.settings.value("mini_on_top", False, type=bool)
        self.load_folder_on_startup = self.settings.value("load_folder_on_startup", True, type=bool)
        self.visualizer_enabled = self.settings.value("visualizer_enabled", True, type=bool)
        self.mini_visualizer_enabled = self.settings.value("mini_visualizer_enabled", True, type=bool)
        self.restore_last_track = self.settings.value("restore_last_track", True, type=bool)
        self.remember_mini_mode = self.settings.value("remember_mini_mode", True, type=bool)
        self.show_tips = self.settings.value("show_tips", True, type=bool)
        self.audio_out_key = self.settings.value("audio_out_key", "", type=str)
        self.audio_out_name = self.settings.value("audio_out_name", "", type=str)
        # v0.3.0: тема оформления
        self.theme_key = theme_key_from_settings(self.settings)

    def _apply_saved_ui_state(self):
        vol = self.settings.value("volume", 70, type=int)
        self.volume_slider.setValue(vol)
        self.vol_pct_label.setText(f"{vol}%")
        self.audio_output.setVolume(vol / 100.0)
        # Восстановление ручного выбора аудиовыхода (v0.1.9)
        self.apply_audio_output_pref()
        label = self.audio_out_name or "Авто (системный)"
        self.btn_audio_out.setToolTip(f"Аудиовыход: {label}")

        sort_idx = self.settings.value("sort_index", 0, type=int)
        if 0 <= sort_idx < self.sort_combo.count():
            self.sort_combo.blockSignals(True)
            self.sort_combo.setCurrentIndex(sort_idx)
            self.sort_combo.blockSignals(False)

        rep = self.settings.value("repeat_one", False, type=bool)
        self.btn_repeat.setChecked(rep)
        # v0.3.4: атрибут repeat_one и иконки повтора синхронизируются при старте
        self.repeat_one = bool(rep)
        self._update_repeat_icon()
        if hasattr(self, "mini_player"):
            self.mini_player._update_repeat_icon()

    def save_settings(self):
        self.settings.setValue("watched_folder", self.watched_folder)
        self.settings.setValue("auto_watch_enabled", self.auto_watch_enabled)
        self.settings.setValue("watch_interval_sec", self.watch_interval_sec)
        self.settings.setValue("minimize_to_mini", self.minimize_to_mini)
        self.settings.setValue("mini_on_top", self.mini_on_top)
        self.settings.setValue("load_folder_on_startup", self.load_folder_on_startup)
        self.settings.setValue("volume", self.volume_slider.value())
        self.settings.setValue("sort_index", self.sort_combo.currentIndex())
        self.settings.setValue("repeat_one", self.repeat_one)
        self.settings.setValue("visualizer_enabled", self.visualizer_enabled)
        self.settings.setValue("mini_visualizer_enabled", self.mini_visualizer_enabled)
        self.settings.setValue("restore_last_track", self.restore_last_track)
        self.settings.setValue("remember_mini_mode", self.remember_mini_mode)
        self.settings.setValue("show_tips", self.show_tips)
        self.settings.setValue("audio_out_key", self.audio_out_key)
        self.settings.setValue("audio_out_name", self.audio_out_name)

    def open_settings_dialog(self):
        dlg = SettingsDialog(self)
        dlg.exec()

    # --- v0.3.7: раздел «Инструменты» ------------------------------------
    def show_tools_menu(self):
        """Меню «Инструменты»: служебные утилиты в отдельных окнах."""
        menu = QMenu(self)
        act_sync = menu.addAction("Синхронизатор текста…")
        act_sync.setIcon(make_icon("sync", QColor(T["sub1"]), 32))
        act_sync.triggered.connect(self.open_sync_dialog)
        menu.exec(self.btn_tools.mapToGlobal(
            QPoint(0, self.btn_tools.height())))

    def open_sync_dialog(self):
        """Синхронизатор текста: немодальное окно — плеер продолжает играть.

        Одно окно на приложение: повторный вызов поднимает уже открытое.
        """
        d = getattr(self, "_sync_dialog", None)
        if d is not None and d.isVisible():
            d.raise_()
            d.activateWindow()
            return
        d = LyricsSyncDialog(self)
        self._sync_dialog = d
        d.show()

    def on_volume_changed(self, v: int):
        self.audio_output.setVolume(v / 100.0)
        self.vol_pct_label.setText(f"{v}%")
        pos = self.player.position()
        self.visualizer.update_music_clock(pos, v / 100.0)
        self.mini_player.viz_widget.update_music_clock(pos, v / 100.0)
        # v0.2.0: запись в QSettings откладывается на 600 мс после последнего
        # изменения — раньше каждый шаг ползунка громкости делал запись INI
        # на диск (дисковый I/O прямо во время перетаскивания ползунка)
        if not hasattr(self, "_vol_save_timer"):
            self._vol_save_timer = QTimer(self)
            self._vol_save_timer.setSingleShot(True)
            self._vol_save_timer.setInterval(600)
            self._vol_save_timer.timeout.connect(
                lambda: self.settings.setValue("volume", self.volume_slider.value()))
        self._vol_save_timer.start()

    # --- Новые возможности v0.0.5 ---
    def apply_visualizer_settings(self):
        """Применяет флаги включения визуализаторов (экономия CPU на слабых ПК)."""
        main_on = self.visualizer_enabled
        mini_on = main_on and self.mini_visualizer_enabled
        self.vis_title.setVisible(main_on)
        self.visualizer.setVisible(main_on)
        self.visualizer.set_active(main_on)
        self.mini_player.viz_widget.setVisible(mini_on)
        self.mini_player.viz_widget.set_active(mini_on)

    def should_start_in_mini(self) -> bool:
        """Нужно ли открыть приложение сразу в миниплеере (если закрыли в нём)."""
        return self.remember_mini_mode and self.settings.value("was_in_mini", False, type=bool)

    def apply_mini_on_top(self):
        """Применяет к миниплееру настройку «поверх всех окон» (v0.1.5)."""
        self.mini_player.apply_on_top(self.mini_on_top)

    def show_mini_at_startup(self):
        """Запуск сразу в миниплеере без показа главного окна."""
        self.mini_player.restore_saved_geometry()
        self.mini_player.show()
        self.mini_player.raise_()

    def open_current_artist_page(self):
        """Клик по имени исполнителя — переход на его страницу во вкладку «Исполнители и альбомы»."""
        if 0 <= self.current_index < len(self.tracks):
            artist = self.tracks[self.current_index].artist
            self.tabs.setCurrentWidget(self.artists_view)
            self.artists_view.focus_artist(artist)
            self.status_bar.showMessage(f"Открыта страница исполнителя: {artist}")

    def setup_folder_watcher(self):
        watched_dirs = self.fs_watcher.directories()
        if watched_dirs:
            self.fs_watcher.removePaths(watched_dirs)
        self.watch_timer.stop()

        if not self.watched_folder:
            return

        folder_path = Path(self.watched_folder)
        if folder_path.is_dir() and self.auto_watch_enabled:
            dirs_to_watch = [str(folder_path)]
            try:
                for sub in folder_path.rglob("*"):
                    if sub.is_dir():
                        dirs_to_watch.append(str(sub))
            except Exception:
                pass
            self.fs_watcher.addPaths(dirs_to_watch[:250])
            self.watch_timer.start(self.watch_interval_sec * 1000)
            self.status_bar.showMessage(f"Мониторинг папки активен: {self.watched_folder}")

    def _on_fs_directory_changed(self, _path: str):
        QTimer.singleShot(1500, self.check_folder_for_new_tracks)

    def check_folder_for_new_tracks(self, manual: bool = False):
        if self.scan_worker and self.scan_worker.isRunning():
            return
        if self.folder_check_worker is not None and self.folder_check_worker.isRunning():
            return

        if not self.watched_folder or not Path(self.watched_folder).is_dir():
            if manual:
                QMessageBox.information(
                    self,
                    "Папка не выбрана",
                    "Сначала откройте папку через «Открыть папку» или укажите её в Настройках."
                )
            return

        # Обход дерева каталогов выполняется в фоне — иначе интерфейс (и миниплеер) зависает
        folder_path = Path(self.watched_folder)
        worker = FolderCheckWorker(folder_path)
        self.folder_check_worker = worker
        worker.checked.connect(
            lambda folder_str, files, m=manual: self._on_folder_checked(Path(folder_str), files, m)
        )
        worker.start()

    def _on_folder_checked(self, folder_path: Path, current_disk_files: Set[Path], manual: bool):
        removed_count = 0
        folder_resolved = folder_path.resolve()
        new_tracks_list: List[TrackInfo] = []
        for t in self.tracks:
            if folder_resolved in t.filepath.parents and t.filepath not in current_disk_files:
                self.known_paths.discard(t.filepath)
                removed_count += 1
            else:
                new_tracks_list.append(t)

        if removed_count > 0:
            self.playlist_model.update_tracks(new_tracks_list)
            self.apply_sorting()
            self.artists_view.rebuild_catalog(self.tracks)

        new_files = sorted(current_disk_files - self.known_paths)
        if new_files:
            self.status_bar.showMessage(f"Обнаружено новых треков: {len(new_files)}. Загрузка...")
            self.start_background_scan(new_files, clear_existing=False, auto_play_first=False)
        elif manual:
            msg = "Новых треков не обнаружено."
            if removed_count:
                msg += f" Удалено отсутствующих: {removed_count}."
            self.status_bar.showMessage(msg)

    def start_background_scan(
        self,
        paths: List[Path],
        clear_existing: bool = False,
        auto_play_first: bool = False
    ):
        if self.scan_worker and self.scan_worker.isRunning():
            self.scan_worker.cancel()
            self.scan_worker.wait()

        if clear_existing:
            self.playlist_model.update_tracks([])
            self.known_paths.clear()

        self.progress_bar.setValue(0)
        self.progress_label.setText("Подготовка к сканированию...")
        self.progress_widget.setVisible(True)

        self.scan_worker = ScanWorker(paths, auto_play_first=auto_play_first)
        self.scan_worker.progress.connect(self.on_scan_progress)
        self.scan_worker.finished_scan.connect(self.on_scan_finished)
        self.scan_worker.start()

    def on_scan_progress(self, current: int, total: int, filename: str):
        if total > 0:
            pct = int((current / total) * 100)
            self.progress_bar.setValue(pct)
            self.progress_label.setText(f"Добавление [{current}/{total}]: {filename}")

    def on_scan_finished(self, new_items: List[TrackInfo], auto_play_first: bool):
        self.progress_widget.setVisible(False)
        added = 0
        first_added_path: Optional[Path] = None

        combined = list(self.tracks)
        for item in new_items:
            if item.filepath not in self.known_paths:
                self.known_paths.add(item.filepath)
                combined.append(item)
                added += 1
                if first_added_path is None:
                    first_added_path = item.filepath
            elif auto_play_first and first_added_path is None:
                first_added_path = item.filepath

        self.playlist_model.update_tracks(combined)
        self.apply_sorting()
        self.artists_view.rebuild_catalog(self.tracks)
        self.status_bar.showMessage(f"Добавлено треков: {added}. Всего в плейлисте: {len(self.tracks)}.")

        # Однократное восстановление последнего трека/позиции после загрузки библиотеки
        if not self._restore_done:
            self._restore_done = True
            if not auto_play_first and self._restore_path is not None:
                restore_idx = self._find_restore_index(self._restore_path)
                if restore_idx is not None:
                    self.play_index(restore_idx, start_ms=self._restore_ms, autoplay=self._restore_playing)
                    state_str = "продолжается" if self._restore_playing else "на паузе"
                    self.status_bar.showMessage(
                        f"Восстановлен последний трек: {self.tracks[restore_idx].title} ({state_str} на {self._format_ms(self._restore_ms)})"
                    )
                else:
                    # Раньше провал был тихим: пользователь не видел причины,
                    # почему после нажатия Play всегда играет первый трек
                    self.status_bar.showMessage(
                        f"Не найден последний трек ({self._restore_path.name}) — "
                        "выберите песню вручную."
                    )

        if auto_play_first and first_added_path is not None:
            self.play_by_filepath(first_added_path)

    def _find_restore_index(self, path: Path) -> Optional[int]:
        """Ищет сохранённый трек в библиотеке в три прохода:
        1) точное совпадение пути;
        2) без учёта регистра и типа слэшей (особенности Windows);
        3) по уникальному имени файла — спасает при смене буквы диска
           или переименовании папки (если имён-двойников нет).
        Возвращает None, если трек не найден ни одним способом."""
        tracks = self.tracks
        for idx, t in enumerate(tracks):
            if t.filepath == path:
                return idx
        norm = os.path.normcase(str(path))
        for idx, t in enumerate(tracks):
            if os.path.normcase(str(t.filepath)) == norm:
                return idx
        name_matches = [idx for idx, t in enumerate(tracks) if t.filepath.name == path.name]
        if len(name_matches) == 1:
            return name_matches[0]
        return None

    def cancel_background_scan(self):
        if self.scan_worker and self.scan_worker.isRunning():
            self.scan_worker.cancel()
            self.progress_widget.setVisible(False)
            self.status_bar.showMessage("Сканирование отменено пользователем.")

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def showEvent(self, event: QEvent):
        # Тёмный заголовок окна в цвет темы (v0.1.9, Windows 10/11)
        apply_dark_title_bar(self)
        super().showEvent(event)
        # v0.2.2: следить за переносом окна на другой монитор
        wh = self.windowHandle()
        if wh is not None and not self._screen_conn_done:
            wh.screenChanged.connect(self._on_screen_changed)
            self._screen_conn_done = True
        # v0.2.2: вписать окно в рабочую область и ужать колонку под факт
        QTimer.singleShot(0, self._fit_window_on_show)

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        dropped_paths = [Path(u.toLocalFile()) for u in urls if u.isLocalFile()]
        if dropped_paths:
            self.start_background_scan(dropped_paths, clear_existing=False, auto_play_first=False)

    def changeEvent(self, event: QEvent):
        if event.type() == QEvent.Type.WindowStateChange:
            if self.isMinimized():
                QTimer.singleShot(0, self._on_main_minimized)
        super().changeEvent(event)

    def _on_main_minimized(self):
        """Сворачивание главного окна: одноразовая подсказка о настройке
        «Открывать миниплеер при сворачивании окна» + мини-режим (если включён)."""
        if self.minimize_to_mini and self.isMinimized():
            self.switch_to_mini()
        if self.minimize_to_mini:
            text = ("Сейчас при сворачивании главного окна открывается миниплеер в углу экрана.\n\n"
                    "Хотите, чтобы окно сворачивалось обычным образом — в панель задач?\n"
                    "Откройте Настройки и снимите галочку\n"
                    "«Открывать миниплеер при сворачивании окна».")
        else:
            text = ("Сейчас окно сворачивается обычным образом — в панель задач.\n\n"
                    "Хотите, чтобы при сворачивании автоматически открывался миниплеер?\n"
                    "Откройте Настройки и поставьте галочку\n"
                    "«Открывать миниплеер при сворачивании окна».")
        self.maybe_show_tip("main_minimize_mini", "Сворачивание окна", text)

    # --- Подсказки при первом использовании функций (v0.0.9) ---
    def maybe_show_tip(self, tip_id: str, title: str, text: str):
        """Показывает одноразовую подсказку при первом использовании функции.

        Каждая подсказка показывается один раз (флаг tip_<id>_shown в настройках)
        и только при включённом общем переключателе show_tips. Галочка
        «Больше не показывать» в диалоге отключает все последующие подсказки
        (можно вернуть галочкой в Настройках).
        """
        if not self.show_tips:
            return
        key = f"tip_{tip_id}_shown"
        if self.settings.value(key, False, type=bool):
            return
        self.settings.setValue(key, True)
        # Привязка к миниплееру, если он виден: главное окно может быть
        # свёрнуто (iconic-позиция -32000), и диалог по его центру уйдёт за экран
        parent = self.mini_player if self.mini_player.isVisible() else None
        box = QMessageBox(parent)
        # ВАЖНО: без этого атрибута закрытие диалога гасит всё приложение,
        # когда главное окно скрыто (minimize_to_mini): остаются видимыми только
        # Tool-окна, а Qt не считает их «главными» при проверке
        # quitOnLastWindowClosed -> QApplication завершается после нажатия «Понятно»
        box.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        box.setWindowTitle(title)
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(text)
        box.setCheckBox(QCheckBox("Больше не показывать подсказки"))
        ok_btn = box.addButton("Понятно", QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(ok_btn)
        box.exec()
        if box.checkBox().isChecked():
            self.show_tips = False
            self.save_settings()

    # --- Окно «Что нового» (v0.0.9) ---
    def maybe_show_whats_new(self):
        """При запуске новой версии показывает ПОЛНЫЙ список изменений всех
        версий (самая новая вверху). Показ — один раз на версию; открыть
        список ещё раз можно через Настройки → «Что нового»."""
        last = self.settings.value("whatsnew_last_version", "", type=str)
        if last == APP_VERSION:
            return
        self.open_whats_new_dialog()
        self.settings.setValue("whatsnew_last_version", APP_VERSION)

    def open_whats_new_dialog(self, since_version: str = ""):
        """Открывает окно «Что нового»; без аргумента — все версии (0.0.4 — текущая)."""
        dlg = WhatsNewDialog(changelog_html(since_version), parent=self)
        dlg.exec()

    def switch_to_mini(self):
        self.hide()
        self.mini_player.show()
        self.mini_player.raise_()

    def restore_from_mini(self):
        self.mini_player.hide()
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def closeEvent(self, event):
        # Запоминаем трек и позицию, чтобы продолжить песню при следующем запуске.
        # Путь берём из самого плеера (player.source), а не по индексу в списке:
        # после пересортировки/удаления файлов индекс может указывать не на тот трек,
        # и тогда при каждом запуске восстанавливалась одна и та же неверная песня
        loaded_path = self.player.source().toLocalFile()
        if self.restore_last_track and loaded_path:
            self.settings.setValue("last_track_path", loaded_path)
            self.settings.setValue("last_position_ms", int(self.player.position()))
            self.settings.setValue(
                "last_was_playing",
                self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            )
        else:
            self.settings.setValue("last_track_path", "")

        # Запоминаем режим миниплеера и его геометрию
        if self.remember_mini_mode:
            self.settings.setValue(
                "was_in_mini",
                self.mini_player.isVisible() and not self.isVisible()
            )
            g = self.mini_player.geometry()
            self.settings.setValue("mini_geometry", f"{g.x()},{g.y()},{g.width()},{g.height()}")
            self.settings.setValue("mini_expanded", self.mini_player.is_expanded)
        else:
            self.settings.setValue("was_in_mini", False)

        self.save_settings()
        if self.scan_worker and self.scan_worker.isRunning():
            self.scan_worker.cancel()
            self.scan_worker.wait()
        if self.folder_check_worker is not None and self.folder_check_worker.isRunning():
            self.folder_check_worker.cancel()
            self.folder_check_worker.wait(1000)
        # Корректно дожидаемся фоновых анализаторов, чтобы QThread не был разрушен на ходу
        for env_w in [self.env_worker] + self.old_env_workers:
            if env_w is not None and env_w.isRunning():
                env_w.wait(2000)
        self.mini_player.close()
        super().closeEvent(event)
        # Гарантированное завершение работы. Если закрываем из режима
        # миниплеера, главное окно скрыто: close() скрытого окна не запускает
        # проверку quitOnLastWindowClosed, а миниплеер — окно Qt.Tool, которое
        # Qt «главным» не считает. В итоге окна исчезают, но цикл событий
        # продолжает работать и музыка играет. Явный quit() завершает процесс.
        QApplication.quit()

    def _set_default_cover(self):
        icon_pix = create_hox_icon(188).pixmap(188, 188)
        self._cover_base = icon_pix  # v0.2.1: база для адаптивного масштаба
        self._apply_cover_pixmap()

        mini_pix = icon_pix.scaled(
            86, 86,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )
        self.mini_player.cover_label.setPixmap(rounded_pixmap(mini_pix, 10))

    def open_files(self):
        exts = " ".join(f"*{e}" for e in SUPPORTED_EXTENSIONS)
        files, _ = QFileDialog.getOpenFileNames(
            self, "Выберите аудиофайлы", "", f"Аудиофайлы ({exts});;Все файлы (*.*)"
        )
        if files:
            self.start_background_scan([Path(f) for f in files], clear_existing=False)

    def open_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Выберите папку с музыкой", self.watched_folder or ""
        )
        if folder:
            self.watched_folder = folder
            self.save_settings()
            self.setup_folder_watcher()
            self.start_background_scan([Path(folder)], clear_existing=True, auto_play_first=False)

    # --- Мгновенная сортировка ---
    def apply_sorting(self):
        self.settings.setValue("sort_index", self.sort_combo.currentIndex())
        if not self.tracks:
            self.mini_player.refresh_mini_playlist([], None, False)
            return

        current_filepath = (
            self.tracks[self.current_index].filepath
            if 0 <= self.current_index < len(self.tracks) else None
        )

        # v0.3.3: запоминаем прокрутку и выделение — сортировка не должна
        # ни дёргать таблицу, ни сбрасывать их
        vbar = self.table.verticalScrollBar()
        scroll_val = vbar.value()
        sel_rows = self.table.selectionModel().selectedRows()
        sel_row_before = sel_rows[0].row() if sel_rows else -1

        mode = self.sort_combo.currentIndex()
        if mode == 0:
            self.playlist_model.sort_in_place(lambda t: (t.folder_key, t.natural_file_key), False)
        elif mode == 1:
            self.playlist_model.sort_in_place(lambda t: (t.folder_key, t.natural_file_key), True)
        elif mode == 2:
            self.playlist_model.sort_in_place(lambda t: t.natural_file_key, False)
        elif mode == 3:
            self.playlist_model.sort_in_place(lambda t: t.natural_file_key, True)
        elif mode == 4:
            self.playlist_model.sort_in_place(lambda t: t.title_key, False)
        elif mode == 5:
            self.playlist_model.sort_in_place(lambda t: t.title_key, True)
        elif mode == 6:
            self.playlist_model.sort_in_place(lambda t: t.mtime, True)
        elif mode == 7:
            self.playlist_model.sort_in_place(lambda t: t.mtime, False)

        self.current_index = -1
        new_idx = -1
        if current_filepath is not None:
            for idx, t in enumerate(self.tracks):
                if t.filepath == current_filepath:
                    self.current_index = idx
                    new_idx = idx
                    break
        # v0.3.3: тихо возвращаем выделение на играющий трек (blockSignals —
        # чтобы selectionChanged не дёргал UI) и восстанавливаем прокрутку
        if new_idx >= 0 and new_idx != sel_row_before:
            self.table.blockSignals(True)
            self.table.selectRow(new_idx)
            self.table.blockSignals(False)
        vbar.setValue(scroll_val)
        # Миниплеер обновляем отложенно и только если он виден (v0.3.3)
        QTimer.singleShot(0, self._refresh_mini_playlist_async)

    def _refresh_mini_playlist_async(self):
        """v0.3.3: отложенная синхронизация миниплеера после сортировки.

        Скрытый миниплеер не пересобираем — только помечаем «грязным»;
        при показе showEvent сам запросит обновление.
        """
        if not self.mini_player.isVisible():
            self.mini_player.mark_playlist_dirty()
            return
        is_pl = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        self.mini_player.refresh_mini_playlist(
            self.tracks, self.playlist_model.playing_filepath, is_pl
        )

    # --- Воспроизведение ---
    def play_by_filepath(self, filepath: Path):
        for idx, t in enumerate(self.tracks):
            if t.filepath == filepath:
                self.play_index(idx)
                return

    def play_index(self, index: int, start_ms: int = 0, autoplay: bool = True):
        if not (0 <= index < len(self.tracks)):
            return
        self.current_index = index
        track = self.tracks[index]
        # v0.3.4: ручной сдвиг синхронизации сбрасывается для нового трека
        self.lyric_offset_ms = 0

        self.player.setSource(QUrl.fromLocalFile(str(track.filepath)))
        # Позиция перемотки применяется после загрузки медиа (см. LoadedMedia)
        self._pending_seek_ms = int(start_ms) if start_ms > 0 else -1
        if autoplay:
            self.player.play()

        self.title_label.setText(f"▶ {track.title}")
        self.artist_label.setText(track.artist)
        self.album_label.setText(track.album)
        self.mini_player.title_label.setText(f"▶ {track.title}")
        self.mini_player.artist_label.setText(track.artist)

        self.playlist_model.set_playing_state(track.filepath, autoplay)
        self.mini_player.refresh_mini_playlist(self.tracks, track.filepath, autoplay)
        self.table.selectRow(index)

        # Загрузка текста песни: караоке на панели управления + миниплеер
        # (v0.3.6: отдельная вкладка «Текст песни» убрана)
        lyrics_lines, is_synced = extract_lyrics(track.filepath, track.duration_sec)
        self.mini_player.set_mini_lyrics(lyrics_lines)
        self.karaoke.set_lyrics(lyrics_lines, is_synced)

        # Запуск фонового анализа огибающей для визуализатора в такт музыки
        seed = hash(track.filename)
        self.visualizer.set_envelope([], seed)
        self.mini_player.viz_widget.set_envelope([], seed)
        self.karaoke.set_envelope([])  # v0.3.4: сброс огибающей караоке
        if self.visualizer_enabled or self.mini_visualizer_enabled:
            if self.env_worker is not None:
                old_worker = self.env_worker
                try:
                    old_worker.envelope_ready.disconnect(self._on_envelope_ready)
                except TypeError:
                    pass
                self.old_env_workers.append(old_worker)

            self.env_worker = EnvelopeWorker(track.filepath)
            self.env_worker.envelope_ready.connect(self._on_envelope_ready)
            self.env_worker.start()
            self.old_env_workers = [w for w in self.old_env_workers if w.isRunning()]

        # Обложка
        cover_bytes = extract_cover_bytes(track.filepath)
        if cover_bytes:
            img = QImage.fromData(cover_bytes)
            if not img.isNull():
                base_pix = QPixmap.fromImage(img)
                self._cover_base = base_pix  # v0.2.1: база для адаптивного масштаба
                pix_main = base_pix.scaled(
                    188, 188,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
                pix_mini = base_pix.scaled(
                    86, 86,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
                self.cover_label.setPixmap(rounded_pixmap(pix_main, 12))
                self.mini_player.cover_label.setPixmap(rounded_pixmap(pix_mini, 10))
            else:
                self._set_default_cover()
        else:
            self._set_default_cover()

    def _on_envelope_ready(self, fpath_str: str, env: list):
        if 0 <= self.current_index < len(self.tracks):
            if str(self.tracks[self.current_index].filepath) == fpath_str:
                seed = hash(fpath_str)
                if self.visualizer_enabled:
                    self.visualizer.set_envelope(env, seed)
                if self.mini_visualizer_enabled:
                    self.mini_player.viz_widget.set_envelope(env, seed)
                # v0.3.4: огибающая уходит и в караоке — «умный» отсчёт
                self.karaoke.set_envelope(env)

    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            if self.current_index == -1 and self.tracks:
                self.play_index(0)
            else:
                self.player.play()

    def stop_track(self):
        self.player.stop()

    def prev_track(self):
        if not self.tracks:
            return
        new_idx = (self.current_index - 1) % len(self.tracks)
        self.play_index(new_idx)

    def next_track(self):
        if not self.tracks:
            return
        new_idx = (self.current_index + 1) % len(self.tracks)
        self.play_index(new_idx)

    def toggle_repeat(self, checked: bool):
        self.repeat_one = checked
        # v0.3.3: подпись без эмодзи, режим повтора показывает иконка
        self.btn_repeat.setText(" Повтор: ВКЛ" if checked else " Повтор: ВЫКЛ")
        self._update_repeat_icon()
        self.mini_player.btn_repeat.setChecked(checked)
        self.mini_player._update_repeat_icon()
        self.settings.setValue("repeat_one", checked)

    def on_table_double_click(self, index: QModelIndex):
        if index.isValid():
            self.play_index(index.row())

    def seek_position(self):
        self.slider_pressed = False
        self.player.setPosition(self.position_slider.value())

    def on_position_changed(self, pos: int):
        if not self.slider_pressed:
            self.position_slider.setValue(pos)
            self.mini_player.position_slider.setValue(pos)

        cur_str = self._format_ms(pos)
        tot_str = self.time_total.text()
        self.time_current.setText(cur_str)
        self.mini_player.time_label.setText(f"{cur_str} / {tot_str}")

        # Синхронизация визуализаторов и текста песни
        # (v0.3.6: таймер песни — штатные время/ползунок/длительность на
        # панели управления; ровный ход обеспечивает таймер 100 мс v0.3.5)
        vol_f = self.volume_slider.value() / 100.0
        self.visualizer.update_music_clock(pos, vol_f)
        self.mini_player.viz_widget.update_music_clock(pos, vol_f)
        row_idx, lyric_txt = self.karaoke.sync_position(pos)
        if row_idx >= 0 and lyric_txt:
            self.mini_player.highlight_mini_lyric_row(row_idx, lyric_txt)

    def on_duration_changed(self, dur: int):
        self.position_slider.setRange(0, dur)
        self.mini_player.position_slider.setRange(0, dur)
        tot_str = self._format_ms(dur)
        self.time_total.setText(tot_str)
        self.mini_player.time_label.setText(f"{self.time_current.text()} / {tot_str}")

    def _on_sync_tick(self):
        """v0.3.5: ровный ход текста/времени — опрос позиции каждые 100 мс.
        Дубликаты одной и той же позиции отбрасываются."""
        pos = self.player.position()
        if pos != self._last_sync_pos:
            self._last_sync_pos = pos
            self.on_position_changed(pos)

    def on_state_changed(self, state: QMediaPlayer.PlaybackState):
        is_playing = state == QMediaPlayer.PlaybackState.PlayingState
        # v0.3.3: play/pause — векторная иконка в цвете темы (у главной
        # кнопки — тёмный play_text на градиенте, у миниплеера — sub1)
        self.btn_play.setIcon(make_icon("pause" if is_playing else "play",
                                        QColor(T["play_text"]), 64))
        self.btn_play.setIconSize(QSize(28, 28))
        self.mini_player.btn_play.setIcon(make_icon(
            "pause" if is_playing else "play", QColor(T["sub1"]), 24))
        self.mini_player.btn_play.setIconSize(QSize(12, 12))
        self.visualizer.set_playing(is_playing)
        self.karaoke.set_playing(is_playing)
        self.mini_player.viz_widget.set_playing(is_playing)
        # v0.3.5: анимация текста только при игре; таймер синхронизации
        # стартует/останавливается вместе с игрой
        if is_playing:
            self._last_sync_pos = -1
            self._sync_timer.start()
        else:
            self._sync_timer.stop()
            self.on_position_changed(self.player.position())

        if 0 <= self.current_index < len(self.tracks):
            t = self.tracks[self.current_index]
            prefix = "▶" if is_playing else "⏸"
            self.title_label.setText(f"{prefix} {t.title}")
            self.mini_player.title_label.setText(f"{prefix} {t.title}")
            self.playlist_model.set_playing_state(t.filepath, is_playing)
            self.mini_player.refresh_mini_playlist(self.tracks, t.filepath, is_playing)

    def _apply_pending_seek(self):
        """Перемотка на сохранённую позицию после загрузки медиа."""
        if self._pending_seek_ms >= 0:
            self.player.setPosition(self._pending_seek_ms)
            self._pending_seek_ms = -1

    def on_media_status_changed(self, status: QMediaPlayer.MediaStatus):
        if status == QMediaPlayer.MediaStatus.LoadedMedia:
            # Продолжаем песню с сохранённой позиции (восстановление после запуска)
            self._apply_pending_seek()
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            if self.repeat_one:
                self.player.setPosition(0)
                self.player.play()
            else:
                self.next_track()

    @staticmethod
    def _format_ms(ms: int) -> str:
        seconds = max(0, ms // 1000)
        mins, secs = divmod(seconds, 60)
        return f"{mins:02d}:{secs:02d}"


# --- Глобальные мультимедиа-клавиши (v0.1.5, только Windows) ---------------
WM_HOTKEY = 0x0312
MOD_NOREPEAT = 0x4000
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_STOP = 0xB2
VK_MEDIA_PLAY_PAUSE = 0xB3

MEDIA_KEY_PLAYPAUSE = 1
MEDIA_KEY_STOP = 2
MEDIA_KEY_PREV = 3
MEDIA_KEY_NEXT = 4

MEDIA_HOTKEYS = (
    (MEDIA_KEY_PLAYPAUSE, VK_MEDIA_PLAY_PAUSE),
    (MEDIA_KEY_STOP, VK_MEDIA_STOP),
    (MEDIA_KEY_PREV, VK_MEDIA_PREV_TRACK),
    (MEDIA_KEY_NEXT, VK_MEDIA_NEXT_TRACK),
)


class MediaKeysFilter(QAbstractNativeEventFilter):
    """Ловит WM_HOTKEY от глобальных мультимедиа-клавиш.

    RegisterHotKey(None, ...) кладёт сообщение в очередь потока, а Qt как
    владелец цикла сообщений отдаёт его через nativeEventFilter — поэтому
    клавиши работают даже когда окно плеера не в фокусе (другие программы,
    игры, полноэкранный режим). Устанавливается только на Windows.
    """

    def __init__(self, dispatcher):
        super().__init__()
        self.dispatcher = dispatcher
        self.registered_ids: Tuple[int, ...] = ()
        self._msg = None
        if os.name == "nt":
            import ctypes.wintypes as wintypes
            self._msg = wintypes.MSG

    def nativeEventFilter(self, event_type, message):
        if self._msg is not None and bytes(event_type) == b"windows_generic_MSG":
            try:
                msg = self._msg.from_address(int(message))
                if msg.message == WM_HOTKEY:
                    self.dispatcher(int(msg.wParam))
            except Exception:
                # Горячие клавиши не должны ронять плеер ни при каких условиях
                pass
        return False, 0


def register_media_hotkeys(dispatcher) -> Optional[MediaKeysFilter]:
    """Регистрирует системные мультимедиа-клавиши за плеером (Windows).

    Если клавишу уже заняла другая программа — просто пропускаем её:
    конфликт не должен мешать запуску плеера. На других ОС — no-op.
    """
    if os.name != "nt":
        return None
    import ctypes
    user32 = ctypes.windll.user32
    registered = tuple(
        hotkey_id for hotkey_id, vk in MEDIA_HOTKEYS
        if user32.RegisterHotKey(None, hotkey_id, MOD_NOREPEAT, vk)
    )
    if not registered:
        return None
    filt = MediaKeysFilter(dispatcher)
    filt.registered_ids = registered
    QApplication.instance().installNativeEventFilter(filt)
    return filt


def unregister_media_hotkeys(filt: Optional[MediaKeysFilter]) -> None:
    """Снимает регистрацию мультимедиа-клавиш при выходе (Windows)."""
    if os.name != "nt" or filt is None or not filt.registered_ids:
        return
    import ctypes
    user32 = ctypes.windll.user32
    for hotkey_id in filt.registered_ids:
        user32.UnregisterHotKey(None, hotkey_id)
    filt.registered_ids = ()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setWindowIcon(create_hox_icon())
    # v0.3.0: тема применяется ДО создания окна — первый кадр уже в
    # выбранной теме (значение сохранено в QSettings «theme»)
    _theme_settings = QSettings("HoxMedia", "HoxMusicPlayer")
    apply_theme(theme_key_from_settings(_theme_settings))

    splash = HoxSplashScreen()
    splash.show()
    splash.set_status(f"Запуск {APP_FULL_TITLE}...")

    window = MediaPlayerWindow(initial_args=sys.argv[1:])

    # --- Глобальные мультимедиа-клавиши (v0.1.5): Play/Pause, Стоп,
    # Предыдущий/Следующий трек работают даже когда плеер не в фокусе —
    # в других программах, играх, полноэкранном режиме.
    def _media_key_dispatch(hotkey_id: int):
        if hotkey_id == MEDIA_KEY_PLAYPAUSE:
            window.toggle_play()
        elif hotkey_id == MEDIA_KEY_STOP:
            window.stop_track()
        elif hotkey_id == MEDIA_KEY_PREV:
            window.prev_track()
        elif hotkey_id == MEDIA_KEY_NEXT:
            window.next_track()

    _media_filter = register_media_hotkeys(_media_key_dispatch)
    app.aboutToQuit.connect(
        lambda: unregister_media_hotkeys(_media_filter))

    def _finish_startup():
        if window.should_start_in_mini():
            # Если плеер закрыли в миниплеере — открываемся сразу в нём
            window.show_mini_at_startup()
            splash.close()
        else:
            window.show()
            splash.finish(window)
        # Первая загрузка новой версии — показываем список изменений («Что нового»)
        window.maybe_show_whats_new()

    # v0.3.4: ждём анимацию сплэша (2 с пауза + 3 с заполнение) до показа окна
    QTimer.singleShot(5000, _finish_startup)
    sys.exit(app.exec())