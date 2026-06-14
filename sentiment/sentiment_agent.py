import json
import re as _re
import logging
import datetime
import yfinance as yf
import anthropic
from config.settings import Settings

logger = logging.getLogger(__name__)


def _parse_json_response(raw: str) -> dict:
    """Extract and parse the first JSON object from a Claude response.
    Handles plain JSON and markdown-fenced code blocks."""
    raw = raw.strip()
    fenced = _re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", raw)
    if fenced:
        raw = fenced.group(1).strip()
    match = _re.search(r"\{[\s\S]*\}", raw)
    if match:
        return json.loads(match.group(0))
    return json.loads(raw)  # last attempt, will raise if unparseable


_FALLBACK_MARKET = {
    "sentiment": "neutral",
    "confidence": "low",
    "key_factors": [],
    "summary": "Sentiment unavailable.",
}


class SentimentAgent:
    def __init__(self, settings: Settings):
        self.client = anthropic.Anthropic(api_key=settings.ANTHROPIC_KEY)

    def get_market_sentiment(self) -> dict:
        try:
            raw_news = []
            for sym in ("SPY", "QQQ", "^GSPC"):
                try:
                    raw_news.extend(yf.Ticker(sym).news or [])
                except Exception:
                    pass

            seen_titles: set = set()
            unique_news = []
            for item in raw_news:
                title = item.get("title", "")
                if title and title not in seen_titles:
                    seen_titles.add(title)
                    unique_news.append(item)

            unique_news.sort(key=lambda x: x.get("providerPublishTime", 0), reverse=True)
            unique_news = unique_news[:20]

            if not unique_news:
                return dict(_FALLBACK_MARKET)

            headline_lines = []
            for item in unique_news:
                title = item.get("title", "")
                source = item.get("publisher", "unknown")
                ts = item.get("providerPublishTime", 0)
                date_str = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else "unknown"
                headline_lines.append(f"- {title} ({source}, {date_str})")

            headlines = "\n".join(headline_lines)

            response = self.client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=512,
                system=(
                    "You are a macro market analyst. Classify overall US equity market sentiment "
                    "as bullish, neutral, or bearish based on the news below. "
                    "Focus on: FOMC decisions and Fed language, jobs/CPI/GDP data, geopolitical "
                    "events (wars, sanctions, trade disputes), credit events, and broad risk-off signals. "
                    "Return ONLY valid JSON:\n"
                    "{\n"
                    '  "sentiment": "bullish|neutral|bearish",\n'
                    '  "confidence": "high|medium|low",\n'
                    '  "key_factors": ["<factor 1>", "<factor 2>", "<factor 3>"],\n'
                    '  "summary": "<2-sentence plain-English summary>"\n'
                    "}"
                ),
                messages=[{"role": "user", "content": f"Recent market headlines:\n{headlines}"}],
            )

            raw = response.content[0].text.strip()
            result = _parse_json_response(raw)
            return result
        except Exception as e:
            logger.warning(f"Market sentiment fetch failed: {e}")
            return dict(_FALLBACK_MARKET)

    def get_industry_sentiment(self, tickers: list) -> dict:
        SECTOR_MAP = {
            "AMD": "Semiconductors", "NVDA": "Semiconductors", "AVGO": "Semiconductors", "DRAM": "Semiconductors",
            "TSLA": "EV & Auto", "GOOGL": "Big Tech", "VGT": "Technology ETF",
            "C": "Financials", "MS": "Financials",
            "WMT": "Retail", "VOOG": "Broad Market ETF", "MGK": "Broad Market ETF",
        }

        sector_to_tickers: dict = {}
        for ticker in tickers:
            sector = SECTOR_MAP.get(ticker, "General Equity")
            sector_to_tickers.setdefault(sector, []).append(ticker)

        sector_results: dict = {}
        for sector, sector_tickers in sector_to_tickers.items():
            try:
                raw_news = []
                for t in sector_tickers:
                    try:
                        raw_news.extend(yf.Ticker(t).news or [])
                    except Exception:
                        pass

                seen_titles: set = set()
                unique_news = []
                for item in raw_news:
                    title = item.get("title", "")
                    if title and title not in seen_titles:
                        seen_titles.add(title)
                        unique_news.append(item)

                unique_news.sort(key=lambda x: x.get("providerPublishTime", 0), reverse=True)
                unique_news = unique_news[:15]

                if not unique_news:
                    sector_results[sector] = {**_FALLBACK_MARKET, "sector": sector}
                    continue

                headline_lines = []
                for item in unique_news:
                    title = item.get("title", "")
                    source = item.get("publisher", "unknown")
                    ts = item.get("providerPublishTime", 0)
                    date_str = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else "unknown"
                    headline_lines.append(f"- {title} ({source}, {date_str})")

                headlines = "\n".join(headline_lines)

                response = self.client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=512,
                    system=(
                        f"You are a sector equity analyst. Classify sentiment for the {sector} sector "
                        "as bullish, neutral, or bearish based on recent news. "
                        "Focus on: earnings beats/misses vs analyst expectations, revenue/EPS guidance "
                        "vs consensus, analyst upgrades/downgrades, supply-chain or regulatory news, "
                        "and major product/competitive developments. "
                        "Return ONLY valid JSON:\n"
                        "{\n"
                        f'  "sector": "{sector}",\n'
                        '  "sentiment": "bullish|neutral|bearish",\n'
                        '  "confidence": "high|medium|low",\n'
                        '  "key_factors": ["<factor 1>", "<factor 2>", "<factor 3>"],\n'
                        '  "summary": "<2-sentence plain-English summary>"\n'
                        "}"
                    ),
                    messages=[{"role": "user", "content": f"Recent {sector} sector headlines:\n{headlines}"}],
                )

                raw = response.content[0].text.strip()
                result = _parse_json_response(raw)
                sector_results[sector] = result
            except Exception as e:
                logger.warning(f"Industry sentiment fetch failed for {sector}: {e}")
                sector_results[sector] = {**_FALLBACK_MARKET, "sector": sector}

        ticker_results: dict = {}
        for ticker in tickers:
            sector = SECTOR_MAP.get(ticker, "General Equity")
            ticker_results[ticker] = sector_results.get(sector, {**_FALLBACK_MARKET, "sector": sector})

        return ticker_results
