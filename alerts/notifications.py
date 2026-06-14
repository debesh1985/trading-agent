import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from config.settings import Settings

logger = logging.getLogger(__name__)

STRATEGY_LABELS = {
    "put_credit_spread": "Put Credit Spread",
    "call_credit_spread": "Call Credit Spread",
    "iron_condor": "Iron Condor",
    "bull_call_spread": "Bull Call Spread",
    "bear_put_spread": "Bear Put Spread",
}

MAX_LOSS_USD = 145   # hard cap in USD; keep in sync with Settings.MAX_LOSS_USD
MIN_POP = 65


class AlertManager:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _send(self, subject: str, plain: str, html: str) -> bool:
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = self.settings.GMAIL_SENDER
            msg["To"] = self.settings.ALERT_RECIPIENT
            msg.attach(MIMEText(plain, "plain", "utf-8"))
            msg.attach(MIMEText(html, "html", "utf-8"))
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(self.settings.GMAIL_SENDER, self.settings.GMAIL_APP_PASSWORD)
                server.sendmail(
                    self.settings.GMAIL_SENDER,
                    self.settings.ALERT_RECIPIENT,
                    msg.as_string(),
                )
            logger.info(f"Alert sent: {subject}")
            return True
        except Exception as e:
            logger.error(f"Failed to send alert: {e}")
            return False

    def send_trade_signals_email(
        self,
        signals: list,
        analyzed: dict,
        skipped: dict,
        scan_date: str,
        market_sentiment: dict,
        industry_sentiment: dict,
    ) -> bool:
        count = len(signals)
        subject = (
            f"\U0001f3af Trading Signals — {count} Qualifying Trade{'s' if count != 1 else ''} "
            f"{scan_date} | Conservative Margin Account | Max Loss ${MAX_LOSS_USD} USD"
        )
        plain = self._build_plain(signals, analyzed, skipped, scan_date, market_sentiment, industry_sentiment)
        html = self._build_html(signals, analyzed, skipped, scan_date, market_sentiment, industry_sentiment)
        return self._send(subject, plain, html)

    def error_alert(self, error: str) -> bool:
        subject = "[Trading Agent] ERROR — Intervention Required"
        html = f"<p><b>Error:</b> {error}</p>"
        return self._send(subject, error, html)

    # ------------------------------------------------------------------ #
    #  Plain text fallback
    # ------------------------------------------------------------------ #
    def _build_plain(self, signals, analyzed, skipped, scan_date, market_sentiment, industry_sentiment):
        sentiment = market_sentiment.get("sentiment", "neutral").upper()
        confidence = market_sentiment.get("confidence", "low")
        summary = market_sentiment.get("summary", "")

        lines = [
            f"TRADING SIGNALS | {scan_date}",
            "=" * 60,
            "",
            "SENTIMENT OVERVIEW",
            f"Market  : {sentiment} ({confidence}) — {summary}",
            "",
            "SCAN SUMMARY",
            f"  Analyzed : {len(analyzed)} — {', '.join(analyzed.keys())}",
            f"  Skipped  : {len(skipped)}",
        ]
        for t, reason in skipped.items():
            lines.append(f"    {t:<8} — {reason}")

        lines += [""]

        if not signals:
            lines += [
                "NO QUALIFYING SIGNALS TODAY",
                f"No trades met the {MIN_POP}% PoP + ${MAX_LOSS_USD} USD max loss criteria.",
            ]
        else:
            lines += [f"TOP {len(signals)} SIGNAL(S)", "-" * 60]
            for s in signals:
                strikes_str = " / ".join(f"{k.title()} ${v}" for k, v in s.get("strikes", {}).items())
                direction = s.get("direction", "credit")
                net = s.get("net_credit_or_debit", 0) or 0
                ror = s.get("return_on_risk_pct", 0) or 0
                be = s.get("breakeven", "—")
                lines += [
                    f"#{s['rank']} {s['ticker']} ({s.get('account_reference','')}) "
                    f"— {STRATEGY_LABELS.get(s['strategy'], s['strategy'])}",
                    f"  Expiry   : {s['expiry']}   Width: ${s.get('width','?')}   "
                    f"Direction: {direction.upper()}",
                    f"  Strikes  : {strikes_str}",
                    f"  Net      : ${net:.2f} USD   Max Loss: ${s['max_loss']} USD   "
                    f"Max Profit: ${s.get('max_profit',0)} USD   RoR: {ror:.1f}%   PoP: {s['probability_of_profit']}%",
                    f"  Breakeven: {be}",
                    f"  {s['rationale']}",
                ]
                if s.get("earnings_warning"):
                    lines.append("  WARNING: Earnings within 7 days")
                lines.append("")

        lines += [
            "-" * 60,
            "RISK REMINDER",
            "AI-generated only. Always verify before trading.",
            f"Hard cap: max loss ${MAX_LOSS_USD} USD | Min PoP: {MIN_POP}% | Weekly expiry only.",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    #  HTML email
    # ------------------------------------------------------------------ #
    def _build_html(self, signals, analyzed, skipped, scan_date, market_sentiment, industry_sentiment):
        import html as _html

        sentiment_val = market_sentiment.get("sentiment", "neutral").lower()
        sentiment_label = sentiment_val.upper()
        confidence = market_sentiment.get("confidence", "low")
        summary = market_sentiment.get("summary", "")
        key_factors = market_sentiment.get("key_factors", [])

        sentiment_colors = {
            "bullish": ("bg", "#dcfce7", "txt", "#15803d"),
            "neutral": ("bg", "#f3f4f6", "txt", "#374151"),
            "bearish": ("bg", "#fee2e2", "txt", "#991b1b"),
        }
        _, bg_color, _, text_color = sentiment_colors.get(sentiment_val, sentiment_colors["neutral"])

        key_factors_html = "".join(f"<li>{_html.escape(f)}</li>" for f in key_factors)

        sentiment_banner = f"""
  <!-- Sentiment Banner -->
  <div style='background:{bg_color};padding:20px 32px;border:1px solid #e5e7eb;border-top:none'>
    <div style='margin-bottom:10px'>
      <span style='background:{text_color};color:white;padding:4px 14px;border-radius:6px;
                   font-weight:700;font-size:14px;margin-right:10px'>{sentiment_label}</span>
      <span style='background:white;color:#6b7280;padding:3px 10px;border-radius:10px;
                   font-size:12px;border:1px solid #e5e7eb'>{_html.escape(confidence)} confidence</span>
    </div>
    <p style='margin:0 0 8px;font-size:13px;color:{text_color}'>{_html.escape(summary)}</p>
    <ul style='margin:4px 0 0;padding-left:20px;font-size:12px;color:{text_color}'>
      {key_factors_html}
    </ul>
  </div>"""

        ok_badges = "".join(
            f"<span style='display:inline-block;margin:2px 3px;padding:2px 8px;"
            f"background:#d1fae5;color:#065f46;border-radius:4px;font-size:12px'>{t}</span>"
            for t in analyzed.keys()
        )
        skip_rows = "".join(
            f"<tr><td style='padding:4px 12px;color:#dc2626;font-weight:600'>{t}</td>"
            f"<td style='padding:4px 12px;color:#6b7280'>{r}</td></tr>"
            for t, r in skipped.items()
        )

        if not signals:
            signals_section = f"""
  <div style='background:white;padding:24px 32px;border:1px solid #e5e7eb;border-top:none'>
    <div style='background:#fef9c3;border:1px solid #fde047;border-radius:8px;padding:20px 24px;text-align:center'>
      <p style='margin:0;font-size:16px;font-weight:700;color:#854d0e'>No Qualifying Signals Today</p>
      <p style='margin:8px 0 0;font-size:13px;color:#92400e'>
        No trades met the <b>{MIN_POP}% PoP</b> + <b>${MAX_LOSS_USD} USD max loss</b> criteria.
        The market conditions don&rsquo;t currently offer setups that clear both hard filters.
      </p>
    </div>
  </div>"""
        else:
            signal_rows = ""
            for s in signals:
                strikes_str = " / ".join(
                    f"{k.title()} <b>${v}</b>" for k, v in s.get("strikes", {}).items()
                )
                earn_badge = (
                    "<span style='color:#b45309;font-weight:600'> &#9888; Earnings</span>"
                    if s.get("earnings_warning") else ""
                )
                pop = s["probability_of_profit"]
                pop_color = "#16a34a" if pop >= 80 else "#2563eb" if pop >= 65 else "#ca8a04"
                direction = s.get("direction", "credit")
                dir_color = "#16a34a" if direction == "credit" else "#7c3aed"
                net = s.get("net_credit_or_debit", 0) or 0
                ror = s.get("return_on_risk_pct", 0) or 0
                be = s.get("breakeven", "—")
                be_display = _html.escape(str(be))
                signal_rows += f"""
            <tr style='border-bottom:1px solid #e5e7eb'>
              <td style='padding:10px 12px;text-align:center;font-weight:700;font-size:16px'>{s['rank']}</td>
              <td style='padding:10px 12px;font-weight:700'>{s['ticker']}{earn_badge}</td>
              <td style='padding:10px 12px'>
                <span style='background:#ede9fe;color:#5b21b6;padding:2px 8px;border-radius:4px;font-size:12px'>
                  {s.get('account_reference','—')}
                </span>
              </td>
              <td style='padding:10px 12px;font-size:13px'>{STRATEGY_LABELS.get(s['strategy'], s['strategy'])}</td>
              <td style='padding:10px 12px;font-size:13px'>{strikes_str}</td>
              <td style='padding:10px 12px'>{s['expiry']}</td>
              <td style='padding:10px 12px;text-align:center;font-weight:600'>${s.get('width','?')}</td>
              <td style='padding:10px 12px;color:{dir_color};font-weight:600'>
                ${net:.2f}
                <span style='font-size:11px;font-weight:400'>{direction}</span>
              </td>
              <td style='padding:10px 12px;color:#dc2626;font-weight:600'>${s['max_loss']} USD</td>
              <td style='padding:10px 12px;color:#16a34a;font-weight:600'>${s.get('max_profit',0)} USD</td>
              <td style='padding:10px 12px;font-weight:600'>{ror:.1f}%</td>
              <td style='padding:10px 12px;font-weight:700;color:{pop_color}'>{pop}%</td>
              <td style='padding:10px 12px;font-weight:600;color:#1d4ed8'>{be_display}</td>
              <td style='padding:10px 12px;font-size:12px;color:#4b5563;max-width:220px'>{_html.escape(s.get("rationale",""))}</td>
            </tr>"""

            signals_section = f"""
  <div style='background:white;padding:24px 32px;border:1px solid #e5e7eb;border-top:none'>
    <h2 style='margin:0 0 16px;font-size:16px;color:#374151'>
      &#127942; {len(signals)} Qualifying Signal{'s' if len(signals) != 1 else ''}
      <span style='font-size:12px;font-weight:400;color:#6b7280;margin-left:8px'>
        PoP &ge; {MIN_POP}% &amp; Max Loss &le; ${MAX_LOSS_USD} USD
      </span>
    </h2>
    <div style='overflow-x:auto'>
    <table style='border-collapse:collapse;width:100%;font-size:13px;min-width:900px'>
      <thead>
        <tr style='background:#f3f4f6'>
          <th style='padding:10px 12px;text-align:left;color:#6b7280;font-weight:600'>#</th>
          <th style='padding:10px 12px;text-align:left;color:#6b7280;font-weight:600'>Ticker</th>
          <th style='padding:10px 12px;text-align:left;color:#6b7280;font-weight:600'>Acct Ref</th>
          <th style='padding:10px 12px;text-align:left;color:#6b7280;font-weight:600'>Strategy</th>
          <th style='padding:10px 12px;text-align:left;color:#6b7280;font-weight:600'>Strikes</th>
          <th style='padding:10px 12px;text-align:left;color:#6b7280;font-weight:600'>Expiry</th>
          <th style='padding:10px 12px;text-align:center;color:#6b7280;font-weight:600'>Width</th>
          <th style='padding:10px 12px;text-align:left;color:#6b7280;font-weight:600'>Credit/Debit</th>
          <th style='padding:10px 12px;text-align:left;color:#6b7280;font-weight:600'>Max Loss</th>
          <th style='padding:10px 12px;text-align:left;color:#6b7280;font-weight:600'>Max Profit</th>
          <th style='padding:10px 12px;text-align:left;color:#6b7280;font-weight:600'>RoR%</th>
          <th style='padding:10px 12px;text-align:left;color:#6b7280;font-weight:600'>PoP%</th>
          <th style='padding:10px 12px;text-align:left;color:#6b7280;font-weight:600'>Breakeven</th>
          <th style='padding:10px 12px;text-align:left;color:#6b7280;font-weight:600'>Rationale</th>
        </tr>
      </thead>
      <tbody>
        {signal_rows}
      </tbody>
    </table>
    </div>
  </div>"""

        return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style='font-family:Arial,sans-serif;background:#f9fafb;padding:24px;color:#111827'>

  <!-- Header -->
  <div style='background:linear-gradient(135deg,#1e3a5f,#2563eb);color:white;padding:24px 32px;border-radius:12px 12px 0 0'>
    <h1 style='margin:0;font-size:22px'>\U0001f3af Trading Signals &mdash; {scan_date}</h1>
    <p style='margin:6px 0 0;opacity:.85'>Conservative Margin Account &nbsp;|&nbsp;
      Hard cap: <b>${MAX_LOSS_USD} USD</b> max loss &nbsp;|&nbsp;
      Min PoP: <b>{MIN_POP}%</b>
    </p>
  </div>

  {sentiment_banner}

  <!-- Section 1: Scan Summary -->
  <div style='background:white;padding:24px 32px;border:1px solid #e5e7eb;border-top:none'>
    <h2 style='margin:0 0 16px;font-size:16px;color:#374151'>
      &#128269; Scan Summary &mdash; {len(analyzed) + len(skipped)} Tickers
    </h2>
    <p style='margin:0 0 6px;font-size:13px'><b style='color:#065f46'>Analyzed ({len(analyzed)})</b></p>
    <div style='margin-bottom:14px'>{ok_badges}</div>
    <p style='margin:0 0 6px;font-size:13px'><b style='color:#dc2626'>Skipped ({len(skipped)})</b></p>
    <table style='border-collapse:collapse;font-size:13px'>{skip_rows}</table>
  </div>

  <!-- Section 2: Signals or No-Signal Notice -->
  {signals_section}

  <!-- Section 3: Risk Footer -->
  <div style='background:#fef3c7;padding:16px 32px;border:1px solid #fde68a;border-top:none;border-radius:0 0 12px 12px'>
    <p style='margin:0;font-size:13px;color:#92400e'>
      <b>&#9888; Risk Reminder</b> &mdash;
      AI-generated suggestions only. Always verify strikes and liquidity before trading.
      Hard cap: <b>${MAX_LOSS_USD} USD</b> max loss per trade | Min PoP: <b>{MIN_POP}%</b> | Weekly expiry only.
      Breakeven values are computed from the payoff matrix and are in USD.
      Past signals do not guarantee future results.
    </p>
  </div>

</body>
</html>"""
