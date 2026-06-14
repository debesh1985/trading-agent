import sys
import os
import logging
import json
from datetime import date, datetime

# Force UTF-8 on Windows stdout so emoji in log messages don't crash
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # Python < 3.9 fallback

from config.settings import Settings
from data.market_data import scan_all_tickers
from portfolio.holdings import HOLDINGS
from agent.claude_agent import TradingAgent
from alerts.notifications import AlertManager
from sentiment.sentiment_agent import SentimentAgent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.FileHandler("logs/trade_signals.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

STRATEGY_LABELS = {
    "put_credit_spread": "Put Credit Spread",
    "call_credit_spread": "Call Credit Spread",
    "iron_condor": "Iron Condor",
    "bull_call_spread": "Bull Call Spread",
    "bear_put_spread": "Bear Put Spread",
}

MAX_LOSS_USD = 145   # hard cap in USD (~$200 CAD at 1.38); read from settings at runtime
MIN_POP = 65

ET = ZoneInfo("America/New_York")


def is_market_open() -> bool:
    """True if NYSE is currently open: Mon–Fri 09:30–16:00 ET."""
    now = datetime.now(ET)
    if now.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now < market_close


def compute_payoff(signal: dict) -> dict:
    """
    Derives max_loss, max_profit, breakeven, and return_on_risk_pct from
    strikes and net premium using each strategy's payoff matrix.
    All values are in USD. Overrides Claude's self-reported numbers.
    Returns an empty dict if computation fails (missing keys, bad types).
    """
    strategy = signal.get("strategy", "")
    strikes = signal.get("strikes", {})
    net = float(signal.get("net_credit_or_debit", 0) or 0)

    try:
        if strategy == "put_credit_spread":
            sell, buy = float(strikes["sell"]), float(strikes["buy"])
            width = sell - buy
            if width <= 0:
                raise ValueError(f"put_credit_spread strikes misordered: sell={sell} buy={buy}")
            max_loss  = round((width - net) * 100, 2)
            if max_loss <= 0:
                raise ValueError("max_loss must be positive — credit may exceed spread width or strikes are misordered")
            max_profit = round(net * 100, 2)
            breakeven  = round(sell - net, 2)

        elif strategy == "call_credit_spread":
            sell, buy = float(strikes["sell"]), float(strikes["buy"])
            width = buy - sell
            if width <= 0:
                raise ValueError(f"call_credit_spread strikes misordered: sell={sell} buy={buy}")
            max_loss  = round((width - net) * 100, 2)
            if max_loss <= 0:
                raise ValueError("max_loss must be positive — credit may exceed spread width or strikes are misordered")
            max_profit = round(net * 100, 2)
            breakeven  = round(sell + net, 2)

        elif strategy == "iron_condor":
            ps = float(strikes.get("put_sell",  strikes.get("sell_put",  0)))
            pb = float(strikes.get("put_buy",   strikes.get("buy_put",   0)))
            cs = float(strikes.get("call_sell", strikes.get("sell_call", 0)))
            cb = float(strikes.get("call_buy",  strikes.get("buy_call",  0)))
            put_width  = ps - pb
            call_width = cb - cs
            if put_width <= 0 or call_width <= 0:
                raise ValueError(f"iron_condor wing misordered: put_width={put_width} call_width={call_width}")
            max_wing   = max(put_width, call_width)
            max_loss   = round((max_wing - net) * 100, 2)
            if max_loss <= 0:
                raise ValueError("max_loss must be positive — credit may exceed spread width or strikes are misordered")
            max_profit = round(net * 100, 2)
            breakeven  = f"${round(ps - net, 2)} / ${round(cs + net, 2)}"
            width = max_wing

        elif strategy == "bull_call_spread":
            buy, sell = float(strikes["buy"]), float(strikes["sell"])
            width = sell - buy
            if width <= 0:
                raise ValueError(f"bull_call_spread strikes misordered: buy={buy} sell={sell}")
            max_loss  = round(net * 100, 2)
            if max_loss <= 0:
                raise ValueError("max_loss must be positive — credit may exceed spread width or strikes are misordered")
            max_profit = round((width - net) * 100, 2)
            breakeven  = round(buy + net, 2)

        elif strategy == "bear_put_spread":
            buy, sell = float(strikes["buy"]), float(strikes["sell"])
            width = buy - sell
            if width <= 0:
                raise ValueError(f"bear_put_spread strikes misordered: buy={buy} sell={sell}")
            max_loss  = round(net * 100, 2)
            if max_loss <= 0:
                raise ValueError("max_loss must be positive — credit may exceed spread width or strikes are misordered")
            max_profit = round((width - net) * 100, 2)
            breakeven  = round(buy - net, 2)

        else:
            logger.warning(f"Unknown strategy '{strategy}' — skipping payoff computation")
            return {}

        ror = round(max_profit / max_loss * 100, 1) if max_loss > 0 else 0.0
        return {
            "max_loss": max_loss,
            "max_profit": max_profit,
            "breakeven": breakeven,
            "return_on_risk_pct": ror,
            "width": width,
        }

    except (KeyError, TypeError, ZeroDivisionError, ValueError) as e:
        logger.warning(f"Payoff computation failed for {signal.get('ticker')} ({strategy}): {e}")
        return {}


def validate_signals(signals: list, max_loss_usd: float) -> list:
    valid = []
    for s in signals:
        pop = s.get("probability_of_profit", 0)
        max_loss = s.get("max_loss", 9999)
        if pop < MIN_POP:
            logger.warning(
                f"EXCLUDED {s.get('ticker')} — PoP {pop}% is below {MIN_POP}% minimum"
            )
            continue
        if max_loss > max_loss_usd:
            logger.warning(
                f"EXCLUDED {s.get('ticker')} — max_loss ${max_loss} USD exceeds ${max_loss_usd} USD cap"
            )
            continue
        if s.get("earnings_warning") and s.get("strategy") not in ("iron_condor",):
            logger.warning(
                f"EXCLUDED {s.get('ticker')} — directional spread submitted "
                f"despite earnings_warning=True (strategy: {s.get('strategy')})"
            )
            continue
        valid.append(s)
    return valid


def print_scan_summary(analyzed: dict, skipped: dict):
    total = len(analyzed) + len(skipped)
    print()
    print("=" * 70)
    print(f"  SCAN SUMMARY — {total} tickers processed")
    print("=" * 70)
    print(f"\n  ANALYZED ({len(analyzed)}):")
    print("  " + "  ".join(f"{t:<6}" for t in analyzed.keys()))
    if skipped:
        print(f"\n  SKIPPED ({len(skipped)}):")
        for ticker, reason in skipped.items():
            print(f"    {ticker:<8}  {reason}")
    print()


def print_signals_table(signals: list, market_sentiment: dict):
    print("=" * 70)
    print(f"  MARKET SENTIMENT  : {market_sentiment.get('sentiment', 'neutral').upper()} "
          f"({market_sentiment.get('confidence', 'low')} confidence)")
    print(f"  {market_sentiment.get('summary', '')}")
    print()

    if not signals:
        print("  NO QUALIFYING SIGNALS TODAY")
        print(f"  No trades met the {MIN_POP}% PoP + ${MAX_LOSS_USD} USD max loss criteria.")
        print("=" * 70)
        print()
        return

    count = len(signals)
    print(f"  TOP {count} SIGNAL{'S' if count > 1 else ''}")
    print("=" * 70)

    col = [5, 7, 8, 20, 16, 12, 7, 8, 9, 20, 8, 5]
    headers = ["#", "Ticker", "Acct", "Strategy", "Strikes", "Expiry", "Width", "Credit", "MaxLoss", "Breakeven", "RoR%", "PoP%"]
    sep = "+" + "+".join("-" * (w + 2) for w in col) + "+"
    header_row = "|" + "|".join(f" {h:<{w}} " for h, w in zip(headers, col)) + "|"

    print(sep)
    print(header_row)
    print(sep)

    for s in signals:
        strikes = s.get("strikes", {})
        strikes_str = "/".join(f"${v}" for v in strikes.values())
        earn = "⚠" if s.get("earnings_warning") else ""
        strategy_label = STRATEGY_LABELS.get(s["strategy"], s["strategy"])[:19]
        direction = s.get("direction", "credit")
        net = s.get("net_credit_or_debit", 0) or 0
        credit_str = f"${net:.2f}{'cr' if direction == 'credit' else 'db'}"
        ror = s.get("return_on_risk_pct", 0) or 0
        be = s.get("breakeven", "—")
        be_str = str(be)[:19] if not isinstance(be, str) else be[:19]
        row_vals = [
            f"#{s['rank']}",
            f"{s['ticker']}{earn}",
            s.get("account_reference", "—")[:7],
            strategy_label,
            strikes_str[:15],
            s["expiry"],
            f"${s.get('width', '?')}",
            credit_str,
            f"${s['max_loss']}",
            be_str,
            f"{ror:.1f}%",
            f"{s['probability_of_profit']}%",
        ]
        print("|" + "|".join(f" {v:<{w}} " for v, w in zip(row_vals, col)) + "|")

    print(sep)
    print()
    print("  RATIONALE:")
    for s in signals:
        earn_note = " [⚠ EARNINGS]" if s.get("earnings_warning") else ""
        print(f"  #{s['rank']} {s['ticker']}{earn_note}: {s['rationale']}")
    print()


def log_to_supabase(settings: Settings, signals: list, scan_date: str):
    if not signals:
        return
    try:
        from supabase import create_client
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        inserted = 0
        for s in signals:
            # Dedup: skip if this exact signal was already logged today
            existing = (
                client.table("trade_signals")
                .select("id")
                .eq("scan_date", scan_date)
                .eq("ticker", s["ticker"])
                .eq("strategy", s["strategy"])
                .eq("expiry", s["expiry"])
                .execute()
            )
            if existing.data:
                logger.info(f"Duplicate signal skipped: {s['ticker']} {s['strategy']}")
                continue

            record = {
                "scan_date": scan_date,
                "rank": s["rank"],
                "ticker": s["ticker"],
                "account": s.get("account_reference"),
                "strategy": s["strategy"],
                "expiry": s["expiry"],
                "strikes": json.dumps(s.get("strikes", {})),
                "premium_credit": s.get("net_credit_or_debit"),
                "max_loss": s.get("max_loss"),
                "max_profit": s.get("max_profit"),
                "probability_of_profit": s.get("probability_of_profit"),
                "iv_rank": s.get("iv_rank"),
                "open_interest": s.get("open_interest"),
                "earnings_warning": s.get("earnings_warning", False),
                "rationale": s.get("rationale", ""),
            }
            client.table("trade_signals").insert(record).execute()
            inserted += 1

        if inserted:
            logger.info(f"Logged {inserted} new signal(s) to Supabase")
    except Exception as e:
        logger.error(f"Supabase logging failed: {e}")


def run_analysis(settings: Settings, agent: TradingAgent, alerts: AlertManager):
    tickers = [t.strip() for t in settings.TICKERS.split(",")]
    logger.info(f"Starting scan for {len(tickers)} tickers: {', '.join(tickers)}")

    # Step 0: Sentiment
    sentiment_agent = SentimentAgent(settings)
    logger.info("Fetching market sentiment...")
    market_sentiment = sentiment_agent.get_market_sentiment()
    logger.info(
        f"Market sentiment: {market_sentiment.get('sentiment', 'neutral').upper()} "
        f"({market_sentiment.get('confidence', 'low')} confidence) — {market_sentiment.get('summary', '')}"
    )
    logger.info("Fetching industry sentiment...")
    industry_sentiment = sentiment_agent.get_industry_sentiment(tickers)

    # Step 1: Scan market data
    scan_results, skipped = scan_all_tickers(tickers)
    print_scan_summary(scan_results, skipped)

    if not scan_results:
        logger.error("No tickers could be scanned. Aborting.")
        alerts.error_alert("No tickers returned valid data — scan aborted.")
        return

    # Step 2: Claude analysis
    logger.info(f"Sending {len(scan_results)} tickers to Claude for analysis...")
    try:
        raw_signals = agent.analyze(scan_results, HOLDINGS, market_sentiment, industry_sentiment)
    except Exception as e:
        logger.error(f"Claude analysis failed: {e}", exc_info=True)
        alerts.error_alert(f"Claude analysis error: {e}")
        return

    # Step 3: Compute payoff matrix client-side and override Claude's self-reported values
    for s in raw_signals:
        payoff = compute_payoff(s)
        if payoff:
            s.update(payoff)
        else:
            logger.warning(f"Payoff matrix unavailable for {s.get('ticker')} — using Claude's values")

    # Step 4: Validate hard filters against verified payoff values
    signals = validate_signals(raw_signals, settings.MAX_LOSS_USD)
    excluded = len(raw_signals) - len(signals)
    if excluded:
        logger.warning(f"{excluded} signal(s) excluded by client-side validation")

    # Step 5: Print console table
    print_signals_table(signals, market_sentiment)

    # Step 6: Supabase (with dedup)
    scan_date = date.today().isoformat()
    log_to_supabase(settings, signals, scan_date)

    # Step 7: Email
    alerts.send_trade_signals_email(signals, scan_results, skipped, scan_date, market_sentiment, industry_sentiment)
    logger.info("Analysis complete.")


def maybe_run_analysis(settings: Settings, agent: TradingAgent, alerts: AlertManager):
    """Wrapper that enforces the market-hours guard unless FORCE_RUN=true."""
    force = os.getenv("FORCE_RUN", "").lower() in ("1", "true", "yes")
    if not force and not is_market_open():
        now_et = datetime.now(ET).strftime("%H:%M ET")
        logger.info(f"Market closed ({now_et}) — skipping scan")
        return
    run_analysis(settings, agent, alerts)


def main():
    settings = Settings()
    if not settings.validate():
        logger.error("Missing required API keys. Check your .env file.")
        return

    agent = TradingAgent(settings)
    alerts = AlertManager(settings)

    logger.info("Trading agent started.")
    maybe_run_analysis(settings, agent, alerts)


if __name__ == "__main__":
    main()
