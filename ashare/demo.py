"""Synthetic fixtures; never represents historical or current market prices."""
import math
from datetime import datetime, timedelta
from .config import WATCHLIST, INDICES
from .engine import TZ, iso_now


def demo_raw():
    now = datetime.now(TZ)
    # Explicitly artificial calendar for exercising the UI, not a trading calendar.
    days = [(now.date() + timedelta(days=i)).isoformat() for i in range(-200, 20)
            if (now.date() + timedelta(days=i)).weekday() < 5]
    cutoff = now.date().isoformat() if now.hour >= 16 else (now.date() - timedelta(days=1)).isoformat()
    history = [d for d in days if d <= cutoff][-100:]
    def series(base, phase):
        rows = []
        for i, day in enumerate(history):
            close = round(base * (1 + 0.001 * i + 0.013 * math.sin(i / 5 + phase)), 2)
            opening = round(close * 0.997, 2)
            rows.append({"date": day, "open": opening, "close": close, "high": round(close * 1.012, 2),
                         "low": round(opening * 0.988, 2), "volume": 10000 + i * 13 + 1000 * (math.sin(i) + 1)})
        return rows
    return {"mode": "demo", "collected_at": iso_now(), "source": "本地合成演示数据 / 人工交易日历，非AKShare行情",
            "calendar": days, "indices": {c: series(b, i) for i, (c, b) in enumerate(zip(INDICES, (3000, 10000, 2000)))},
            "stocks": {c: series(b, i) for i, (c, b) in enumerate(zip(WATCHLIST, (8, 10, 9, 6, 4, 5, 7)))},
            "metadata": {c: {"name": "演示标的"} for c in WATCHLIST}, "interface_report": []}
