from dataclasses import dataclass


@dataclass
class StrangleLegs:
    symbol: str
    expiry: str
    buy_put: float
    buy_call: float
    net_debit: float
    breakeven_low: float
    breakeven_high: float


class Strangle:
    """
    Buy OTM call + buy OTM put at same expiry.
    Profits on a large move in either direction (high IV events).
    """

    def __init__(self, symbol: str, spot: float, iv: float):
        self.symbol = symbol
        self.spot = spot
        self.iv = iv

    def build(self, expiry: str, otm_pct: float = 0.05) -> StrangleLegs:
        buy_put = round(self.spot * (1 - otm_pct), 0)
        buy_call = round(self.spot * (1 + otm_pct), 0)

        estimated_debit = round(self.iv * self.spot * 0.015, 2)

        return StrangleLegs(
            symbol=self.symbol,
            expiry=expiry,
            buy_put=buy_put,
            buy_call=buy_call,
            net_debit=estimated_debit,
            breakeven_low=buy_put - estimated_debit,
            breakeven_high=buy_call + estimated_debit,
        )

    def to_dict(self, legs: StrangleLegs) -> dict:
        return {
            "strategy": "strangle",
            **legs.__dict__,
        }
