import json
import re
import logging
import datetime
import anthropic
from config.settings import Settings

logger = logging.getLogger(__name__)


def _build_system_prompt(max_loss_usd: float, market_sentiment: dict) -> str:
    mkt_sentiment = market_sentiment.get("sentiment", "neutral").upper()
    mkt_confidence = market_sentiment.get("confidence", "low")
    mkt_summary = market_sentiment.get("summary", "")
    key_factors = market_sentiment.get("key_factors", [])
    key_factors_str = "\n".join(f"  • {f}" for f in key_factors) if key_factors else "  • (none)"

    return f"""
You are an expert options strategist for a Canadian retail investor trading from an UNREGISTERED MARGIN ACCOUNT (not TFSA/RRSP directly — those are reference holdings only, but trades execute in margin).

INVESTOR PROFILE:
- Risk profile: CONSERVATIVE — capital preservation is #1 priority
- HARD CAP: Max loss per trade = ${max_loss_usd} USD, NO EXCEPTIONS. All monetary values are in USD.
- HARD FILTER: Only include trades with Probability of Profit >= 65%
- Target: 3-5% return on capital-at-risk per trade
- Preferred DTE: 0-7 days (weekly options)
- Flag and avoid earnings within 7 days

STRICTLY FORBIDDEN:
- Naked/uncovered short options (unlimited or capital-heavy risk)
- Any strategy where max_loss > ${max_loss_usd} USD

ALLOWED STRATEGIES — ALL MUST BE DEFINED-RISK SPREADS:
1. put_credit_spread — sell higher strike put, buy lower strike put
   strikes keys: "sell", "buy"
   max_loss = (sell - buy - net_credit) x 100
   breakeven = sell_strike - net_credit

2. call_credit_spread — sell lower strike call, buy higher strike call
   strikes keys: "sell", "buy"
   max_loss = (buy - sell - net_credit) x 100
   breakeven = sell_strike + net_credit

3. iron_condor — put credit spread + call credit spread
   strikes keys: "put_buy", "put_sell", "call_sell", "call_buy"
   max_loss = (max_wing_width - total_credit) x 100
   breakeven = ["put_sell - credit", "call_sell + credit"] (two values)

4. bull_call_spread — buy lower call, sell higher call (debit)
   strikes keys: "buy", "sell"
   max_loss = net_debit x 100
   breakeven = buy_strike + net_debit

5. bear_put_spread — buy higher put, sell lower put (debit)
   strikes keys: "buy", "sell"
   max_loss = net_debit x 100
   breakeven = buy_strike - net_debit

SPREAD WIDTH RULES:
- Choose strike widths so that max_loss (USD) <= ${max_loss_usd}
- Always calculate: max_loss = max_loss_per_share x 100 <= {max_loss_usd}

── SENTIMENT CONTEXT ──────────────────────────────────────
Market Sentiment : {mkt_sentiment}
                   ({mkt_confidence} confidence)
Key Macro Factors:
{key_factors_str}
Summary          : {mkt_summary}

Industry Sentiment is provided per-ticker in the market data below.

── STRATEGY SELECTION MATRIX ──────────────────────────────
Use the market + industry sentiment combination as the PRIMARY filter,
then use per-ticker signals (IV rank, price trend) as the SECONDARY
selector when multiple strategies are allowed.

PRIMARY — allowed strategies per sentiment cell:

Market \\ Industry │ Bullish                              │ Neutral                        │ Bearish
──────────────────┼──────────────────────────────────────┼────────────────────────────────┼──────────────────────────
Bullish           │ Bull Call Spread, Put Credit Spread  │ Put Credit Spread              │ Iron Condor
Neutral           │ Put Credit Spread, Bull Call Spread  │ ANY defined-risk spread*       │ Call Credit Spread, Bear Put Spread
Bearish           │ Iron Condor                          │ Call Credit Spread, Bear Put   │ Bear Put Spread, Call Credit Spread
                  │                                      │ Spread                         │
──────────────────────────────────────────────────────────────────────────────────────────────

*Neutral/Neutral: All five strategies are permitted. Use SECONDARY rules
 below to pick the most appropriate one per ticker.

SECONDARY — for Neutral/Neutral (or when multiple strategies are allowed):
- IV rank >= 50 → prefer credit strategies (Put Credit Spread,
  Call Credit Spread, Iron Condor) to sell elevated premium
- IV rank < 50  → prefer debit strategies (Bull Call Spread,
  Bear Put Spread) where premium is cheaper
- price_change_pct > +0.5% (upward momentum) → bias toward bullish
  strategies (Put Credit Spread, Bull Call Spread)
- price_change_pct < -0.5% (downward momentum) → bias toward bearish
  strategies (Call Credit Spread, Bear Put Spread)
- price_change_pct between -0.5% and +0.5% (flat) → Iron Condor
- earnings_warning = true → Iron Condor only, regardless of sentiment

IMPORTANT: Do NOT default to Iron Condor just because sentiment is
neutral. Use the secondary rules above to pick a directional strategy
where the data supports it. Iron Condor is only the right choice when
momentum is flat AND/OR earnings_warning is true.
───────────────────────────────────────────────────────────────────────

FILTERING RULES:
- Skip any ticker with open interest < 100 on the relevant strikes
- Skip KALA (micro-cap, illiquid), NASA (new ETF, thin options), EZBC (bitcoin ETF, limited options)
- ONLY include signals where probability_of_profit >= 65
- If fewer than 5 tickers qualify, return however many DO qualify (could be 0). Do NOT lower the bar to fill 5 slots.

SCORING — rank qualifying signals by:
- Probability of Profit: weight 50%
- Return on Risk (max_profit / max_loss): weight 30%
- Liquidity (open interest + volume): weight 20%

OUTPUT: Return ONLY a valid JSON array (0 to 5 objects). No markdown, no explanation outside JSON.
All monetary values (max_loss, max_profit, net_credit_or_debit, breakeven) are in USD.

Each object must contain:
{{
  "rank": 1,
  "ticker": "AMD",
  "account_reference": "TFSA",
  "strategy": "put_credit_spread",
  "expiry": "2026-06-20",
  "strikes": {{"sell": 145, "buy": 144}},
  "width": 1,
  "net_credit_or_debit": 0.35,
  "direction": "credit",
  "max_loss": 65,
  "max_profit": 35,
  "return_on_risk_pct": 53.8,
  "breakeven": 144.65,
  "probability_of_profit": 87,
  "open_interest": 1200,
  "earnings_warning": false,
  "rationale": "AMD bullish trend, sold $145/$144 put spread at $0.35 credit. Max loss $65 USD. Breakeven $144.65."
}}

For iron_condor, breakeven is a list: [lower_breakeven, upper_breakeven]

── STRIKE SELECTION (MANDATORY) ──────────────────────────
Each ticker's market data includes a "chain_slice" field with real
options chain data: actual strikes, bids, asks, and open interest.

YOU MUST select strikes ONLY from the strikes listed in chain_slice.
Do NOT invent or estimate strikes. If chain_slice is empty for a ticker,
skip that ticker entirely.

For credit spreads: choose the short strike from chain_slice where
  bid >= 0.05 AND oi >= 100. Choose the long strike one step away.
  Net credit = short_mid - long_mid  (mid = (bid+ask)/2 for each leg)

For debit spreads: choose the long strike near ATM, short strike one
  step OTM. Net debit = long_ask - short_bid.

For iron condors: choose put short strike below spot (bid >= 0.05, oi >= 100)
  and call short strike above spot (bid >= 0.05, oi >= 100), each with
  a protective long one step further OTM.

Use the actual mid-price ( (bid+ask)/2 ) for net_credit_or_debit.
Report open_interest from the chain_slice row matching your short strike.
──────────────────────────────────────────────────────────────

CALCULATION REMINDERS (all USD):
- put_credit_spread / call_credit_spread: max_loss = (width - net_credit) x 100
- iron_condor: max_loss = (max_wing_width - total_credit) x 100
- bull_call_spread / bear_put_spread: max_loss = net_debit x 100
- max_profit for credit = net_credit x 100
- max_profit for debit = (width - net_debit) x 100
- return_on_risk_pct = (max_profit / max_loss) x 100
"""


class TradingAgent:
    def __init__(self, settings: Settings):
        self.client = anthropic.Anthropic(api_key=settings.ANTHROPIC_KEY)
        self.settings = settings

    def analyze(
        self,
        scan_results: dict,
        holdings: dict,
        market_sentiment: dict,
        industry_sentiment: dict,
    ) -> list:
        REQUIRED_KEYS = {"rank", "ticker", "strategy", "expiry", "strikes", "max_loss", "probability_of_profit"}
        system_prompt = _build_system_prompt(
            max_loss_usd=self.settings.MAX_LOSS_USD,
            market_sentiment=market_sentiment,
        )

        account_map = {}
        for account, tickers in holdings.items():
            for t in tickers:
                account_map[t] = account

        enriched = {
            ticker: {
                **data,
                "account_reference": account_map.get(ticker, "UNKNOWN"),
                "industry_sentiment": industry_sentiment.get(ticker, {}).get("sentiment", "neutral"),
                "industry_sentiment_summary": industry_sentiment.get(ticker, {}).get("summary", ""),
            }
            for ticker, data in scan_results.items()
        }

        user_message = (
            f"Today's date: {datetime.date.today().isoformat()}\n\n"
            f"Scanned tickers with market data:\n{json.dumps(enriched, indent=2)}\n\n"
            f"Evaluate each ticker for defined-risk spread trades meeting PoP >= 65% AND max_loss <= ${self.settings.MAX_LOSS_USD} USD. "
            "Return ONLY a JSON array of qualifying signals (0–5 objects). "
            "Do NOT include any signal that fails either hard filter."
        )

        messages = [{"role": "user", "content": user_message}]
        response = self.client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=system_prompt,
            messages=messages,
        )

        raw = self._extract_json(response)

        # If no JSON array was found in the first response, ask Claude to output it explicitly
        if raw == "[]" and response.content and response.content[0].text.strip():
            logger.info("No JSON array in first response — sending follow-up to extract JSON")
            messages.append({"role": "assistant", "content": response.content[0].text})
            messages.append({
                "role": "user",
                "content": (
                    "Based on your analysis above, output ONLY the final JSON array result. "
                    "If no trades qualify, output exactly: []\n"
                    "If trades qualify, output the JSON array with no extra text."
                ),
            })
            followup = self.client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=4096,
                system=system_prompt,
                messages=messages,
            )
            raw = self._extract_json(followup)

        result = json.loads(raw)
        if not isinstance(result, list):
            raise ValueError("Expected JSON array from Claude")
        before_filter = len(result)
        result = [s for s in result if REQUIRED_KEYS.issubset(s.keys())]
        if len(result) < before_filter:
            dropped = before_filter - len(result)
            logger.warning(
                f"Schema validation dropped {dropped} signal(s) missing required keys: {REQUIRED_KEYS}"
            )
        logger.info(f"Claude returned {len(result)} qualifying signal(s)")
        return result[:5]

    def evaluate_position(self, position: dict, current_data: dict) -> dict:
        user_message = (
            f"Evaluate whether to hold or exit this open position:\n\n"
            f"Position: {json.dumps(position, indent=2)}\n\n"
            f"Current Market: {json.dumps(current_data, indent=2)}"
        )

        response = self.client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=256,
            system='You are an options risk manager. Respond in JSON: {"action": "hold|exit", "reason": "..."}',
            messages=[{"role": "user", "content": user_message}],
        )

        raw = self._extract_json(response)
        return json.loads(raw)

    def _extract_json(self, response) -> str:
        if not response.content:
            raise ValueError("Empty response from Claude")
        raw = response.content[0].text.strip()
        logger.debug(f"Claude raw response ({len(raw)} chars):\n{raw}")

        if not raw:
            logger.warning("Claude returned empty body — treating as no qualifying signals")
            return "[]"

        # 1. Prefer fenced code block
        fenced = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", raw)
        if fenced:
            candidate = fenced.group(1).strip()
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                pass  # fenced block present but incomplete — fall through to truncation recovery

        # 2. Extract the outermost [...] array (greedy — handles nested objects inside)
        match = re.search(r"(\[[\s\S]*\])", raw)
        if match:
            candidate = match.group(1).strip()
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                pass

        # 3. Truncation recovery — response was cut mid-JSON by token limit
        array_start = raw.find("[")
        if array_start != -1:
            partial = raw[array_start:]

            # 3a. Try simply appending "]" to close the array
            try:
                json.loads(partial + "]")
                logger.warning("Truncation recovery: appended ']' to close incomplete JSON array")
                return partial + "]"
            except json.JSONDecodeError:
                pass

            # 3b. Trim to the last complete object "}" then close the array
            last_brace = partial.rfind("}")
            if last_brace != -1:
                truncated = partial[: last_brace + 1] + "]"
                try:
                    json.loads(truncated)
                    logger.warning(
                        f"Truncation recovery: trimmed to last complete object "
                        f"(recovered {truncated.count(chr(34) + 'rank' + chr(34))} signal(s) from truncated response)"
                    )
                    return truncated
                except json.JSONDecodeError:
                    pass

        # 4. Nothing parseable — treat as no signals
        logger.warning("No JSON array found in response — treating as no qualifying signals")
        return "[]"
