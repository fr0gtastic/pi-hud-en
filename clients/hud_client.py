#!/usr/bin/env python3
"""Rana desktop pet client for Pi HUD's localhost NDJSON service."""
from __future__ import annotations

import argparse
import ctypes
import json
import queue
import random
import socket
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QPoint, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QBitmap,
    QColor,
    QFont,
    QFontMetrics,
    QImage,
    QPainter,
    QPainterPath,
    QRegion,
    QWheelEvent,
)
from PySide6.QtWidgets import QApplication, QMenu, QWidget

PROTOCOL_VERSION = 1
LOST_SERVICE_SECONDS = 12
CELL_COLS = 8
CELL_ROWS = 9
DRAG_THRESHOLD = 4
SCALE_BASE = 160
MIN_HEIGHT = 80
MAX_HEIGHT = 320
ALPHA_THRESHOLD_NOTE = 128  # QImage.createAlphaMask uses the midpoint threshold.


@dataclass(frozen=True)
class Clip:
    name: str
    row: int
    frames: int
    interval_ms: int


@dataclass
class PetLayout:
    width: int
    height: int
    character: QRect
    bubble: QRect | None
    bubble_path: QPainterPath | None
    font: QFont
    text: str


# The source is a fixed 8x9 grid. Shorter clips use only their leftmost cells.
CLIPS = {
    "WORKING": Clip("WORKING", 8, 6, 250),
    "WAITING_APPROVAL": Clip("WAITING_APPROVAL", 7, 6, 250),
    "COMPLETED": Clip("COMPLETED", 3, 4, 333),
    "ERROR": Clip("ERROR", 5, 8, 333),
}
IDLE_CLIPS = (Clip("IDLE_1", 0, 6, 333), Clip("IDLE_7", 6, 6, 333))
MOVE_RIGHT = Clip("MOVE_RIGHT", 1, 8, 250)
MOVE_LEFT = Clip("MOVE_LEFT", 2, 8, 250)
MOVE_VERTICAL = Clip("MOVE_VERTICAL", 4, 5, 250)
TEXT = {
    "IDLE": "◌  ( •_• )  Idle",
    "WORKING": "◆  (ง •̀_•́)ง  Working",
    "WAITING_APPROVAL": "?  ( •̀_•́ )!  Waiting for approval",
    "COMPLETED": "★  ( ✧◡✧ )  Goal achieved",
    "ERROR": "×  ( >_< )!  Error",
}
COLORS = {
    "IDLE": "#94a3b8",
    "WORKING": "#60a5fa",
    "WAITING_APPROVAL": "#fbbf24",
    "COMPLETED": "#4ade80",
    "ERROR": "#fb7185",
}
ROOT = Path(__file__).resolve().parent.parent
ASSET = ROOT / "assets" / "rana.webp"
CONFIG = Path.home() / ".pi" / "hud-pet.json"
LEGACY_CONFIG = Path.home() / ".pi" / "hud-position.json"
HWND_TOPMOST = -1
SWP_NOSIZE = 1
SWP_NOMOVE = 2
SWP_NOACTIVATE = 16
SW_RESTORE = 9


class Bridge(QObject):
    command = Signal(dict)
    diagnostic = Signal(dict)


def initial_foreground_window(platform: str) -> int:
    if platform != "windows" or sys.platform != "win32":
        return 0
    try:
        return int(ctypes.windll.user32.GetForegroundWindow())
    except Exception:
        return 0


def focus_window(hwnd: int) -> bool:
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        user32 = ctypes.windll.user32
        if not user32.IsWindow(hwnd) or not user32.IsWindowVisible(hwnd):
            return False
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        return bool(user32.SetForegroundWindow(hwnd))
    except Exception:
        return False


def acquire_single_instance(platform: str):
    if platform != "windows" or sys.platform != "win32":
        return None
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\PiFloatingStatusHud")
    return False if ctypes.windll.kernel32.GetLastError() == 183 else mutex


def move_clip_for(dx: int, dy: int) -> Clip:
    if abs(dx) >= abs(dy):
        return MOVE_RIGHT if dx > 0 else MOVE_LEFT
    return MOVE_VERTICAL


class RanaPet(QWidget):
    def __init__(self, bridge: Bridge, platform: str, restore_hwnd: int):
        super().__init__()
        self.bridge = bridge
        self.platform = platform
        self.restore_hwnd = restore_hwnd
        self.status = "IDLE"
        self.clip: Clip | None = None
        self.frame = 0
        self.bubble_enabled = True
        self.height_px = SCALE_BASE
        self.image = QImage()
        self.asset_error = ""
        self.layout: PetLayout | None = None
        self.region_cache: dict[tuple[int, int, int, int], QRegion] = {}
        self.press_global: QPoint | None = None
        self.press_origin: QPoint | None = None
        self.last_global: QPoint | None = None
        self.dragged = False
        self.saved_position: dict[str, Any] = {}

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_Hover, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.setMouseTracking(True)

        self.load_config()
        self.load_asset()
        self.frame_timer = QTimer(self)
        self.frame_timer.timeout.connect(self.advance_frame)
        self.set_clip(random.choice(IDLE_CLIPS), anchor=False)
        self.restore_position()

        bridge.command.connect(self.apply)
        self.topmost_timer = QTimer(self)
        self.topmost_timer.timeout.connect(self.force_topmost)
        self.topmost_timer.start(1500)
        self.force_topmost()
        self.report("started")

    def load_config(self) -> None:
        try:
            config = json.loads(CONFIG.read_text(encoding="utf8"))
        except Exception:
            try:
                config = json.loads(LEGACY_CONFIG.read_text(encoding="utf8"))
            except Exception:
                config = {}
        self.saved_position = config.get("position", config if "x" in config else {})
        self.bubble_enabled = bool(config.get("bubbleEnabled", True))
        self.height_px = max(MIN_HEIGHT, min(MAX_HEIGHT, int(config.get("height", SCALE_BASE))))

    def save_config(self) -> None:
        try:
            CONFIG.parent.mkdir(parents=True, exist_ok=True)
            CONFIG.write_text(
                json.dumps(
                    {
                        "position": {"x": self.x(), "y": self.y()},
                        "bubbleEnabled": self.bubble_enabled,
                        "height": int(self.height_px),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf8",
            )
        except OSError:
            pass

    def load_asset(self) -> None:
        if not ASSET.exists():
            self.asset_error = f"missing asset: {ASSET}"
            return
        image = QImage(str(ASSET))
        if image.isNull() or image.width() < CELL_COLS or image.height() < CELL_ROWS:
            self.asset_error = "Qt could not read assets/rana.webp"
            return
        if image.width() % CELL_COLS or image.height() % CELL_ROWS:
            self.asset_error = f"sprite {image.width()}x{image.height()} is not an 8x9 grid"
            return
        self.image = image

    def restore_position(self) -> None:
        try:
            x, y = int(self.saved_position["x"]), int(self.saved_position["y"])
            bounds = QRect(x, y, self.width(), self.height())
            if any(screen.availableGeometry().intersects(bounds) for screen in QApplication.screens()):
                self.move(x, y)
                return
        except Exception:
            pass
        area = QApplication.primaryScreen().availableGeometry()
        self.move(area.right() - self.width() - 24, area.top() + 24)

    def force_topmost(self) -> None:
        if self.platform == "windows" and sys.platform == "win32" and self.isVisible():
            ctypes.windll.user32.SetWindowPos(
                int(self.winId()), HWND_TOPMOST, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE
            )

    def scale(self) -> float:
        return self.height_px / SCALE_BASE

    def report(self, reason: str) -> None:
        clip = self.clip or IDLE_CLIPS[0]
        self.bridge.diagnostic.emit(
            {
                "type": "diagnostic",
                "asset": "ok" if not self.asset_error else self.asset_error,
                "status": self.status,
                "clip": clip.name,
                "row": clip.row + 1,
                "frame": self.frame + 1,
                "frames": clip.frames,
                "intervalMs": clip.interval_ms,
                "fps": round(1000 / clip.interval_ms, 1),
                "bubble": self.bubble_enabled,
                "height": int(self.height_px),
                "scale": round(self.scale(), 2),
                "reason": reason,
            }
        )

    def status_clip(self) -> Clip:
        if self.status == "IDLE":
            if self.clip in IDLE_CLIPS:
                return self.clip
            return random.choice(IDLE_CLIPS)
        return CLIPS.get(self.status, IDLE_CLIPS[0])

    def set_clip(self, clip: Clip, anchor: bool = True) -> None:
        if self.clip == clip:
            return
        self.clip = clip
        self.frame = 0
        self.frame_timer.setInterval(clip.interval_ms)
        if not self.frame_timer.isActive():
            self.frame_timer.start()
        self.update_layout(anchor=anchor)
        self.report("clip changed")

    def bubble_display_text(self) -> str:
        suffix = " ·" * ((self.frame % 3) + 1) if self.status == "WORKING" else ""
        return TEXT[self.status] + suffix

    def bubble_measure_text(self) -> str:
        return TEXT[self.status] + (" ···" if self.status == "WORKING" else "")

    def build_layout(self) -> PetLayout:
        scale = self.scale()
        outer = max(2, round(4 * scale))
        gap = max(3, round(7 * scale))
        character_h = int(self.height_px)
        if not self.image.isNull():
            cell_w = self.image.width() // CELL_COLS
            cell_h = self.image.height() // CELL_ROWS
        else:
            cell_w, cell_h = 192, 208
        character_w = max(1, round(character_h * cell_w / cell_h))

        font = QFont("Microsoft YaHei" if self.platform == "windows" else "sans-serif")
        font.setPixelSize(max(9, round(14 * scale)))
        metrics = QFontMetrics(font)
        bubble_rect: QRect | None = None
        bubble_path: QPainterPath | None = None
        bubble_w = 0
        bubble_h = 0
        text = ""
        if self.bubble_enabled:
            padding_x = max(9, round(14 * scale))
            padding_y = max(5, round(8 * scale))
            minimum = max(100, round(126 * scale))
            maximum = max(minimum, round(260 * scale))
            desired = metrics.horizontalAdvance(self.bubble_measure_text()) + padding_x * 2
            bubble_w = max(minimum, min(maximum, desired))
            bubble_h = metrics.height() + padding_y * 2
            max_text_width = bubble_w - padding_x * 2
            text = metrics.elidedText(self.bubble_display_text(), Qt.ElideRight, max_text_width)

        width = max(character_w, bubble_w) + outer * 2
        height = character_h + outer * 2
        character_y = outer
        if self.bubble_enabled:
            height += bubble_h + gap
            bubble_rect = QRect((width - bubble_w) // 2, outer, bubble_w, bubble_h)
            character_y = outer + bubble_h + gap
            radius = max(7, round(15 * scale))
            bubble_path = QPainterPath()
            bubble_path.addRoundedRect(QRectF(bubble_rect), radius, radius)
        character = QRect((width - character_w) // 2, character_y, character_w, character_h)
        return PetLayout(width, height, character, bubble_rect, bubble_path, font, text)

    def update_layout(self, anchor: bool = True) -> None:
        old_center_x = self.x() + self.width() // 2
        old_bottom = self.y() + self.height()
        self.layout = self.build_layout()
        self.setFixedSize(self.layout.width, self.layout.height)
        if anchor:
            self.move(old_center_x - self.width() // 2, old_bottom - self.height())
        self.update_mask()
        self.update()

    def character_region(self) -> QRegion:
        if self.layout is None or self.clip is None or self.image.isNull():
            return QRegion()
        target = self.layout.character
        key = (self.clip.row, self.frame, target.width(), target.height())
        cached = self.region_cache.get(key)
        if cached is None:
            cell_w = self.image.width() // CELL_COLS
            cell_h = self.image.height() // CELL_ROWS
            source = QRect(self.frame * cell_w, self.clip.row * cell_h, cell_w, cell_h)
            frame_image = self.image.copy(source).scaled(
                target.size(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation
            )
            alpha_mask = frame_image.createAlphaMask(Qt.AutoColor)
            cached = QRegion(QBitmap.fromImage(alpha_mask))
            self.region_cache[key] = cached
        return cached.translated(target.topLeft())

    def update_mask(self) -> None:
        if self.layout is None:
            return
        if self.image.isNull():
            self.setMask(QRegion(self.rect()))
            return
        region = self.character_region()
        if self.layout.bubble_path is not None:
            polygon = self.layout.bubble_path.toFillPolygon().toPolygon()
            region = region.united(QRegion(polygon))
        self.setMask(region if not region.isEmpty() else QRegion(self.rect()))

    def apply(self, message: dict[str, Any]) -> None:
        kind = message.get("type")
        if kind == "shutdown":
            self.save_config()
            QApplication.quit()
            return
        if kind == "visibility":
            self.setVisible(bool(message.get("visible")))
            self.force_topmost()
            return
        if kind not in ("state", "status") or message.get("status") not in TEXT:
            return
        next_status = message["status"]
        changed = next_status != self.status
        self.status = next_status
        if changed or (self.clip and self.clip.name.startswith("MOVE_")):
            self.set_clip(self.status_clip())
        else:
            self.update_layout()
        self.force_topmost()

    def advance_frame(self) -> None:
        if not self.isVisible() or self.clip is None:
            return
        self.frame = (self.frame + 1) % self.clip.frames
        if self.status == "WORKING" and self.layout is not None and self.layout.bubble is not None:
            metrics = QFontMetrics(self.layout.font)
            scale = self.scale()
            padding_x = max(9, round(14 * scale))
            self.layout.text = metrics.elidedText(
                self.bubble_display_text(), Qt.ElideRight, self.layout.bubble.width() - padding_x * 2
            )
        self.update_mask()
        self.update()

    def set_pet_height(self, height: float, persist: bool = True) -> None:
        next_height = max(MIN_HEIGHT, min(MAX_HEIGHT, int(round(height))))
        if next_height == int(self.height_px):
            return
        self.height_px = next_height
        self.region_cache.clear()
        self.update_layout(anchor=True)
        if persist:
            self.save_config()
        self.report("resized")

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        self.press_global = event.globalPosition().toPoint()
        self.press_origin = self.pos()
        self.last_global = self.press_global
        self.dragged = False
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self.press_global is None or self.press_origin is None or not (event.buttons() & Qt.LeftButton):
            return
        current = event.globalPosition().toPoint()
        total = current - self.press_global
        if not self.dragged and total.manhattanLength() < DRAG_THRESHOLD:
            return
        self.dragged = True
        self.move(self.press_origin + total)
        if self.last_global is not None:
            step = current - self.last_global
            if step.manhattanLength() >= DRAG_THRESHOLD:
                self.set_clip(move_clip_for(step.x(), step.y()))
                self.last_global = current
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.LeftButton or self.press_global is None:
            return
        if self.dragged:
            self.save_config()
            self.set_clip(self.status_clip())
        self.press_global = None
        self.press_origin = None
        self.last_global = None
        self.dragged = False
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            focus_window(self.restore_hwnd)
            event.accept()

    def wheelEvent(self, event: QWheelEvent) -> None:
        if not (event.modifiers() & Qt.ControlModifier):
            event.ignore()
            return
        steps = event.angleDelta().y() / 120
        if steps == 0:
            event.ignore()
            return
        self.set_pet_height(self.height_px + steps * 16)
        event.accept()

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        bubble = QAction("Hide Bubble" if self.bubble_enabled else "Show Bubble", menu)
        bubble.triggered.connect(self.toggle_bubble)
        menu.addAction(bubble)
        clip = self.clip or IDLE_CLIPS[0]
        info = QAction(
            f"Status: {self.status} ({clip.name}, Row {clip.row + 1}, "
            f"Frame {self.frame + 1}/{clip.frames}, {1000 / clip.interval_ms:.1f} FPS, "
            f"{int(self.height_px)}px)",
            menu,
        )
        info.setEnabled(False)
        menu.addAction(info)
        size_menu = menu.addMenu("Size")
        for label, height in (("Small", 120), ("Default", 160), ("Large", 220)):
            action = QAction(label, size_menu)
            action.triggered.connect(lambda _checked=False, value=height: self.set_pet_height(value))
            size_menu.addAction(action)
        reset = QAction("Reset Size", size_menu)
        reset.triggered.connect(lambda: self.set_pet_height(SCALE_BASE))
        size_menu.addAction(reset)
        top = QAction("Re-Pin", menu)
        top.triggered.connect(self.force_topmost)
        menu.addAction(top)
        hide = QAction("Hide Desktop Pet", menu)
        hide.triggered.connect(self.hide)
        menu.addAction(hide)
        quit_action = QAction("Exit Desktop Pet", menu)
        quit_action.triggered.connect(QApplication.quit)
        menu.addAction(quit_action)
        menu.exec(event.globalPos())

    def toggle_bubble(self) -> None:
        self.bubble_enabled = not self.bubble_enabled
        self.update_layout(anchor=True)
        self.save_config()
        self.report("bubble preference")

    def paintEvent(self, _event) -> None:
        if self.layout is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.Antialiasing)
        if self.layout.bubble_path is not None and self.layout.bubble is not None:
            painter.fillPath(self.layout.bubble_path, QColor(15, 23, 42, 238))
            painter.setPen(QColor(COLORS[self.status]))
            painter.drawPath(self.layout.bubble_path)
            painter.setFont(self.layout.font)
            painter.drawText(self.layout.bubble, Qt.AlignCenter, self.layout.text)
        if not self.image.isNull() and self.clip is not None:
            cell_w = self.image.width() // CELL_COLS
            cell_h = self.image.height() // CELL_ROWS
            source = QRect(self.frame * cell_w, self.clip.row * cell_h, cell_w, cell_h)
            painter.drawImage(self.layout.character, self.image, source)
        elif self.asset_error:
            painter.setPen(QColor("#fb7185"))
            painter.setFont(self.layout.font)
            painter.drawText(self.rect(), Qt.AlignCenter, "Rana failed to load assets\n" + self.asset_error)

    def closeEvent(self, event) -> None:
        self.save_config()
        event.accept()


def valid_server_message(message: Any, instance_id: str | None) -> bool:
    return (
        isinstance(message, dict)
        and message.get("protocolVersion") == PROTOCOL_VERSION
        and isinstance(message.get("instanceId"), str)
        and (instance_id is None or message["instanceId"] == instance_id)
    )


def receive(port: int, bridge: Bridge, reports: queue.Queue[dict[str, Any]]) -> None:
    instance_id: str | None = None
    last_verified = time.monotonic()
    while time.monotonic() - last_verified < LOST_SERVICE_SECONDS:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=3) as connection:
                connection.settimeout(0.25)
                connection.sendall((json.dumps({"type": "hello", "protocolVersion": PROTOCOL_VERSION}) + "\n").encode())
                buffer = b""
                last_heartbeat = 0.0
                while time.monotonic() - last_verified < LOST_SERVICE_SECONDS:
                    try:
                        while True:
                            report = reports.get_nowait()
                            report.update({"protocolVersion": PROTOCOL_VERSION, "instanceId": instance_id})
                            connection.sendall((json.dumps(report, ensure_ascii=False) + "\n").encode())
                    except queue.Empty:
                        pass
                    now = time.monotonic()
                    if now - last_heartbeat >= 1:
                        connection.sendall(
                            (
                                json.dumps(
                                    {
                                        "type": "heartbeat",
                                        "protocolVersion": PROTOCOL_VERSION,
                                        "instanceId": instance_id,
                                    }
                                )
                                + "\n"
                            ).encode()
                        )
                        last_heartbeat = now
                    try:
                        chunk = connection.recv(4096)
                    except socket.timeout:
                        continue
                    if not chunk:
                        break
                    buffer += chunk
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        try:
                            message = json.loads(line)
                        except (ValueError, TypeError):
                            continue
                        if not valid_server_message(message, instance_id):
                            continue
                        if instance_id is None:
                            instance_id = message["instanceId"]
                        last_verified = time.monotonic()
                        bridge.command.emit(message)
                        if message.get("type") == "shutdown":
                            return
        except OSError:
            time.sleep(0.5)
    bridge.command.emit({"type": "shutdown"})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=38741)
    parser.add_argument(
        "--platform",
        choices=("windows", "linux"),
        default="windows" if sys.platform == "win32" else "linux",
    )
    args = parser.parse_args()
    mutex = acquire_single_instance(args.platform)
    if mutex is False:
        return 0
    app = QApplication(sys.argv)
    bridge = Bridge()
    reports: queue.Queue[dict[str, Any]] = queue.Queue()
    bridge.diagnostic.connect(reports.put)
    pet = RanaPet(bridge, args.platform, initial_foreground_window(args.platform))
    pet.show()
    threading.Thread(target=receive, args=(args.port, bridge, reports), daemon=True).start()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
