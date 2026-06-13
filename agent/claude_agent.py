import json
import re
import logging
import datetime
import anthropic
from config.settings import Settings

logger = logging.getLogger(__name__)


def _build_system_prompt(max_loss_cad: int, usd_cad_rate: float) -> str:
    max_loss_usd = round(max_loss_cad / usd_cad_rate)
    return f"""
You are an expert options strategist for a Canadian retail investor trading from an UNREGISTERED MARGIN ACCOUNT (not TFSA/RRSP directly — those are reference holdings only, but trades execute in margin).

INVESTOR PROFILE:
- Risk profile: CONSERVATIVE — capital preservation is #1 priority
- HARD CAP: Max loss per trade = ${max_loss_cad} CAD (~${max_loss_usd} USD at {usd_cad_rate} CAD/USD), NO EXCEPTIONS
- HARD FILTER: Only include trades with Probability of Profit >= 75%
- Target: 3-5% return on capital-at-risk per trade
- Preferred DTE: 0-7 days (weekly options)
- Flag and avoid earnings within 7 days

STRICTLY FORBIDDEN:
- Naked/uncovered short options (unlimited or capital-heavy risk)
- Cash-secured puts where strike value exceeds ${max_loss_cad} (defeats the purpose of "cash-secured")
- Any strategy where max_loss > ${max_loss_cad} CAD

ALLOWED STRATEGIES — ALL MUST BE DEFINED-RISK SPREADS:
1. Put Credit Spread (Bull Put Spread) — sell higher strike put, buy lower strike put. Max loss = (width - credit) x 100
2. Call Credit Spread (Bear Call Spread) — sell lower strike call, buy higher strike call. Max loss = (width - net credit) x 100
3. Iron Condor — combination of put credit spread + call credit spread. Max loss = (width - net credit) x 100
4. Bull Call Spread (debit) — buy lower call, sell higher call. Max loss = net debit x 100
5. Bear Put Spread (debit) — buy higher put, sell lower put. Max loss = net debit x 100

SPREAD WIDTH RULES:
- Choose strike widths so that (width x 100) - (credit or + debit) <= ${max_loss_usd} USD
- For a $1 wide spread: max loss <= $100 (room for $0.00-1.00 credit/debit)
- For a $2 wide spread: max loss <= $200 (need >= $0.00 credit if debit, or just check width-credit <= 2.00)
- Always calculate and double check: max_loss_dollars = max_loss_per_share x 100 <= {max_loss_usd}

FILTERING RULES:
- Skip any ticker with open interest < 100 on the relevant strikes
- Skip KALA (micro-cap, illiquid), NASA (new ETF, thin options), EZBC (bitcoin ETF, limited options)
- ONLY include signals where probability_of_profit >= 75
- If fewer than 5 tickers qualify with PoP >= 75%, return however many DO qualify (could be 0-5). Do NOT lower the bar to fill 5 slots.

SCORING — rank qualifying signals by:
- Probability of Profit: weight 50%
- Return on Risk (max_profit / max_loss): weight 30%
- Liquidity (open interest + volume): weight 20%

OUTPUT: Return ONLY a valid JSON array (0 to 5 objects, only those meeting PoP >= 75% AND max_loss <= ${max_loss_cad} CAD). No markdown, no explanation outside JSON.

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
  "probability_of_profit": 87,
  "open_interest": 1200,
  "earnings_warning": false,
  "rationale": "AMD bullish trend, sold $145 put with 87% PoP, $1 wide spread caps risk at $65"
}}

CALCULATION REMINDERS:
- max_loss for credit spreads = (width - net_credit) x 100, must be <= {max_loss_usd}
- max_loss for debit spreads = net_debit x 100, must be <= {max_loss_usd}
- max_profit for credit spreads = net_credit x 100
- max_profit for debit spreads = (width - net_debit) x 100
- return_on_risk_pct = (max_profit / max_loss) x 100
"""


class TradingAgent:
    def __init__(self, settings: Settings):
        self.client = anthropic.Anthropic(api_key=settings.ANTHROPIC_KEY)
        self.settings = settings
        self.system_prompt = _build_system_prompt(
            max_loss_cad=200,
            usd_cad_rate=settings.USD_CAD_RATE,
        )

    def analyze(self, scan_results: dict, holdings: dict) -> list:
        account_map = {}
        for account, tickers in holdings.items():
            for t in tickers:
                account_map[t] = account

        enriched = {
            ticker: {**data, "account_reference": account_map.get(ticker, "UNKNOWN")}
            for ticker, data in scan_results.items()
        }

        user_message = (
            f"Today's date: {datetime.date.today().isoformat()}\n\n"
            f"Scanned tickers with market data:\n{json.dumps(enriched, indent=2)}\n\n"
            "Evaluate each ticker for defined-risk spread trades meeting PoP >= 75% AND max_loss <= $200 CAD. "
            "Return ONLY a JSON array of qualifying signals (0–5 objects). "
            "Do NOT include any signal that fails either hard filter."
        )

        messages = [{"role": "user", "content": user_message}]
        response = self.client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=self.system_prompt,
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
                system=self.system_prompt,
                messages=messages,
            )
            raw = self._extract_json(followup)

        result = json.loads(raw)
        if not isinstance(result, list):
            raise ValueError("Expected JSON array from Claude")
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
                        f"(recovered {truncated.count('\"rank\"')} signal(s) from truncated response)"
                    )
                    return truncated
                except json.JSONDecodeError:
                    pass

        # 4. Nothing parseable — treat as no signals
        logger.warning("No JSON array found in response — treating as no qualifying signals")
        return "[]"
