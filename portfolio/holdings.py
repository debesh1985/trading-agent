HOLDINGS = {
    "TFSA": ["AMD", "TSLA", "DRAM", "NASA", "EZBC", "KALA"],
    "RRSP": ["C", "WMT", "VGT", "GOOGL", "MS", "AVGO"],
    "BOTH": ["NVDA", "VOOG", "MGK"],
}


def get_account(ticker: str) -> str:
    for account, tickers in HOLDINGS.items():
        if ticker in tickers:
            return account
    return "UNKNOWN"
