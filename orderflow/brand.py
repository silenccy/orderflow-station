"""
brand.py — the app's icon, and telling Windows to actually show it.

The icon is drawn in code rather than shipped as a .ico/.png: no binary asset in
the repo, and it is rendered crisp at every size Windows asks for instead of being
scaled from one bitmap. It is a tiny order book — asks over bids, sized like depth
bars — with a gold price line through the touch.
"""

import sys

from PySide6 import QtCore, QtGui

from .theme import BEAR, BULL, POC

APP_ID = "silenccy.orderflow-station"
SIZES = (16, 24, 32, 48, 64, 128, 256)


def _pixmap(px):
    """One size of the icon, drawn on a 100-unit canvas scaled to px."""
    pm = QtGui.QPixmap(px, px)
    pm.fill(QtCore.Qt.GlobalColor.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    p.scale(px / 100.0, px / 100.0)

    tile = QtCore.QRectF(4, 4, 92, 92)
    grad = QtGui.QLinearGradient(0, 4, 0, 96)
    grad.setColorAt(0.0, QtGui.QColor("#1c242c"))
    grad.setColorAt(1.0, QtGui.QColor("#0a0d10"))
    p.setPen(QtGui.QPen(QtGui.QColor("#2f3842"), 2.0) if px >= 32
             else QtCore.Qt.PenStyle.NoPen)
    p.setBrush(QtGui.QBrush(grad))
    p.drawRoundedRect(tile, 20, 20)

    # Small sizes get two rungs a side: at 16 px three would blur into a block.
    if px >= 32:
        asks = [(19, 46), (31, 62), (41, 34)]
        bids = [(55, 58), (65, 40), (77, 66)]
        h = 7.5
    else:
        asks = [(22, 50), (36, 64)]
        bids = [(56, 60), (70, 44)]
        h = 10.0
    p.setPen(QtCore.Qt.PenStyle.NoPen)
    for rungs, col in ((asks, BEAR), (bids, BULL)):
        c = QtGui.QColor(col)
        c.setAlpha(235)
        p.setBrush(c)
        for y, w in rungs:
            p.drawRoundedRect(QtCore.QRectF(16, y, w, h), 2, 2)

    gold = QtGui.QColor(POC)
    pen = QtGui.QPen(gold, 5.5 if px >= 32 else 7.0)
    pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.drawLine(QtCore.QPointF(14, 50), QtCore.QPointF(86, 50))
    p.setPen(QtCore.Qt.PenStyle.NoPen)
    p.setBrush(gold)
    p.drawEllipse(QtCore.QPointF(76, 50), 7, 7)
    p.end()
    return pm


def make_icon():
    """The application icon at every size Windows and Qt might ask for."""
    icon = QtGui.QIcon()
    for px in SIZES:
        icon.addPixmap(_pixmap(px))
    return icon


def set_app_id():
    """Give the process its own taskbar identity. Call before any window exists.

    Launched through pythonw.exe, Windows otherwise groups the window under
    Python and shows Python's icon on the taskbar, whatever the window says.
    Best effort by design: a failure here must never stop the app starting.
    Returns True if the identity was set."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        return ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID) == 0
    except Exception:
        return False
