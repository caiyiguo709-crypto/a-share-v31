"""V4.1 candidate transitions and fail-closed trade readiness."""
import json
from pathlib import Path

import pytest

from ashare.v41 import EARLY, READY, EXECUTION, REMOVED, _signals, replay, scan


def bar(day, price, high=None):
    return {"date": day, "open": price, "high": high or price + .02,
            "low": price - .02, "close": price, "volume": 100000}


def snapshot(day="2026-09-30", price=10.3):
    from datetime import date, timedelta
    base = date(2026, 8, 1)
    days = [(base + timedelta(days=i)).isoformat() for i in range(20)]
    bars = [bar(d, 10) for d in days] + [bar(day, price)]
    return {"schema_version": "v41-snapshot-1", "asof": day,
            "captured_at": day + "T16:30:00+08:00", "universe": ["601328"],
            "universe_complete": True, "universe_source": "test fixture",
            "stocks": {"601328": {"name": "测试股票", "bars": bars,
                                   "status": "normal", "listed_sessions": 100, "liquid": True}},
            "market": {"grade": "C", "verified": True, "asof": day}}


def full_universe(raw):
    codes = [f"600{i:03d}" for i in range(1000)] + [f"601{i:03d}" for i in range(1000)] + [f"603{i:03d}" for i in range(500)]
    flat = [bar(item["date"], 10) for item in raw["stocks"]["601328"]["bars"]]
    for code in codes:
        if code != "601328":
            raw["stocks"][code] = {"name": "测试股票", "bars": flat,
                                   "status": "normal", "listed_sessions": 100, "liquid": True}
    raw["universe"] = codes
    raw["universe_source"] = "AKShare / 上交所与深交所证券清单"
    return raw


def test_early_detection_and_c_market_never_executes():
    first = scan(snapshot())
    item = first["candidates"]["601328"]
    assert item["stage"] == EARLY
    assert item["first_date"] == "2026-09-30"
    assert "突破前20日高点" in item["first_signals"]
    assert item["buy_permission"] is False
    second_input = snapshot("2026-10-01", 10.4)
    second_input["stocks"]["601328"]["bars"][-2] = bar("2026-09-30", 10.3)
    second = scan(second_input, first)
    assert second["candidates"]["601328"]["stage"] == READY
    assert second["candidates"]["601328"]["tracking_days"] == 2
    assert second["candidates"]["601328"]["first_price"] == 10.3
    assert len(second["history"]) == 2
    assert any("C级" in reason for reason in second["candidates"]["601328"]["reasons"])


def test_execution_stage_needs_all_gates_and_still_no_buy_permission():
    first = scan(snapshot())
    later = full_universe(snapshot("2026-10-01", 10.4))
    later["stocks"]["601328"]["bars"][-2] = bar("2026-09-30", 10.3)
    later["market"] = {"grade": "A", "verified": True, "asof": "2026-10-01"}
    row = later["stocks"]["601328"]
    row.update(p0_verified=True, p1_verified=True, quality_asof="2026-10-01", entry_confirmed=True, entry_asof="2026-10-01",
               trade_plan={"asof": "2026-10-01", "shares": 100, "stop": 10.1, "target": 11.5,
                           "available_cash": 5000, "open_positions": 0, "existing_exposure": 0,
                           "open_planned_risk": 0, "consecutive_losses": 0, "drawdown_pct": 0})
    result = scan(later, first)
    assert result["candidates"]["601328"]["stage"] == EXECUTION
    assert result["candidates"]["601328"]["buy_permission"] is False
    later["market"]["grade"] = "C"
    assert scan(later, first)["candidates"]["601328"]["stage"] == READY
    later["market"]["grade"] = "A"
    later["universe_complete"] = False
    assert scan(later, first)["candidates"]["601328"]["stage"] == READY
    later["universe_complete"] = True
    later["market"]["grade"] = "B"
    row["trade_plan"]["existing_exposure"] = 1200
    assert scan(later, first)["candidates"]["601328"]["stage"] == READY
    row["trade_plan"]["existing_exposure"] = 0
    row["trade_plan"]["consecutive_losses"] = 3
    assert scan(later, first)["candidates"]["601328"]["stage"] == READY
    row["trade_plan"]["consecutive_losses"] = 0
    row["trade_plan"]["drawdown_pct"] = 5
    assert scan(later, first)["candidates"]["601328"]["stage"] == READY
    row["trade_plan"]["drawdown_pct"] = 0
    row["trade_plan"]["open_planned_risk"] = 120
    assert scan(later, first)["candidates"]["601328"]["stage"] == READY


def test_future_data_and_incomplete_universe_rejected():
    raw = snapshot()
    raw["stocks"]["601328"]["bars"].append(bar("2026-10-01", 10.4))
    assert scan(raw)["candidates"] == {}
    raw = snapshot()
    raw["universe"].append("600000")
    with pytest.raises(ValueError, match="覆盖不完整"):
        scan(raw)


def test_trend_break_removes_but_bad_feed_preserves_first_discovery():
    first = scan(snapshot())
    bad = snapshot("2026-10-01", 9.5)
    bad["stocks"]["601328"]["bars"] = []
    pending = scan(bad, first)["candidates"]["601328"]
    assert pending["stage"] == EARLY
    assert pending["first_date"] == "2026-09-30"
    assert pending["tracking_days"] == 1
    down = snapshot("2026-10-01", 9.5)
    down["stocks"]["601328"]["bars"][-2] = bar("2026-09-30", 10.3)
    removed = scan(down, first)["candidates"]["601328"]
    assert removed["stage"] == REMOVED
    assert "跌破10日均线超过3%" in removed["reasons"]


def test_up_streak_does_not_upgrade_to_execution():
    first = scan(snapshot())
    later = snapshot("2026-10-01", 10.6)
    for offset in range(1, 6):
        later["stocks"]["601328"]["bars"][-offset - 1]["close"] = 10.6 - offset * .1
        later["stocks"]["601328"]["bars"][-offset - 1]["high"] = 10.62 - offset * .1
        later["stocks"]["601328"]["bars"][-offset - 1]["open"] = 10.6 - offset * .1
        later["stocks"]["601328"]["bars"][-offset - 1]["low"] = 10.58 - offset * .1
    later["market"] = {"grade": "A", "verified": True, "asof": "2026-10-01"}
    row = later["stocks"]["601328"]
    row.update(p0_verified=True, p1_verified=True, quality_asof="2026-10-01", entry_confirmed=True, entry_asof="2026-10-01",
               trade_plan={"asof": "2026-10-01", "shares": 100, "stop": 10.3, "target": 11.5,
                           "available_cash": 5000, "open_positions": 0, "existing_exposure": 0,
                           "open_planned_risk": 0, "consecutive_losses": 0, "drawdown_pct": 0})
    item = scan(later, first)["candidates"]["601328"]
    assert item["stage"] == EARLY
    assert item["up_streak"] >= 5
    assert any("连续上涨" in reason for reason in item["reasons"])


def test_bank_of_communications_price_only_retrospective_uses_prefixes():
    data = json.loads((Path(__file__).parent / "data/v41-601328-retrospective.json").read_text())
    assert data["kind"] == "retrospective_price_only"
    rows = data["rows"]
    benchmark = data["benchmark_rows"]
    expected = {"2026-09-24": [], "2026-09-28": ["5日相对强度领先指数"],
                "2026-09-29": ["均线初次转强", "5日相对强度领先指数"],
                "2026-09-30": ["突破前20日高点", "5日相对强度领先指数"]}
    for day, signals in expected.items():
        prefix = [row for row in rows if row["date"] <= day]
        index_prefix = [row for row in benchmark if row["date"] <= day]
        assert prefix[-1]["date"] == day
        assert index_prefix[-1]["date"] == day
        assert _signals(prefix, index_prefix)[0] == signals


def test_replay_uses_ordered_asof_snapshots_only():
    first = snapshot()
    second = snapshot("2026-10-01", 10.4)
    second["stocks"]["601328"]["bars"][-2] = bar("2026-09-30", 10.3)
    result = replay([first, second])
    assert result["asof"] == "2026-10-01"
    assert result["candidates"]["601328"]["tracking_days"] == 2
    assert result["replay_metrics"] == {"sessions": 2, "first_discoveries": 1,
                                        "stage_changes": 1, "execution_entries": 0, "pnl": None}
    with pytest.raises(ValueError, match="日期或版本"):
        replay([second, first])
