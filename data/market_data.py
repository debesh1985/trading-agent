import yfinance as yf
import pandas as pd
import numpy as np
import logging
from datetime import datetime, timedelta, date

logger = logging.getLogger(__name__)

# Pre-defined skips — no yfinance call needed
HARDCODED_SKIPS = {
    "KALA": "micro-cap, illiquid options",
    "NASA": "new ETF, thin options market",
    "EZBC": "bitcoin ETF, no standard options chain",
    "MGK": "OI=11, permanently illiquid",
}

MIN_OPEN_INTEREST = 100
MAX_DTE = 7


class MarketData:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.ticker = yf.Ticker(symbol)

    def get_current_price(self) -> float:
        return float(self.ticker.fast_info.last_price)

    def get_weekly_expiry(self) -> str | None:
        today = date.today()
        cutoff = today + timedelta(days=MAX_DTE)
        try:
            expirations = self.ticker.options
        except Exception:
            return None
        for exp in expirations:
            exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
            if today <= exp_date <= cutoff:
                return exp
        return None

    def compute_iv_rank(self, current_iv: float) -> float:
        # Ranks the most recent 30-day realized volatility against its 1-year range as an
        # IV proxy, since yfinance does not expose historical implied volatility directly.
        # This is an approximation pending a dedicated IV history data source.
        try:
            hist = self.ticker.history(period="1y")
            if hist.empty or len(hist) < 31:
                return 50.0
            returns = hist["Close"].pct_change().dropna()
            rolling_rv = returns.rolling(30).std() * (252 ** 0.5)
            rolling_rv = rolling_rv.dropna()
            if len(rolling_rv) < 2:
                return 50.0
            rv_min = float(rolling_rv.min())
            rv_max = float(rolling_rv.max())
            if rv_max <= rv_min:
                return 50.0
            current_rv = float(rolling_rv.iloc[-1])
            rank = (current_rv - rv_min) / (rv_max - rv_min) * 100
            return round(float(np.clip(rank, 0.0, 100.0)), 1)
        except Exception:
            return 50.0

    def check_earnings_warning(self) -> bool:
        try:
            cal = self.ticker.calendar
            if cal is None:
                return False
            today = date.today()
            cutoff = today + timedelta(days=MAX_DTE)

            # yfinance v0.2+ returns a dict; older versions return a DataFrame
            if isinstance(cal, dict):
                raw_dates = cal.get("Earnings Date", [])
                if not isinstance(raw_dates, list):
                    raw_dates = [raw_dates]
                for d in raw_dates:
                    if d is None:
                        continue
                    if hasattr(d, "date"):
                        d = d.date()
                    elif isinstance(d, str):
                        try:
                            d = datetime.strptime(d[:10], "%Y-%m-%d").date()
                        except ValueError:
                            continue
                    if today <= d <= cutoff:
                        return True
            elif isinstance(cal, pd.DataFrame):
                for col in ["Earnings Date", "Earnings High", "Earnings Low"]:
                    if col in cal.columns:
                        for d in cal[col]:
                            if pd.isna(d):
                                continue
                            if hasattr(d, "date"):
                                d = d.date()
                            if today <= d <= cutoff:
                                return True
                        break
            return False
        except Exception:
            return False

    def scan(self) -> dict:
        spot = self.get_current_price()

        weekly_expiry = self.get_weekly_expiry()
        if not weekly_expiry:
            raise ValueError("No weekly options expiry found within 7 days")

        calls, puts = self._get_chain(weekly_expiry)

        atm_call = self._atm_row(calls, spot)
        atm_put = self._atm_row(puts, spot)

        call_iv = self._safe_float(atm_call, "impliedVolatility")
        put_iv = self._safe_float(atm_put, "impliedVolatility")
        current_iv = (call_iv + put_iv) / 2 if (call_iv and put_iv) else (call_iv or put_iv)

        call_oi = self._safe_int(atm_call, "openInterest")
        put_oi = self._safe_int(atm_put, "openInterest")
        max_oi = max(call_oi, put_oi)

        if max_oi < MIN_OPEN_INTEREST:
            raise ValueError(f"Open interest too low ({max_oi} < {MIN_OPEN_INTEREST})")

        hist_1y = self.ticker.history(period="1y")
        high_52w = float(hist_1y["High"].max()) if not hist_1y.empty else spot
        low_52w = float(hist_1y["Low"].min()) if not hist_1y.empty else spot

        hist_5d = self.ticker.history(period="5d")
        prev_close = float(hist_5d["Close"].iloc[-2]) if len(hist_5d) >= 2 else spot
        price_change_pct = round((spot - prev_close) / prev_close * 100, 2)

        iv_rank = self.compute_iv_rank(current_iv)
        earnings_warning = self.check_earnings_warning()

        return {
            "symbol": self.symbol,
            "price": round(spot, 2),
            "high_52w": round(high_52w, 2),
            "low_52w": round(low_52w, 2),
            "price_change_pct": price_change_pct,
            "weekly_expiry": weekly_expiry,
            "call_iv": round(call_iv, 4),
            "put_iv": round(put_iv, 4),
            "current_iv": round(current_iv, 4),
            "iv_rank": iv_rank,
            "open_interest": max_oi,
            "earnings_warning": earnings_warning,
        }

    def _get_chain(self, expiry: str):
        chain = self.ticker.option_chain(expiry)
        return chain.calls, chain.puts

    def _atm_row(self, df: pd.DataFrame, spot: float) -> pd.Series | None:
        if df is None or df.empty:
            return None
        idx = (df["strike"] - spot).abs().idxmin()
        return df.loc[idx]

    def _safe_float(self, row, col: str) -> float:
        try:
            if row is None:
                return 0.0
            val = row[col]
            return float(val) if not pd.isna(val) else 0.0
        except Exception:
            return 0.0

    def _safe_int(self, row, col: str) -> int:
        try:
            if row is None:
                return 0
            val = row[col]
            return int(val) if not pd.isna(val) else 0
        except Exception:
            return 0


def scan_all_tickers(tickers: list) -> tuple:
    """
    Returns (results: dict[ticker, scan_data], skipped: dict[ticker, reason])
    """
    results = {}
    skipped = {}

    for ticker in tickers:
        if ticker in HARDCODED_SKIPS:
            reason = HARDCODED_SKIPS[ticker]
            skipped[ticker] = reason
            logger.info(f"Pre-skipping {ticker}: {reason}")
            continue
        try:
            md = MarketData(ticker)
            data = md.scan()
            results[ticker] = data
            logger.info(
                f"Scanned {ticker}: ${data['price']} | IV rank={data['iv_rank']} "
                f"| OI={data['open_interest']} | expiry={data['weekly_expiry']}"
            )
        except Exception as e:
            skipped[ticker] = str(e)
            logger.warning(f"Skipped {ticker}: {e}")

    return results, skipped
