"""
orderflow — an IDX (Indonesia Stock Exchange) orderflow trading terminal.

Layered so each piece is usable on its own:

    paths     every file location (override the archive with $ORDERFLOW_DATA)
    feed      websocket protocol, frame parsing, CSV persistence, live + replay
    model     pure aggregation, no Qt: footprint, CVD, volume profile, book,
              heatmap columns, regime filter
    app       PySide6/pyqtgraph GUI: footprint, liquidity heatmap, DOM, tape
    backtest  walk-forward evaluation of the regime filter on captured days
    capture   headless capture daemon (no GUI)
    token     Playwright session-token grabber

Typical use:
    python -m orderflow.app --replay --symbol ASII
    python -m orderflow.capture ASII
    python -m orderflow.backtest --symbol ASII
"""

__version__ = "1.0.0"
__all__ = ["paths", "feed", "model", "app", "backtest", "capture", "token"]
