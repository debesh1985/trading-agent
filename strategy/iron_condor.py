from dataclasses import dataclass


@dataclass
class IronCondorLegs:
    symbol: str
    expiry: str
    sell_put: float
    buy_put: float
    sell_call: float
    buy_call: float
    net_credit: float
    max_loss: float
    breakeven_low: float
    breakeven_high: float


class IronCondor:
    """
    Sell OTM put spread + sell OTM call spread.
    Profits when underlying stays within a range (low IV environments).
    """

    def __init__(self, symbol: str, spot: float, iv: float):
        self.symbol = symbol
        self.spot = spot
        self.iv = iv

    def build(self, expiry: str, width: float = 5.0, otm_pct: float = 0.05) -> IronCondorLegs:
        sell_put = round(self.spot * (1 - otm_pct), 0)
        buy_put = sell_put - width
        sell_call = round(self.spot * (1 + otm_pct), 0)
        buy_call = sell_call + width

        # Estimated credit using IV approximation (placeholder — replace with real chain data)
        estimated_credit = round(self.iv * self.spot * 0.01, 2)
        max_loss = round(width - estimated_credit, 2)

        return IronCondorLegs(
            symbol=self.symbol,
            expiry=expiry,
            sell_put=sell_put,
            buy_put=buy_put,
            sell_call=sell_call,
            buy_call=buy_call,
            net_credit=estimated_credit,
            max_loss=max_loss,
            breakeven_low=sell_put - estimated_credit,
            breakeven_high=sell_call + estimated_credit,
        )

    def to_dict(self, legs: IronCondorLegs) -> dict:
        return {
            "strategy": "iron_condor",
            **legs.__dict__,
        }
