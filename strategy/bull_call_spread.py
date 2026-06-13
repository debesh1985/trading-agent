from dataclasses import dataclass


@dataclass
class BullCallSpreadLegs:
    symbol: str
    expiry: str
    buy_call: float
    sell_call: float
    net_debit: float
    max_profit: float
    breakeven: float


class BullCallSpread:
    """
    Buy lower-strike call + sell higher-strike call.
    Profits on a moderate bullish move with capped risk.
    """

    def __init__(self, symbol: str, spot: float, iv: float):
        self.symbol = symbol
        self.spot = spot
        self.iv = iv

    def build(self, expiry: str, width: float = 5.0, atm_offset: float = 0.0) -> BullCallSpreadLegs:
        buy_call = round(self.spot + atm_offset, 0)
        sell_call = buy_call + width

        estimated_debit = round(self.iv * self.spot * 0.012, 2)
        max_profit = round(width - estimated_debit, 2)

        return BullCallSpreadLegs(
            symbol=self.symbol,
            expiry=expiry,
            buy_call=buy_call,
            sell_call=sell_call,
            net_debit=estimated_debit,
            max_profit=max_profit,
            breakeven=buy_call + estimated_debit,
        )

    def to_dict(self, legs: BullCallSpreadLegs) -> dict:
        return {
            "strategy": "bull_call_spread",
            **legs.__dict__,
        }
