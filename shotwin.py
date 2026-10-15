"""ShotWin — portable screenshot tool for Windows (PySide6).

Hotkeys (global, Windows):
  Ctrl+Shift+1  — area capture
  Ctrl+Shift+2  — full-screen capture
Editor: A arrow, R rect, T text, B blur, P pen, M marker, C color,
        Ctrl+Z undo, Ctrl+C copy, Ctrl+S save, Ctrl+P pin, Esc close.
"""
import sys
import ctypes
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QPoint, QPointF, QRect, QRectF, QSize, QAbstractNativeEventFilter, QSettings, QStandardPaths
from PySide6.QtGui import (QAction, QColor, QFont, QGuiApplication, QIcon, QImage, QKeySequence, QPainter,
                           QPainterPath, QPen, QPixmap, QPolygonF, QShortcut, QCursor)
from PySide6.QtWidgets import (QApplication, QColorDialog, QFileDialog, QInputDialog, QLabel, QMenu,
                               QSystemTrayIcon, QToolBar, QWidget, QMainWindow, QScrollArea, QMessageBox)

APP = "ShotWin"
IS_WIN = sys.platform == "win32"


# ---------------------------------------------------------------- capture
def grab_desktop():
    """Return (pixmap of whole virtual desktop in logical coords, virtual geometry)."""
    screens = QGuiApplication.screens()
    geo = QRect()
    for s in screens:
        geo = geo.united(s.geometry())
    dpr = max(s.devicePixelRatio() for s in screens)
    out = QPixmap(int(geo.width() * dpr), int(geo.height() * dpr))
    out.setDevicePixelRatio(dpr)
    out.fill(Qt.black)
    p = QPainter(out)
    for s in screens:
        g = s.geometry()
        p.drawPixmap(QRect(g.topLeft() - geo.topLeft(), g.size()), s.grabWindow(0))
    p.end()
    return out, geo


class Overlay(QWidget):
    """Full-screen dimmed overlay to select a region."""

    def __init__(self, on_done):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.on_done = on_done
        self.shot, geo = grab_desktop()
        self.setGeometry(geo)
        self.setCursor(Qt.CrossCursor)
        self.setMouseTracking(True)
        self.a = self.b = None
        self.mouse = QPoint()

    def sel(self):
        return QRect(self.a, self.b).normalized() if self.a is not None and self.b is not None else QRect()

    def paintEvent(self, _):
        p = QPainter(self)
        p.drawPixmap(self.rect(), self.shot)
        p.fillRect(self.rect(), QColor(0, 0, 0, 110))
        r = self.sel()
        if not r.isEmpty():
            dpr = self.shot.devicePixelRatio()
            p.drawPixmap(QRectF(r), self.shot, QRectF(r.x() * dpr, r.y() * dpr, r.width() * dpr, r.height() * dpr))
            p.setPen(QPen(QColor("#2d8cff"), 1))
            p.drawRect(r.adjusted(0, 0, -1, -1))
            txt = f"{r.width()} × {r.height()}"
            p.fillRect(QRect(r.left(), r.top() - 22, 90, 20), QColor(0, 0, 0, 180))
            p.setPen(Qt.white)
            p.drawText(r.left() + 5, r.top() - 7, txt)
        else:  # crosshair
            p.setPen(QPen(QColor(255, 255, 255, 120), 1, Qt.DashLine))
            p.drawLine(0, self.mouse.y(), self.width(), self.mouse.y())
            p.drawLine(self.mouse.x(), 0, self.mouse.x(), self.height())

    def mousePressEvent(self, e):
        if e.button() == Qt.RightButton:
            return self.close()
        self.a = self.b = e.position().toPoint()

    def mouseMoveEvent(self, e):
        self.mouse = e.position().toPoint()
        if self.a is not None and e.buttons() & Qt.LeftButton:
            self.b = self.mouse
        self.update()

    def mouseReleaseEvent(self, e):
        r = self.sel()
        self.close()
        if r.width() > 3 and r.height() > 3:
            dpr = self.shot.devicePixelRatio()
            px = self.shot.copy(QRect(int(r.x() * dpr), int(r.y() * dpr), int(r.width() * dpr), int(r.height() * dpr)))
            px.setDevicePixelRatio(dpr)
            self.on_done(px)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close()
        elif e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):  # whole desktop
            self.close()
            self.on_done(self.shot)


# ---------------------------------------------------------------- editor
class Canvas(QWidget):
    def __init__(self, pixmap):
        super().__init__()
        self.base = pixmap
        self.dpr = pixmap.devicePixelRatio()
        self.items = []  # (kind, color, data)
        self.tool = "arrow"
        self.color = QColor("#ff3b30")
        self.width_ = 4
        self.cur = None
        self.setFixedSize(pixmap.deviceIndependentSize().toSize())
        self.setCursor(Qt.CrossCursor)

    def render_image(self):
        img = QPixmap(self.base.size())
        img.setDevicePixelRatio(self.dpr)
        p = QPainter(img)
        p.drawPixmap(0, 0, self.base)
        for it in self.items:
            self.draw_item(p, *it)
        p.end()
        return img

    def draw_item(self, p, kind, color, data):
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(color, self.width_, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        if kind == "rect":
            p.drawRect(QRectF(data[0], data[1]).normalized())
        elif kind == "arrow":
            a, b = data
            p.drawLine(a, b)
            v = b - a
            ln = (v.x() ** 2 + v.y() ** 2) ** 0.5 or 1
            u = QPointF(v.x() / ln, v.y() / ln)
            n = QPointF(-u.y(), u.x())
            s = 6 + self.width_ * 3
            p.setBrush(color)
            p.drawPolygon(QPolygonF([b, b - u * s + n * s * 0.6, b - u * s - n * s * 0.6]))
        elif kind in ("pen", "marker"):
            path = QPainterPath(data[0])
            for pt in data[1:]:
                path.lineTo(pt)
            if kind == "marker":
                c = QColor(color)
                c.setAlpha(90)
                p.setPen(QPen(c, 18, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawPath(path)
        elif kind == "text":
            pos, text = data
            p.setFont(QFont("Segoe UI", 18, QFont.Bold))
            p.drawText(pos, text)
        elif kind == "blur":
            r = QRectF(data[0], data[1]).normalized().toRect()
            if r.width() < 2 or r.height() < 2:
                return
            src = self.base.copy(QRect(int(r.x() * self.dpr), int(r.y() * self.dpr),
                                       int(r.width() * self.dpr), int(r.height() * self.dpr))).toImage()
            small = src.scaled(max(1, src.width() // 12), max(1, src.height() // 12),
                               Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            p.drawImage(r, small.scaled(src.size()))

    def paintEvent(self, _):
        p = QPainter(self)
        p.drawPixmap(0, 0, self.base)
        for it in self.items + ([self.cur] if self.cur else []):
            self.draw_item(p, *it)

    def mousePressEvent(self, e):
        pt = e.position()
        if self.tool == "text":
            text, ok = QInputDialog.getText(self, "Text", "Text:")
            if ok and text:
                self.items.append(("text", QColor(self.color), (pt, text)))
                self.update()
            return
        if self.tool in ("pen", "marker"):
            self.cur = (self.tool, QColor(self.color), [pt])
        else:
            self.cur = (self.tool, QColor(self.color), [pt, pt])

    def mouseMoveEvent(self, e):
        if not self.cur:
            return
        if self.cur[0] in ("pen", "marker"):
            self.cur[2].append(e.position())
        else:
            self.cur[2][1] = e.position()
        self.update()

    def mouseReleaseEvent(self, _):
        if self.cur:
            self.items.append(self.cur)
            self.cur = None
            self.update()

    def undo(self):
        if self.items:
            self.items.pop()
            self.update()


def tool_icon(name, color=None):
    """Draw a 24×24 line icon (rendered at 2× for crispness)."""
    px = QPixmap(48, 48)
    px.setDevicePixelRatio(2)
    px.fill(Qt.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.Antialiasing)
    fg = QColor("#e8e8e8")
    p.setPen(QPen(fg, 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    if name == "arrow":
        p.drawLine(QPointF(5, 19), QPointF(18, 6))
        p.setBrush(fg)
        p.drawPolygon(QPolygonF([QPointF(19, 5), QPointF(11, 7), QPointF(17, 13)]))
    elif name == "rect":
        p.drawRoundedRect(QRectF(4, 6, 16, 12), 2, 2)
    elif name == "text":
        p.setFont(QFont("Segoe UI", 15, QFont.Bold))
        p.drawText(QRectF(0, 0, 24, 24), Qt.AlignCenter, "T")
    elif name == "blur":
        for i in range(3):
            for j in range(3):
                c = QColor(fg)
                c.setAlpha(255 if (i + j) % 2 == 0 else 110)
                p.fillRect(QRectF(4 + i * 5.5, 4 + j * 5.5, 4.5, 4.5), c)
    elif name == "pen":
        path = QPainterPath(QPointF(4, 17))
        path.cubicTo(QPointF(8, 5), QPointF(12, 21), QPointF(20, 7))
        p.drawPath(path)
    elif name == "marker":
        c = QColor("#ffd60a")
        c.setAlpha(170)
        p.setPen(QPen(c, 7, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(6, 15), QPointF(18, 9))
    elif name == "color":
        p.setPen(QPen(fg, 1.5))
        p.setBrush(color or QColor("#ff3b30"))
        p.drawEllipse(QRectF(4, 4, 16, 16))
    elif name == "undo":
        path = QPainterPath(QPointF(8, 9))
        path.lineTo(QPointF(15, 9))
        path.cubicTo(QPointF(21, 9), QPointF(21, 19), QPointF(15, 19))
        path.lineTo(QPointF(8, 19))
        p.drawPath(path)
        p.drawPolyline(QPolygonF([QPointF(11, 5), QPointF(7, 9), QPointF(11, 13)]))
    elif name == "copy":
        p.drawRoundedRect(QRectF(8, 8, 12, 12), 2, 2)
        p.drawPolyline(QPolygonF([QPointF(5, 15), QPointF(4, 15), QPointF(4, 4), QPointF(15, 4), QPointF(15, 5)]))
    elif name == "save":
        p.drawLine(QPointF(12, 4), QPointF(12, 15))
        p.drawPolyline(QPolygonF([QPointF(7, 10), QPointF(12, 15), QPointF(17, 10)]))
        p.drawPolyline(QPolygonF([QPointF(4, 16), QPointF(4, 20), QPointF(20, 20), QPointF(20, 16)]))
    elif name == "pin":
        p.drawPolygon(QPolygonF([QPointF(9, 4), QPointF(15, 4), QPointF(14, 10), QPointF(18, 14),
                                 QPointF(6, 14), QPointF(10, 10)]))
        p.drawLine(QPointF(12, 14), QPointF(12, 20))
    p.end()
    return QIcon(px)


TOOLBAR_QSS = """
QToolBar { background: #1e1e20; border: none; border-bottom: 1px solid #333; padding: 4px 6px; spacing: 2px; }
QToolBar::separator { background: #3a3a3c; width: 1px; margin: 6px 6px; }
QToolButton { color: #d0d0d0; background: transparent; border: none; border-radius: 7px;
              padding: 4px 6px; min-width: 46px; font-size: 11px; }
QToolButton:hover { background: #2f2f33; }
QToolButton:pressed { background: #3a3a40; }
QToolButton:checked { background: #2d8cff; color: white; }
"""


class Editor(QMainWindow):
    def __init__(self, app, pixmap):
        super().__init__()
        self.app = app
        self.setWindowTitle(f"{APP} — {pixmap.width()}×{pixmap.height()}")
        self.canvas = Canvas(pixmap)
        sa = QScrollArea()
        sa.setWidget(self.canvas)
        sa.setAlignment(Qt.AlignCenter)
        self.setCentralWidget(sa)
        tb = QToolBar()
        tb.setMovable(False)
        tb.setIconSize(QSize(22, 22))
        tb.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        tb.setStyleSheet(TOOLBAR_QSS)
        self.addToolBar(tb)
        for key, name, label in [("A", "arrow", "Arrow"), ("R", "rect", "Rectangle"), ("T", "text", "Text"),
                                 ("B", "blur", "Blur"), ("P", "pen", "Pen"), ("M", "marker", "Marker")]:
            act = QAction(tool_icon(name), label, self, shortcut=key, checkable=True)
            act.setToolTip(f"{label} ({key})")
            act.triggered.connect(lambda _=False, n=name: self.set_tool(n))
            act.setData(name)
            tb.addAction(act)
        tb.addSeparator()
        self.color_act = QAction(tool_icon("color", self.canvas.color), "Color", self, shortcut="C")
        self.color_act.setToolTip("Color (C)")
        self.color_act.triggered.connect(self.pick_color)
        tb.addAction(self.color_act)
        tb.addSeparator()
        for icon, text, sc, tip, fn in [("undo", "Undo", QKeySequence.Undo, "Ctrl+Z", self.canvas.undo),
                                        ("copy", "Copy", QKeySequence.Copy, "Ctrl+C", self.copy),
                                        ("save", "Save", QKeySequence.Save, "Ctrl+S", self.save),
                                        ("pin", "Pin", "Ctrl+P", "Ctrl+P", self.pin)]:
            act = QAction(tool_icon(icon), text, self, shortcut=sc)
            act.setToolTip(f"{text} ({tip})")
            act.triggered.connect(fn)
            tb.addAction(act)
        self.tb = tb
        QShortcut(Qt.Key_Escape, self, self.close)
        self.set_tool("arrow")
        scr = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        avail = scr.availableGeometry()
        sz = self.canvas.size()
        self.resize(min(sz.width() + 40, avail.width() - 80), min(sz.height() + 90, avail.height() - 80))
        self.move(avail.center() - self.rect().center())

    def set_tool(self, name):
        self.canvas.tool = name
        for a in self.tb.actions():
            if a.isCheckable():
                a.setChecked(a.data() == name)

    def pick_color(self):
        c = QColorDialog.getColor(self.canvas.color, self)
        if c.isValid():
            self.canvas.color = c
            self.color_act.setIcon(tool_icon("color", c))

    def copy(self):
        QGuiApplication.clipboard().setImage(self.canvas.render_image().toImage())
        self.statusBar().showMessage("Copied to clipboard", 2000)

    def save(self):
        folder = self.app.settings.value("save_dir", QStandardPaths.writableLocation(QStandardPaths.PicturesLocation))
        name = f"{folder}/Screenshot {datetime.now():%Y-%m-%d %H.%M.%S}.png"
        path, _ = QFileDialog.getSaveFileName(self, "Save", name, "PNG (*.png);;JPEG (*.jpg)")
        if path:
            self.canvas.render_image().save(path)
            self.app.settings.setValue("save_dir", str(Path(path).parent))
            self.statusBar().showMessage(f"Saved {path}", 3000)

    def pin(self):
        self.app.pin(self.canvas.render_image())
        self.close()


# ---------------------------------------------------------------- pin
class Pin(QLabel):
    """Always-on-top floating screenshot. Drag to move, wheel = opacity, Ctrl+wheel = zoom, Esc/double-click = close."""

    def __init__(self, pixmap):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.src = pixmap
        self.scale = 1.0
        self.setPixmap(pixmap)
        self.setStyleSheet("border: 1px solid #2d8cff;")
        self.drag = None
        self.move(QCursor.pos() - QPoint(20, 20))

    def mousePressEvent(self, e):
        self.drag = e.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, e):
        if self.drag is not None:
            self.move(e.globalPosition().toPoint() - self.drag)

    def mouseReleaseEvent(self, _):
        self.drag = None

    def mouseDoubleClickEvent(self, _):
        self.close()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close()
        elif e.matches(QKeySequence.Copy):
            QGuiApplication.clipboard().setPixmap(self.src)

    def wheelEvent(self, e):
        d = 1 if e.angleDelta().y() > 0 else -1
        if e.modifiers() & Qt.ControlModifier:
            self.scale = max(0.2, min(4.0, self.scale + 0.1 * d))
            sz = self.src.deviceIndependentSize() * self.scale
            self.setPixmap(self.src.scaled((sz * self.src.devicePixelRatio()).toSize(),
                                           Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self.adjustSize()
        else:
            self.setWindowOpacity(max(0.2, min(1.0, self.windowOpacity() + 0.1 * d)))


# ---------------------------------------------------------------- global hotkeys (Win32)
class HotkeyFilter(QAbstractNativeEventFilter):
    WM_HOTKEY = 0x0312

    def __init__(self, callbacks):
        super().__init__()
        self.callbacks = callbacks  # id -> fn

    def nativeEventFilter(self, etype, message):
        if etype == b"windows_generic_MSG":
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == self.WM_HOTKEY and msg.wParam in self.callbacks:
                self.callbacks[msg.wParam]()
                return True, 0
        return False, 0


def register_hotkeys(app, mapping):
    """mapping: list of (id, modifiers, vk, fn). Returns filter or None."""
    if not IS_WIN:
        return None
    user32 = ctypes.windll.user32
    MOD_NOREPEAT = 0x4000
    failed = []
    for hid, mods, vk, _ in mapping:
        if not user32.RegisterHotKey(None, hid, mods | MOD_NOREPEAT, vk):
            failed.append(hid)
    f = HotkeyFilter({hid: fn for hid, _, _, fn in mapping})
    app.installNativeEventFilter(f)
    if failed:
        QMessageBox.warning(None, APP, "Some hotkeys are already used by another program.")
    return f


# ---------------------------------------------------------------- app
class ShotApp:
    def __init__(self):
        self.qt = QApplication(sys.argv)
        self.qt.setQuitOnLastWindowClosed(False)
        # portable: settings in .ini next to the exe
        base = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
        self.settings = QSettings(str(base / "shotwin.ini"), QSettings.IniFormat)
        self.windows = []

        icon = self.make_icon()
        self.qt.setWindowIcon(icon)
        self.tray = QSystemTrayIcon(icon)
        m = QMenu()
        m.addAction("Area capture\tCtrl+Shift+1", self.area)
        m.addAction("Full-screen capture\tCtrl+Shift+2", self.full)
        m.addAction("Open image…", self.open_file)
        m.addSeparator()
        m.addAction("Quit", self.qt.quit)
        self.tray.setContextMenu(m)
        self.tray.activated.connect(lambda r: r == QSystemTrayIcon.Trigger and self.area())
        self.tray.setToolTip(APP)
        self.tray.show()

        MOD_CTRL, MOD_SHIFT = 0x2, 0x4
        self.hk = register_hotkeys(self.qt, [
            (1, MOD_CTRL | MOD_SHIFT, 0x31, self.area),
            (2, MOD_CTRL | MOD_SHIFT, 0x32, self.full),
        ])

    @staticmethod
    def make_icon():
        px = QPixmap(64, 64)
        px.fill(Qt.transparent)
        p = QPainter(px)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor("#2d8cff"))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(4, 4, 56, 56, 14, 14)
        p.setPen(QPen(Qt.white, 6))
        p.drawLine(18, 14, 18, 46); p.drawLine(18, 46, 50, 46)
        p.drawLine(46, 50, 46, 18); p.drawLine(46, 18, 14, 18)
        p.end()
        return QIcon(px)

    def keep(self, w):
        self.windows.append(w)
        w.destroyed.connect(lambda *_: self.windows.remove(w) if w in self.windows else None)
        w.setAttribute(Qt.WA_DeleteOnClose)
        w.show()
        w.raise_()
        w.activateWindow()

    def area(self):
        self.keep(Overlay(self.edit))

    def full(self):
        self.edit(grab_desktop()[0])

    def edit(self, px):
        self.keep(Editor(self, px))

    def pin(self, px):
        self.keep(Pin(px))

    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(None, "Open", "", "Images (*.png *.jpg *.jpeg *.gif *.bmp)")
        if path:
            self.edit(QPixmap(path))

    def run(self):
        if len(sys.argv) > 1 and Path(sys.argv[1]).is_file():
            self.edit(QPixmap(sys.argv[1]))
        return self.qt.exec()


if __name__ == "__main__":
    if IS_WIN:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass
    sys.exit(ShotApp().run())
