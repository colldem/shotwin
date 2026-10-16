"""ShotWin — portable screenshot tool for Windows (PySide6).

Hotkeys (global, Windows):
  Ctrl+Shift+1  — area capture
  Ctrl+Shift+2  — full-screen capture (screen under the mouse)
  Ctrl+Shift+3  — all screens
Editor: A arrow, N numbered steps, R shapes (rectangle / rounded / ellipse / line), T text, B blur, P pen, M marker,
        1/2/3 width of the current tool, C color, Ctrl+Z undo, Ctrl+C copy, Ctrl+S save, Esc close.
"""
import sys
import ctypes
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QPoint, QPointF, QRect, QRectF, QSize, QSizeF, QAbstractNativeEventFilter, QSettings, QStandardPaths
from PySide6.QtGui import (QAction, QColor, QFont, QFontMetrics, QGuiApplication, QIcon, QImage, QKeySequence, QPainter,
                           QPainterPath, QPen, QPixmap, QPolygonF, QShortcut, QCursor, QActionGroup)
from PySide6.QtWidgets import (QApplication, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QInputDialog,
                               QHBoxLayout, QLabel, QMenu, QPlainTextEdit, QSpinBox, QToolButton, QVBoxLayout,
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


def grab_screen(screen):
    """Capture a single monitor at its native resolution."""
    px = screen.grabWindow(0)
    px.setDevicePixelRatio(screen.devicePixelRatio())
    return px


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
FONTS = ["Segoe UI", "Arial", "Times New Roman", "Georgia", "Consolas", "Comic Sans MS"]
WIDTHS = [("Thin", 2), ("Medium", 4), ("Thick", 8)]
SHAPES = [("rect", "Rectangle"), ("rrect", "Rounded"), ("ellipse", "Ellipse"), ("line", "Line")]
WIDTH_TOOLS = ("arrow", "shape", "pen", "marker", "step")
STEP_RADIUS = {2: 11, 4: 15, 8: 21}  # Thin / Medium / Thick badge size


class TextDialog(QDialog):
    """Text input with font family, size and Bold / Italic / Underline."""

    def __init__(self, parent, item=None):
        super().__init__(parent)
        self.setWindowTitle("Text")
        it = item or {"text": "", "family": "Segoe UI", "size": 18, "bold": True, "italic": False, "underline": False}
        self.edit = QPlainTextEdit(it["text"])
        self.edit.setFixedHeight(80)
        self.family = QComboBox()
        self.family.addItems(FONTS)
        self.family.setCurrentText(it["family"])
        self.size = QSpinBox()
        self.size.setRange(8, 96)
        self.size.setValue(it["size"])
        self.size.setSuffix(" pt")
        row = QHBoxLayout()
        row.addWidget(self.family, 1)
        row.addWidget(self.size)
        self.flags = {}
        for key, label, tip in [("bold", "B", "Bold (Ctrl+B)"), ("italic", "I", "Italic (Ctrl+I)"),
                                ("underline", "U", "Underline (Ctrl+U)")]:
            b = QToolButton(text=label, checkable=True, checked=it[key])
            f = b.font()
            f.setBold(key == "bold"); f.setItalic(key == "italic"); f.setUnderline(key == "underline")
            b.setFont(f)
            b.setToolTip(tip)
            b.setFixedSize(30, 28)
            b.setShortcut(f"Ctrl+{label}")
            b.toggled.connect(self.refresh)
            self.flags[key] = b
            row.addWidget(b)
        self.family.currentTextChanged.connect(self.refresh)
        self.size.valueChanged.connect(self.refresh)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(row)
        lay.addWidget(self.edit)
        lay.addWidget(QLabel("Tip: drag text on the image to move it, double-click to edit."))
        lay.addWidget(bb)
        self.refresh()
        self.edit.setFocus()

    def values(self):
        return {"text": self.edit.toPlainText(), "family": self.family.currentText(), "size": self.size.value(),
                **{k: b.isChecked() for k, b in self.flags.items()}}

    def refresh(self):
        self.edit.setFont(item_font(self.values()))


def item_font(it):
    f = QFont(it["family"], it["size"])
    f.setBold(it["bold"]); f.setItalic(it["italic"]); f.setUnderline(it["underline"])
    return f


class Canvas(QWidget):
    def __init__(self, pixmap):
        super().__init__()
        self.base = pixmap
        self.dpr = pixmap.devicePixelRatio()
        self.items = []  # dicts: kind, color, width, pts | text fields
        self.history = []  # snapshots of self.items for undo
        self.tool = "arrow"
        self.color = QColor("#ff3b30")
        self.widths = {t: 4 for t in WIDTH_TOOLS}  # each tool keeps its own thickness
        self.shape = "rect"
        self.cur = None
        self.drag = None  # (text item, offset)
        self.setFixedSize(pixmap.deviceIndependentSize().toSize())
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)

    # --- state
    def snapshot(self):
        self.history.append([dict(it, pts=list(it["pts"])) if "pts" in it else dict(it) for it in self.items])

    def undo(self):
        if self.history:
            self.items = self.history.pop()
            self.update()

    def text_rect(self, it):
        fm = QFontMetrics(item_font(it))
        r = fm.boundingRect(QRect(0, 0, 4000, 4000), Qt.AlignLeft | Qt.AlignTop, it["text"])
        return QRectF(it["pos"], QSizeF(r.width() + 4, r.height()))

    def obj_rect(self, it):
        if it["kind"] == "step":
            r = STEP_RADIUS.get(it["width"], 15)
            return QRectF(it["pos"] - QPointF(r, r), QSizeF(2 * r, 2 * r))
        return self.text_rect(it)

    def obj_at(self, pt):
        """Movable object (text or step badge) under the point."""
        for it in reversed(self.items):
            if it["kind"] in ("text", "step") and self.obj_rect(it).adjusted(-4, -4, 4, 4).contains(pt):
                return it
        return None

    def next_step(self):
        return max((it["n"] for it in self.items if it["kind"] == "step"), default=0) + 1

    # --- drawing
    def render_image(self):
        img = QPixmap(self.base.size())
        img.setDevicePixelRatio(self.dpr)
        p = QPainter(img)
        p.drawPixmap(0, 0, self.base)
        for it in self.items:
            self.draw_item(p, it)
        p.end()
        return img

    def draw_item(self, p, it):
        kind, color, w = it["kind"], it["color"], it.get("width", 4)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(color, w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        if kind in ("rect", "rrect", "ellipse"):
            r = QRectF(*it["pts"]).normalized()
            if kind == "rect":
                p.drawRect(r)
            elif kind == "rrect":
                rad = min(12 + w * 2, r.width() / 2, r.height() / 2)
                p.drawRoundedRect(r, rad, rad)
            else:
                p.drawEllipse(r)
        elif kind == "line":
            p.drawLine(*it["pts"])
        elif kind == "arrow":
            a, b = it["pts"]
            v = b - a
            ln = (v.x() ** 2 + v.y() ** 2) ** 0.5 or 1
            u = QPointF(v.x() / ln, v.y() / ln)
            n = QPointF(-u.y(), u.x())
            s = 8 + w * 2.5
            p.drawLine(a, b - u * (s * 0.6))
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawPolygon(QPolygonF([b, b - u * s + n * s * 0.55, b - u * s - n * s * 0.55]))
        elif kind in ("pen", "marker"):
            pts = it["pts"]
            path = QPainterPath(pts[0])
            for pt in pts[1:]:
                path.lineTo(pt)
            if kind == "marker":
                c = QColor(color)
                c.setAlpha(90)
                p.setPen(QPen(c, 6 + w * 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawPath(path)
        elif kind == "text":
            p.setPen(color)
            p.setFont(item_font(it))
            r = self.text_rect(it)
            p.drawText(r.adjusted(0, 0, 100, 100), Qt.AlignLeft | Qt.AlignTop, it["text"])
        elif kind == "step":
            r = self.obj_rect(it)
            p.setPen(QPen(QColor(255, 255, 255, 230), max(1.5, r.width() / 16)))
            p.setBrush(color)
            p.drawEllipse(r)
            f = QFont("Segoe UI", 1, QFont.Bold)
            f.setPixelSize(int(r.height() * (0.58 if it["n"] < 10 else 0.46)))
            p.setFont(f)
            p.setPen(Qt.white if color.lightness() < 170 else Qt.black)
            p.drawText(r, Qt.AlignCenter, str(it["n"]))
        elif kind == "blur":
            r = QRectF(*it["pts"]).normalized().toRect()
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
            self.draw_item(p, it)
        if self.drag:
            p.setPen(QPen(QColor("#2d8cff"), 1, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRect(self.obj_rect(self.drag[0]).adjusted(-3, -3, 3, 3))

    # --- mouse
    def edit_text(self, item=None, pos=None):
        dlg = TextDialog(self, item)
        if dlg.exec() != QDialog.Accepted or not dlg.values()["text"].strip():
            return
        self.snapshot()
        if item:
            item.update(dlg.values())
        else:
            self.items.append({"kind": "text", "color": QColor(self.color), "pos": pos, **dlg.values()})
        self.update()

    def mousePressEvent(self, e):
        pt = e.position()
        if e.button() != Qt.LeftButton:
            return
        hit = self.obj_at(pt)
        if hit:  # any tool: grab text / step badge to move it
            self.snapshot()
            self.drag = (hit, pt - hit["pos"])
            self.setCursor(Qt.ClosedHandCursor)
            self.update()
            return
        if self.tool == "text":
            self.edit_text(pos=pt)
            return
        if self.tool == "step":  # click = next numbered badge
            self.snapshot()
            self.items.append({"kind": "step", "color": QColor(self.color), "width": self.widths["step"],
                               "pos": pt, "n": self.next_step()})
            self.update()
            return
        n = 1 if self.tool in ("pen", "marker") else 2
        kind = self.shape if self.tool == "shape" else self.tool
        self.cur = {"kind": kind, "color": QColor(self.color), "width": self.widths.get(self.tool, 4), "pts": [pt] * n}

    def mouseDoubleClickEvent(self, e):
        hit = self.obj_at(e.position())
        if hit:
            self.drag = None
            self.history.pop()  # the press already snapshotted
            if hit["kind"] == "text":
                self.edit_text(hit)
            else:
                n, ok = QInputDialog.getInt(self, "Step", "Number:", hit["n"], 1, 999)
                if ok:
                    self.snapshot()
                    hit["n"] = n
                    self.update()

    def mouseMoveEvent(self, e):
        pt = e.position()
        if self.drag:
            self.drag[0]["pos"] = pt - self.drag[1]
            self.update()
            return
        if not self.cur:
            self.setCursor(Qt.OpenHandCursor if self.obj_at(pt) else Qt.CrossCursor)
            return
        if self.cur["kind"] in ("pen", "marker"):
            self.cur["pts"].append(pt)
        elif e.modifiers() & Qt.ShiftModifier and self.cur["kind"] in ("rect", "rrect", "ellipse", "blur"):
            a = self.cur["pts"][0]  # Shift: square / circle
            d = max(abs(pt.x() - a.x()), abs(pt.y() - a.y()))
            self.cur["pts"][1] = QPointF(a.x() + (d if pt.x() >= a.x() else -d), a.y() + (d if pt.y() >= a.y() else -d))
        else:
            self.cur["pts"][1] = pt
        self.update()

    def mouseReleaseEvent(self, _):
        if self.drag:
            self.drag = None
            self.setCursor(Qt.OpenHandCursor)
            self.update()
        elif self.cur:
            self.snapshot()
            self.items.append(self.cur)
            self.cur = None
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
        p.drawRect(QRectF(4, 6, 16, 12))
    elif name == "rrect":
        p.drawRoundedRect(QRectF(4, 6, 16, 12), 4, 4)
    elif name == "ellipse":
        p.drawEllipse(QRectF(3, 6, 18, 12))
    elif name == "line":
        p.drawLine(QPointF(5, 19), QPointF(19, 5))
    elif name == "step":
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#ff3b30"))
        p.drawEllipse(QRectF(3, 3, 18, 18))
        p.setPen(Qt.white)
        p.setFont(QFont("Segoe UI", 10, QFont.Bold))
        p.drawText(QRectF(3, 3, 18, 18), Qt.AlignCenter, "1")
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
    elif name.startswith("width:"):  # thickness sample for the width menus
        p.setPen(QPen(fg, min(int(name.split(":")[1]), 7), Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(4, 12), QPointF(20, 12))
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
QToolButton[popupMode="1"] { padding-right: 14px; }
QToolButton::menu-button { border: none; width: 12px; }
QToolButton::menu-indicator { image: none; width: 0; }
QMenu { background: #26262a; color: #e8e8e8; border: 1px solid #3a3a3c; padding: 4px; }
QMenu::item { padding: 6px 24px 6px 8px; border-radius: 5px; }
QMenu::item:checked { background: #3a3a40; }
QMenu::item:selected { background: #2d8cff; }
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
        self.tool_acts = {}
        self.width_groups = {}
        for key, name, label in [("A", "arrow", "Arrow"), ("R", "shape", "Rectangle"), ("T", "text", "Text"), ("N", "step", "Step"),
                                 ("B", "blur", "Blur"), ("P", "pen", "Pen"), ("M", "marker", "Marker")]:
            act = QAction(tool_icon("rect" if name == "shape" else name), label, self, shortcut=key, checkable=True)
            act.setToolTip(f"{label} ({key})")
            act.triggered.connect(lambda _=False, n=name: self.set_tool(n))
            act.setData(name)
            tb.addAction(act)
            self.tool_acts[name] = act
            if name in WIDTH_TOOLS:  # dropdown ▾ with shapes (for "shape") and Thin / Medium / Thick
                menu = QMenu(self)
                menu.setStyleSheet(TOOLBAR_QSS)
                if name == "shape":
                    sg = QActionGroup(menu)
                    for kind, slabel in SHAPES:
                        sa_ = menu.addAction(tool_icon(kind), slabel)
                        sa_.setCheckable(True)
                        sa_.setChecked(kind == "rect")
                        sg.addAction(sa_)
                        sa_.triggered.connect(lambda _=False, k=kind, l=slabel: self.set_shape(k, l))
                    menu.addSeparator()
                wg = QActionGroup(menu)
                for i, (wlabel, w) in enumerate(WIDTHS, 1):
                    wa = menu.addAction(tool_icon(f"width:{w}"), f"{wlabel}	{i}")
                    wa.setCheckable(True)
                    wa.setChecked(w == 4)
                    wa.setData(w)
                    wg.addAction(wa)
                    wa.triggered.connect(lambda _=False, n=name, w=w: self.set_width(n, w))
                self.width_groups[name] = wg
                act.setMenu(menu)
                tb.widgetForAction(act).setPopupMode(QToolButton.MenuButtonPopup)
        for i, (wlabel, w) in enumerate(WIDTHS, 1):  # 1 / 2 / 3 = width of the current tool
            QShortcut(str(i), self, lambda w=w: self.set_width(self.canvas.tool, w))
        tb.addSeparator()
        self.color_act = QAction(tool_icon("color", self.canvas.color), "Color", self, shortcut="C")
        self.color_act.setToolTip("Color (C)")
        self.color_act.triggered.connect(self.pick_color)
        tb.addAction(self.color_act)
        tb.addSeparator()
        for icon, text, sc, tip, fn in [("undo", "Undo", QKeySequence.Undo, "Ctrl+Z", self.canvas.undo),
                                        ("copy", "Copy", QKeySequence.Copy, "Ctrl+C", self.copy),
                                        ("save", "Save", QKeySequence.Save, "Ctrl+S", self.save)]:
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

    def set_width(self, tool, w):
        if tool not in WIDTH_TOOLS:
            return
        self.canvas.widths[tool] = w
        for a in self.width_groups[tool].actions():
            a.setChecked(a.data() == w)
        self.set_tool(tool)
        label = next(l for l, v in WIDTHS if v == w)
        self.statusBar().showMessage(f"{self.tool_acts[tool].text()}: {label}", 1500)

    def set_shape(self, kind, label):
        self.canvas.shape = kind
        act = self.tool_acts["shape"]
        act.setIcon(tool_icon(kind))
        act.setText(label)
        act.setToolTip(f"{label} (R) — Shift: square / circle")
        self.set_tool("shape")

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
        m.addAction("Screen under mouse\tCtrl+Shift+2", self.full)
        m.addAction("All screens\tCtrl+Shift+3", self.all_screens)
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
            (3, MOD_CTRL | MOD_SHIFT, 0x33, self.all_screens),
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
        scr = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        self.edit(grab_screen(scr))

    def all_screens(self):
        self.edit(grab_desktop()[0])

    def edit(self, px):
        self.keep(Editor(self, px))

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
