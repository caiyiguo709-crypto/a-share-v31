"""V4.1 research watchlist. Never submits orders or grants buy permission."""
import json
import math
import fcntl
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from .stage1 import TZ, fee
from .storage import atomic_json, private_path

PREFIXES = ("600", "601", "603", "605", "000", "001", "002", "003")
MIN_FULL_UNIVERSE = 2500  # Conservative sanity floor; 2026-10-16 exchange lists held 3197 matching codes.
EARLY, READY, EXECUTION, REMOVED = "趋势早期发现", "交易准备", "交易执行", "淘汰"


def mainboard(code):
    return isinstance(code, str) and len(code) == 6 and code.isdigit() and code.startswith(PREFIXES)


def _date(value):
    return datetime.strptime(value, "%Y-%m-%d").date()


def _bars(rows, day):
    if len(rows) < 21:
        raise ValueError("不足21根已完成日线")
    dates = [_date(row["date"]) for row in rows]
    if dates != sorted(set(dates)) or dates[-1] != _date(day):
        raise ValueError("日线日期重复、乱序或末根非扫描日")
    for row in rows:
        if row["date"] > day:
            raise ValueError("存在未来日线")
        o, h, l, c, v = (float(row[k]) for k in ("open", "high", "low", "close", "volume"))
        if not all(math.isfinite(x) for x in (o, h, l, c, v)) or min(o, h, l, c, v) <= 0 or not l <= min(o, c) <= max(o, c) <= h:
            raise ValueError("日线数值或OHLC异常")


def _signals(rows, index_rows=None):
    close = [float(x["close"]) for x in rows]
    ma5 = sum(close[-5:]) / 5
    ma10 = sum(close[-10:]) / 10
    prior_ma5 = sum(close[-6:-1]) / 5
    prior_ma10 = sum(close[-11:-1]) / 10
    crossing = close[-1] > ma5 > ma10 and not (close[-2] > prior_ma5 > prior_ma10)
    breakout = close[-1] > max(float(x["high"]) for x in rows[-21:-1]) * 1.005
    relative = None
    if index_rows and len(index_rows) >= 6 and index_rows[-1]["date"] == rows[-1]["date"]:
        matched = {row["date"]: row for row in index_rows}
        if rows[-6]["date"] in matched:
            relative = close[-1] / close[-6] - float(index_rows[-1]["close"]) / float(matched[rows[-6]["date"]]["close"])
    signals = []
    if crossing:
        signals.append("均线初次转强")
    if breakout:
        signals.append("突破前20日高点")
    if relative is not None and relative > 0.01 and close[-1] > ma10:
        signals.append("5日相对强度领先指数")
    streak = 0
    for a, b in zip(reversed(close[:-1]), reversed(close[1:])):
        if b <= a:
            break
        streak += 1
    return signals, streak, ma10


def _plan(row, price, day, grade):
    """Trade readiness uses explicit as-of plan; no inferred stop or target."""
    evidence = row.get("trade_plan") or {}
    try:
        shares = evidence["shares"]
        stop, target = float(evidence["stop"]), float(evidence["target"])
        cash = float(evidence["available_cash"])
        positions = evidence["open_positions"]
        exposure = float(evidence["existing_exposure"])
        open_risk = float(evidence["open_planned_risk"])
        losses = evidence["consecutive_losses"]
        drawdown = float(evidence["drawdown_pct"])
        if evidence.get("asof") != day:
            raise ValueError
        if type(shares) is not int or shares < 100 or shares % 100 or not all(math.isfinite(x) for x in (stop, target)) or not 0 < stop < price < target:
            raise ValueError
        if type(positions) is not int or not 0 <= positions < 2 or not math.isfinite(cash) or not 0 < cash <= 5000:
            raise ValueError
        if type(losses) is not int or losses < 0 or not all(math.isfinite(x) and x >= 0 for x in (exposure, open_risk, drawdown)):
            raise ValueError
        entry = math.ceil(price * 1.001 * 100) / 100
        exit_stop = math.floor(stop * 0.999 * 100) / 100
        exit_target = math.floor(target * 0.999 * 100) / 100
        cost = shares * entry + fee(shares * entry)
        risk = cost - (shares * exit_stop - fee(shares * exit_stop, True))
        reward = shares * exit_target - fee(shares * exit_target, True) - cost
        paused = losses >= 3 or drawdown >= 5
        if paused and not (evidence.get("resume_authorized") is True and evidence.get("review_asof") == day):
            return {"pass": False, "reason": "连续亏损或回撤达到暂停条件，需当日复盘后明确恢复"}
        grade_limit = {"A": 4000, "B": 2000}.get(grade, 0)
        return {"shares": shares, "stop": stop, "target": target, "cost": round(cost, 2),
                "planned_risk": round(risk, 2), "net_reward": round(reward, 2),
                "net_ratio": round(reward / risk, 3) if risk > 0 else None,
                "pass": cost <= cash and exposure + cost <= grade_limit and 0 < risk <= 75
                        and open_risk + risk <= 150 and reward >= 2 * risk}
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return {"pass": False, "reason": "缺少当日整手数量、止损目标、现金持仓、组合风险或回撤记录"}


def scan(snapshot, previous=None):
    """One completed session. Input must contain an as-of universe and prices."""
    if snapshot.get("schema_version") != "v41-snapshot-1":
        raise ValueError("V4.1快照版本不符")
    day = snapshot["asof"]
    _date(day)
    captured = datetime.fromisoformat(snapshot["captured_at"])
    local = captured.astimezone(TZ) if captured.tzinfo else None
    if local is None or local.date().isoformat() != day or local.hour < 15:
        raise ValueError("逐日快照须在当日收盘后采集，不能补造过去的时点档案")
    universe = snapshot["universe"]
    if not universe or len(universe) != len(set(universe)) or any(not mainboard(c) for c in universe):
        raise ValueError("全主板代码清单无效")
    rows = snapshot["stocks"]
    missing = sorted(set(universe) - set(rows))
    if missing or set(rows) - set(universe):
        raise ValueError(f"扫描覆盖不完整：缺失{len(missing)}只，额外{len(set(rows)-set(universe))}只")
    full_coverage = (snapshot.get("universe_complete") is True and len(universe) >= MIN_FULL_UNIVERSE
                     and snapshot.get("universe_source") == "AKShare / 上交所与深交所证券清单")
    if previous and (previous["asof"] >= day or previous["schema_version"] != "v41-state-1"):
        raise ValueError("候选历史日期或版本无效")
    prior = previous["candidates"] if previous else {}
    index_rows = snapshot.get("benchmark_bars")
    if index_rows:
        _bars(index_rows, day)
    candidates = {}
    events = []
    for code in universe:
        row = rows[code]
        old = prior.get(code)
        try:
            _bars(row["bars"], day)
            if row.get("status") != "normal" or row.get("listed_sessions", 0) < 60 or not row.get("liquid", False):
                raise ValueError("证券状态、上市时长或流动性未通过")
            signals, streak, ma10 = _signals(row["bars"], index_rows)
            price = float(row["bars"][-1]["close"])
            if not old and not signals:
                continue
            if old and old["stage"] == REMOVED and not signals:
                candidates[code] = old
                continue
            first_date = old["first_date"] if old and old["stage"] != REMOVED else day
            first_price = old["first_price"] if old and old["stage"] != REMOVED else price
            first_signals = old["first_signals"] if old and old["stage"] != REMOVED else signals
            days = (old["tracking_days"] + 1) if old and old["stage"] != REMOVED else 1
            rise = round((price / first_price - 1) * 100, 3)
            reasons = []
            if price < ma10 * 0.97:
                stage = REMOVED
                reasons.append("跌破10日均线超过3%")
            elif streak >= 5 or rise >= 8:
                stage = EARLY
                reasons.append("连续上涨或距首次发现涨幅较大，禁止自动升级")
            elif days >= 2 and price >= ma10:
                stage = READY
            else:
                stage = EARLY
            market = snapshot.get("market", {})
            plan = _plan(row, price, day, market.get("grade"))
            gates = [full_coverage, market.get("verified") is True and market.get("asof") == day,
                     market.get("grade") in ("A", "B"),
                     row.get("p0_verified") is True and row.get("quality_asof") == day,
                     row.get("p1_verified") is True and row.get("quality_asof") == day,
                     row.get("entry_confirmed") is True and row.get("entry_asof") == day, plan["pass"]]
            if stage == READY and all(gates) and not reasons:
                stage = EXECUTION
            if stage != EXECUTION:
                reasons.extend([name for name, ok in zip(("全市场覆盖未核验", "市场评级未核验", "C级或未知市场禁止新开仓", "P0未通过", "P1未通过", "买点未确认", "仓位/组合风险/75元风险/净盈亏比2:1未通过"), gates) if not ok])
            candidate = {"code": code, "name": row.get("name", "名称待核验"), "stage": stage,
                         "first_date": first_date, "first_price": first_price, "first_signals": first_signals,
                         "tracking_days": days, "last_date": day, "last_price": price,
                         "rise_since_first_pct": rise, "current_signals": signals, "up_streak": streak,
                         "plan": plan, "reasons": reasons, "buy_permission": False}
        except (KeyError, ValueError, TypeError, OverflowError) as exc:
            if not old:
                continue
            invalid_security = "证券状态、上市时长或流动性" in str(exc)
            candidate = {**old, "stage": REMOVED if invalid_security else EARLY,
                         "last_date": day, "last_price": None,
                         "current_signals": [], "reasons": [str(exc)], "buy_permission": False}
        candidates[code] = candidate
        if not old or old["stage"] != candidate["stage"]:
            events.append({"date": day, "code": code, "from": old["stage"] if old else None,
                           "to": candidate["stage"], "reasons": candidate["reasons"]})
    for code, old in prior.items():
        if code not in rows:
            candidate = {**old, "stage": EARLY, "last_date": day, "last_price": None,
                         "current_signals": [], "reasons": ["不在当日主板清单，需核查退市或数据源遗漏"], "buy_permission": False}
            candidates[code] = candidate
            if old["stage"] != EARLY:
                events.append({"date": day, "code": code, "from": old["stage"], "to": EARLY,
                               "reasons": candidate["reasons"]})
    history = list(previous.get("history", [])) if previous else []
    history.extend({"date": day, "code": code, "stage": item["stage"], "price": item["last_price"],
                    "signals": item["current_signals"], "reasons": item["reasons"]}
                   for code, item in candidates.items() if item["last_date"] == day)
    return {"schema_version": "v41-state-1", "asof": day, "universe_count": len(universe),
            "universe_source": snapshot.get("universe_source"), "coverage_complete": full_coverage,
            "candidates": candidates, "events": events, "history": history, "buy_permission": False}


def scan_file(snapshot_path, state_path):
    path = private_path(state_path)
    snapshot = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(path.suffix + ".lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        previous = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        result = scan(snapshot, previous)
        atomic_json(path, result)
        return result


def replay(snapshots):
    """Replay immutable, daily as-of snapshots. Reports discovery only, never P&L."""
    if not snapshots:
        raise ValueError("回放快照为空")
    state = None
    all_events = []
    for snapshot in snapshots:
        state = scan(snapshot, state)
        all_events.extend(state["events"])
    state["replay_metrics"] = {"sessions": len(snapshots),
                               "first_discoveries": sum(event["from"] is None for event in all_events),
                               "stage_changes": sum(event["from"] is not None for event in all_events),
                               "execution_entries": sum(event["to"] == EXECUTION for event in all_events),
                               "pnl": None}
    return state


def collect_daily(timeout=18):
    """Fetch the provider's entire current main-board list and completed bars.

    This is a research feed only: one vendor cannot certify P0 or market grade.
    A partial provider run remains visibly incomplete and cannot reach execution.
    """
    from .provider import normalize, request_api

    now = datetime.now(TZ)
    if now.hour < 15:
        raise ValueError("全市场收盘扫描只能在北京时间15:00后启动")
    day = now.date().isoformat()
    sh, sh_report = request_api("stock_info_sh_name_code", {"symbol": "主板A股"}, timeout)
    sz, sz_report = request_api("stock_info_sz_name_code", {"symbol": "A股列表"}, timeout)
    if sh_report["status"] != "ok" or sz_report["status"] != "ok":
        raise ValueError("交易所主板清单获取失败：沪 " + str(sh_report.get("error")) + "；深 " + str(sz_report.get("error")))
    names = {str(r.get("证券代码")): str(r.get("证券简称", "")) for r in sh if mainboard(str(r.get("证券代码")))}
    names.update({str(r.get("A股代码")): str(r.get("A股简称", "")) for r in sz
                  if r.get("板块") == "主板" and mainboard(str(r.get("A股代码")))})
    if not names:
        raise ValueError("主板证券列表为空")
    codes = sorted(names)
    from datetime import timedelta
    start = (now.date() - timedelta(days=220)).strftime("%Y%m%d")
    end = now.strftime("%Y%m%d")
    index_records, index_report = request_api("stock_zh_index_daily_tx", {"symbol": "sh000001",
                                                     "start_date": start, "end_date": end}, timeout)
    benchmark = normalize(index_records, index_tx=True, volume_multiplier=100,
                          start=start[:4] + "-" + start[4:6] + "-" + start[6:], end=day)
    if index_report["status"] != "ok" or len(benchmark) < 21 or benchmark[-1].get("date") != day:
        benchmark = None

    def fetch(code):
        symbol = ("sh" if code.startswith("6") else "sz") + code
        records, item_report = request_api("stock_zh_a_hist_tx", {"symbol": symbol, "adjust": "",
                                                           "start_date": start, "end_date": end}, timeout)
        bars = normalize(records, start=start[:4] + "-" + start[4:6] + "-" + start[6:], end=day)
        return code, bars, item_report

    stocks = {}
    failures = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        for code, bars, item_report in pool.map(fetch, codes):
            if item_report["status"] != "ok" or not bars or bars[-1].get("date") != day:
                failures.append({"code": code, "error": item_report.get("error"),
                                 "status": item_report["status"], "last_date": bars[-1].get("date") if bars else None})
            name = names[code]
            stocks[code] = {"name": name, "bars": bars, "status": "normal" if name and "ST" not in name.upper() and "退" not in name else "blocked",
                            "listed_sessions": len(bars), "liquid": bool(bars and all(float(b.get("volume") or 0) > 0 for b in bars[-5:])),
                            "p0_verified": False, "p1_verified": False}
    finished = datetime.now(TZ)
    if finished.date().isoformat() != day:
        raise ValueError("全市场采集跨越交易日，拒绝保存为当日完整快照")
    return {"schema_version": "v41-snapshot-1", "asof": day, "captured_at": finished.isoformat(timespec="seconds"),
            "universe": codes, "universe_source": "AKShare / 上交所与深交所证券清单",
            "universe_complete": not failures, "stocks": stocks,
            "benchmark_bars": benchmark, "benchmark_status": "ok" if benchmark else "unavailable",
            "market": {"grade": "UNKNOWN", "verified": False, "asof": day},
            "collection_failures": failures, "research_only": True}
