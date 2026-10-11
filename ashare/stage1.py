"""Deterministic daily analysis with conservative, fail-closed gates."""
import math
import re
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from .config import MODEL, PREFIXES, WATCHLIST, INDICES

TZ = ZoneInfo("Asia/Shanghai")


def iso_now():
    return datetime.now(TZ).isoformat(timespec="seconds")


def eligible_code(code):
    return bool(re.fullmatch(r"\d{6}", code)) and code.startswith(PREFIXES)


def expected_date(calendar, now):
    """No weekday-only inference. Need actual exchange sessions and forward coverage."""
    today = now.date().isoformat()
    try:
        for day in calendar:
            datetime.strptime(day, "%Y-%m-%d")
    except (TypeError, ValueError):
        return None
    if not calendar or calendar != sorted(set(calendar)) or max(calendar) <= today:
        return None
    cutoff = today if now.timetz().replace(tzinfo=None) >= time(16) else (now.date() - timedelta(days=1)).isoformat()
    previous = [d for d in calendar if d <= cutoff]
    return previous[-1] if previous else None


def validate_bars(bars, expected, calendar, now):
    errors = []
    if expected is None:
        errors.append("交易日历缺失、过期或无未来覆盖")
    if len(bars) < 60:
        errors.append("有效日线不足60根")
    dates = []
    previous = None
    for bar in bars:
        try:
            day = str(bar["date"])
            datetime.strptime(day, "%Y-%m-%d")
            dates.append(day)
            if day > now.date().isoformat() or day not in calendar:
                raise ValueError("invalid session")
            o, h, l, c, v = (float(bar[k]) for k in ("open", "high", "low", "close", "volume"))
            if not all(math.isfinite(x) for x in (o, h, l, c, v)) or min(o, h, l, c) <= 0 or v <= 0:
                raise ValueError("invalid numeric data")
            if not l <= min(o, c) <= max(o, c) <= h:
                raise ValueError("invalid OHLC")
            if previous and abs(c / previous - 1) > 0.12:
                errors.append("异常跳变/除权待核验")
            previous = c
        except (KeyError, ValueError, TypeError, OverflowError):
            errors.append("行情数值、日期或OHLC异常")
    if dates != sorted(set(dates)):
        errors.append("日期重复或未按升序排列")
    if not dates or dates[-1] != expected:
        errors.append("行情缺失或非最近完整交易日")
    if dates and expected:
        required = [d for d in calendar if dates[0] <= d <= expected]
        if dates != required:
            errors.append("历史交易日缺口/停牌待核验")
    return sorted(set(errors))


def indicators(bars):
    closes = [float(b["close"]) for b in bars]
    if len(closes) < 60:
        raise ValueError("need 60 bars")
    def ema(values, span):
        out = [values[0]]
        alpha = 2 / (span + 1)
        for value in values[1:]:
            out.append(value * alpha + out[-1] * (1 - alpha))
        return out
    e12, e26 = ema(closes, 12), ema(closes, 26)
    dif = [a - b for a, b in zip(e12, e26)]
    dea = ema(dif, 9)
    result = {f"ma{n}": sum(closes[-n:]) / n for n in (5, 10, 20, 30)}
    result.update(close=closes[-1], dif=dif[-1], dea=dea[-1], macd=2 * (dif[-1] - dea[-1]),
                  volume_ratio=float(bars[-1]["volume"]) / (sum(float(b["volume"]) for b in bars[-6:-1]) / 5),
                  change_pct=(closes[-1] / closes[-2] - 1) * 100)
    return {k: round(v, 6) for k, v in result.items()}


def buy_point(bars, ind):
    """MVP heuristics only; both patterns use completed bars."""
    last = bars[-1]
    trend = ind["ma5"] >= ind["ma10"] >= ind["ma20"] >= ind["ma30"]
    if trend and float(last["low"]) <= ind["ma10"] * 1.015 and ind["close"] >= ind["ma10"] and ind["volume_ratio"] <= 1:
        return "趋势回踩（候选）"
    # Breakout must be in prior 5 sessions against highs strictly before it.
    for offset in range(2, 7):
        ix = len(bars) - offset
        resistance = max(float(b["high"]) for b in bars[ix-20:ix])
        prior = bars[ix]
        if float(prior["close"]) > resistance * 1.005 and float(last["low"]) <= resistance * 1.02 and ind["close"] >= resistance and trend:
            return "突破回踩（候选）"
    return "无确认买点"


def fee(notional, sell=False, model=MODEL):
    return round(max(model.min_commission, notional * model.commission_rate) + notional * model.transfer_rate + (notional * model.stamp_tax_sell if sell else 0), 2)


def size_plan(entry, stop, cash=5000, positions=0, model=MODEL):
    """50 yuan planned loss includes both fees and adverse slippage; gaps can exceed it."""
    empty = {"shares": 0, "entry": entry, "stop": stop, "cost": 0, "planned_loss": 0}
    if not all(math.isfinite(x) and x > 0 for x in (entry, stop, cash)) or stop >= entry or positions >= model.max_positions:
        return empty
    buy = math.ceil(entry * (1 + model.slippage) * 100) / 100
    sell = math.floor(stop * (1 - model.slippage) * 100) / 100
    if sell <= 0:
        return empty
    budget = min(cash, model.budget_per_position)
    for shares in range(int(budget / (buy * 100)) * 100, 0, -100):
        cost = shares * buy + fee(shares * buy, model=model)
        loss = cost - (shares * sell - fee(shares * sell, True, model))
        if cost <= budget and loss <= model.risk_per_trade:
            return {"shares": shares, "entry": entry, "stop": stop, "estimated_buy": buy,
                    "estimated_stop_fill": sell, "cost": round(cost, 2), "planned_loss": round(loss, 2)}
    return empty


def analyze(raw, now=None):
    now = (now or datetime.now(TZ)).astimezone(TZ)
    try:
        age = (now - datetime.fromisoformat(raw["collected_at"])).total_seconds()
    except (KeyError, ValueError, TypeError):
        age = None
    stale_collection = age is None or age < -60 or age > 36 * 3600
    calendar = raw.get("calendar", [])
    expected = expected_date(calendar, now)
    index_rows = []
    for code, name in INDICES.items():
        bars = raw.get("indices", {}).get(code, [])
        problems = validate_bars(bars, expected, calendar, now)
        if stale_collection:
            problems.append("采集时间缺失、过期或异常")
        ind = indicators(bars) if not problems else None
        index_rows.append({"code": code, "name": name, "quality": problems, "indicators": ind,
                           "quote_date": bars[-1]["date"] if bars else None})
    all_good = all(not row["quality"] for row in index_rows)
    bullish = sum(bool(row["indicators"] and row["indicators"]["close"] > row["indicators"]["ma20"] and row["indicators"]["macd"] > 0) for row in index_rows)
    cycle = ("A" if bullish == 3 else "B" if bullish >= 1 else "C") if all_good else "UNKNOWN"
    stocks = []
    for code in WATCHLIST:
        bars = raw.get("stocks", {}).get(code, [])
        quality = validate_bars(bars, expected, calendar, now)
        if stale_collection:
            quality.append("采集时间缺失、过期或异常")
        meta = raw.get("metadata", {}).get(code, {})
        name = meta.get("name")
        if not name:
            quality.append("当期名称/ST/退市状态未核验")
        if name and any(x in name.upper() for x in ("ST", "退")):
            quality.append("风险警示/退市股票禁止开仓")
        if not eligible_code(code):
            quality.append("不在初始沪深主板范围")
        ind = indicators(bars) if not quality else None
        point = buy_point(bars, ind) if ind else "待数据核验"
        plan = size_plan(ind["close"], round(min(float(b["low"]) for b in bars[-5:]) * 0.99, 2)) if ind else None
        trend_score = (10 * (ind["close"] > ind["ma20"]) + 10 * (ind["ma5"] >= ind["ma10"] >= ind["ma20"]) + 5 * (ind["macd"] > 0)) if ind else None
        point_score = 20 if "候选" in point else 0 if ind else None
        fit_score = (15 if plan and plan["shares"] >= 100 else 0) if ind else None
        # Never manufacture sector or money-flow scores. Stage 1 has no permission.
        scores = {"industry": None, "funds": None, "trend": trend_score, "entry": point_score, "account": fit_score}
        blockers = list(quality)
        if not all_good:
            blockers.append("指数数据质量未通过")
        if cycle != "A":
            blockers.append("市场条件未通过")
        if "候选" not in point:
            blockers.append("买点未确认")
        if not plan or plan["shares"] == 0:
            blockers.append("100股/50元计划风险/2500元预算约束未通过")
        blockers += ["行业资金维度尚未验证", "第一阶段仅观察，买入许可锁定"]
        if raw.get("mode") != "live":
            blockers.append("合成演示数据禁止交易")
        stocks.append({"code": code, "name": name or "名称待核验", "quote_date": bars[-1]["date"] if bars else None,
                       "quality": quality, "indicators": ind, "scores": scores, "score": None,
                       "technical_subtotal": sum(v for v in scores.values() if v is not None) if ind else None,
                       "buy_point": point, "model_plan": plan, "buy_permission": False,
                       "blockers": sorted(set(blockers)), "chart": [b["close"] for b in bars[-30:]] if ind else []})
    deadline = now + timedelta(hours=36)
    if expected:
        next_days = [d for d in calendar if d > expected]
        if next_days:
            next_close = datetime.fromisoformat(next_days[0] + "T16:00:00+08:00")
            deadline = min(deadline, next_close)
    return {"schema_version": "1.0", "stage": 1, "version": "V3.1 · MVP", "generated_at": now.isoformat(timespec="seconds"),
            "collected_at": raw.get("collected_at"), "expected_quote_date": expected,
            "mode": raw.get("mode", "unavailable"), "source": raw.get("source", "未知"),
            "latency": "日线收盘数据，非实时；交易日16:00后核验完整性", "stale_collection": stale_collection,
            "valid_until": deadline.isoformat(timespec="seconds"),
            "market": {"cycle": cycle, "bullish_indices": bullish, "quality_pass": all_good and not stale_collection,
                       "description": {"A": "三指数趋势共振（实验规则）", "B": "指数分化，暂停新开仓", "C": "弱势防御，暂停新开仓", "UNKNOWN": "数据未通过，无法判断"}[cycle]},
            "indices": index_rows, "stocks": stocks,
            "sector": {"status": "pending", "description": "资金主线/热点阶段留待第二阶段验证，不以成交量代替资金流"},
            "model_constraints": {"capital": 5000, "positions_max": 2, "risk_per_trade": 50, "position_budget": 2500, "label": "策略模型假设，非真实账户"},
            "warnings": ["第一阶段所有新开仓许可关闭", "50元为含费用和滑点的计划损失，T+1、跳空及跌停可能导致损失超额", "实际账户与模拟交易记录仅保存在本地；公开JSON不含账户状态", "日线未复权；异常跳变/除权强制阻断，复权一致性将在后续阶段实现"],
            "interface_report": raw.get("interface_report", [])}
