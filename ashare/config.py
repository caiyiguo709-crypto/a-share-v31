from dataclasses import dataclass

WATCHLIST = ("601001", "000983", "600267", "002312", "002602", "002354", "000066")
INDICES = {"000001": "上证指数", "399001": "深证成指", "399006": "创业板指数"}
PREFIXES = ("600", "601", "603", "000", "001", "002")

@dataclass(frozen=True)
class ModelAccount:
    """Strategy example, never a user's actual account."""
    capital: float = 5000.0
    max_positions: int = 2
    risk_per_trade: float = 50.0
    budget_per_position: float = 2500.0
    commission_rate: float = 0.0003
    min_commission: float = 5.0
    stamp_tax_sell: float = 0.0005
    transfer_rate: float = 0.00001
    slippage: float = 0.001

MODEL = ModelAccount()
