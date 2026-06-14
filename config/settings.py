import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    ANTHROPIC_KEY: str = os.getenv("ANTHROPIC_KEY", "")
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
    SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "")

    GMAIL_SENDER: str = os.getenv("GMAIL_SENDER", "")
    GMAIL_APP_PASSWORD: str = os.getenv("GMAIL_APP_PASSWORD", "")
    ALERT_RECIPIENT: str = os.getenv("ALERT_RECIPIENT", "")

    TICKERS: str = os.getenv("TICKERS", "SPY")
    TRADING_SYMBOL: str = os.getenv("TRADING_SYMBOL", "SPY")
    RISK_PER_TRADE: float = float(os.getenv("RISK_PER_TRADE", "0.02"))
    MAX_OPEN_POSITIONS: int = int(os.getenv("MAX_OPEN_POSITIONS", "5"))
    USD_CAD_RATE: float = float(os.getenv("USD_CAD_RATE", "1.38"))
    MAX_LOSS_USD: float = float(os.getenv("MAX_LOSS_USD", "145.0"))  # ~$200 CAD at 1.38
    MIN_POP: int = int(os.getenv("MIN_POP", "65"))

    LOG_DIR: str = "logs"
    LOG_FILE: str = "logs/trade_signals.log"

    def validate(self) -> bool:
        required = [self.ANTHROPIC_KEY, self.SUPABASE_URL, self.SUPABASE_KEY]
        return all(required)
