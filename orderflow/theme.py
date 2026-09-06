"""
theme.py — what the terminal looks like: colours and the dark stylesheet.

Kept apart from the settings that reference these colours and from the drawing
code that paints with them, so "what colour is a buy?" has one obvious home.
"""

from PySide6 import QtGui, QtWidgets  # noqa: F401  (QtWidgets used by callers)
import pyqtgraph as pg

pg.setConfigOption("background", "#0b0d0e")
pg.setConfigOption("foreground", "#9aa0a6")
pg.setConfigOption("imageAxisOrder", "row-major")
pg.setConfigOptions(antialias=True)

# trade tape are QTableWidgets) follow the platform palette and came out white
# against them. One stylesheet on the main window covers every child.
DARK_QSS = """
QMainWindow, QDockWidget, QWidget { background: #0b0d0e; color: #d8dde2; }
QTableWidget, QTableView {
    background: #0b0d0e; alternate-background-color: #101416;
    color: #d8dde2; gridline-color: #1c2226;
    selection-background-color: #1d2b3a; selection-color: #ffffff;
    border: none;
}
QHeaderView::section {
    background: #12171a; color: #8b939b; border: 0;
    border-bottom: 1px solid #23282d; padding: 2px 4px;
}
QTableCornerButton::section { background: #12171a; border: 0; }
QToolBar { background: #0e1114; border: 0; spacing: 2px; }
QToolButton { color: #d8dde2; }
QToolButton:hover { background: #1b2126; border-radius: 3px; }
/* QAbstractSpinBox, not QSpinBox: the latter does not match QDoubleSpinBox, so
   half the numeric fields in the settings dialog were left unstyled. */
QComboBox, QAbstractSpinBox, QLineEdit {
    background: #12171a; color: #d8dde2;
    border: 1px solid #23282d; border-radius: 3px; padding: 1px 4px;
}
QPushButton {
    background: #1b2229; color: #d8dde2;
    border: 1px solid #2c343c; border-radius: 3px; padding: 4px 14px;
}
QPushButton:hover { background: #232c35; border-color: #3a444e; }
QPushButton:pressed { background: #161c22; }
QPushButton:default { border-color: #3f6ea8; }
QPushButton:disabled { color: #5f6b76; border-color: #23282d; }
QComboBox QAbstractItemView {
    background: #12171a; color: #d8dde2; selection-background-color: #1d2b3a;
}
QCheckBox, QLabel, QRadioButton { color: #d8dde2; }
QMenu { background: #12171a; color: #d8dde2; border: 1px solid #23282d; }
QMenu::item:selected { background: #1d2b3a; }
QScrollBar:vertical, QScrollBar:horizontal { background: #0b0d0e; border: 0; }
QScrollBar::handle { background: #262d33; border-radius: 4px; min-height: 18px; }
QScrollBar::handle:hover { background: #333c44; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QToolTip { background: #12171a; color: #d8dde2; border: 1px solid #2c343a; }
"""

BULL = QtGui.QColor(63, 226, 106)
BEAR = QtGui.QColor(255, 84, 84)
POC = QtGui.QColor(255, 224, 102)
GRID = QtGui.QColor(40, 46, 50)
TXT = QtGui.QColor(224, 224, 224)
DIM = QtGui.QColor(120, 126, 132)
IMB_BUY = QtGui.QColor(124, 255, 124)   # buy-imbalance edge marker
IMB_SELL = QtGui.QColor(255, 77, 77)    # sell-imbalance edge marker
IMB_RATIO = 3.0
IMB_MIN = 300                           # shares; ignore imbalances on tiny cells
# Quantower-style bid|ask footprint cell palette
CELL_BG = QtGui.QColor(44, 80, 132)      # volume-shaded blue cell
IMB_BUY_BG = QtGui.QColor(34, 150, 84)   # buy-imbalance cell fill (green)
IMB_SELL_BG = QtGui.QColor(150, 46, 46)  # sell-imbalance bid-half tint (red)
IMB_SELL_NUM = QtGui.QColor(245, 96, 96) # sell-imbalance number (red)
CELL_NUM = QtGui.QColor(205, 214, 226)   # default cell numbers


def side_colors(cfg):
    """Buy/sell palette for the footprint and the volume profile.

    Only the two base colours are configurable; the imbalance fill and edge
    shades are DERIVED from them. Hand-tuned constants would stay green while a
    user set the base to blue, which is exactly the incoherence this avoids.
    The ratios are chosen to land near the original palette at the defaults."""
    buy = QtGui.QColor(cfg.get("buy_color") or "#3fe26a")
    sell = QtGui.QColor(cfg.get("sell_color") or "#ff5454")
    if not buy.isValid():
        buy = QtGui.QColor(BULL)
    if not sell.isValid():
        sell = QtGui.QColor(BEAR)
    return {
        "buy": buy,
        "sell": sell,
        "buy_fill": buy.darker(155),      # imbalance cell body
        "sell_fill": sell.darker(175),
        "buy_edge": buy.lighter(145),     # imbalance marker / number
        "sell_edge": sell.lighter(115),
    }
CELL_DIV = QtGui.QColor(16, 26, 40)      # bid|ask divider
HDR_DIM = QtGui.QColor(150, 162, 178)    # per-bar V / R-H / R-L header text
ABSORB_SUP = QtGui.QColor(56, 230, 255)  # buy absorption at the low = support (cyan)
ABSORB_RES = QtGui.QColor(255, 159, 67)  # sell absorption at the high = resistance (orange)
