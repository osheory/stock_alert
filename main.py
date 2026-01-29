import argparse
import yfinance as yf
import schedule
import time
import json
import os
from datetime import datetime
import pandas as pd
from ib_client import IBClient
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Configuration
STOCKS_FILE = "stocks.json"
# brevo_apikey removed for security (Use Environment Variable BREVO_APIKEY)

class AlertSystem:
    @staticmethod
    def send_alert(message):
        print(f"\n[ALERT] {message}")

    @staticmethod
    def send_email(subject, body, is_html=False):
        # 1. Try Environment Variable (Best for GitHub Actions/Secrets)
        api_key = os.environ.get('BREVO_APIKEY')
        
        # 2. Fallback to global variable (Local dev)
        if not api_key:
            api_key = globals().get('brevo_apikey') 
            
        recipient = "osheory@gmail.com"
        sender_email = "osheory@gmail.com" # Must be a verified sender in Brevo
        
        if not api_key:
            print("\n[Email] Skipping: 'brevo_apikey' not defined in main.py")
            return

        url = "https://api.brevo.com/v3/smtp/email"
        headers = {
            "accept": "application/json",
            "api-key": api_key,
            "content-type": "application/json"
        }
        
        content_key = "htmlContent" if is_html else "textContent"
        
        payload = {
            "sender": {"name": "Stock Alert Bot", "email": sender_email},
            "to": [{"email": recipient}],
            "subject": subject,
            content_key: body
        }
        
        try:
            import requests # Lazy import to ensure it exists
            response = requests.post(url, json=payload, headers=headers)
            if response.status_code in [201, 202]:
                print("\n[Email] Notification sent successfully via Brevo API! 🚀")
            else:
                print(f"\n[Email] Failed (HTTP {response.status_code}): {response.text}")
        except Exception as e:
            print(f"\n[Email] Error sending request: {e}")

# ... (get_market_regime, calculate_rsi, StockAnalyzer, load_stocks - KEEP UNCHANGED) ...

def get_market_regime(logger=None):
    """
    Returns True if SPY is in a bull market (Price > 200 SMA), else False.
    """
    def log(msg):
        
        if logger is not None: logger.append(msg)
        print(msg)

    try:
        spy = yf.Ticker("SPY")
        hist = spy.history(period="1y")
        if hist.empty:
            return False
        
        # Calculate 200-day SMA
        spy_sma200 = hist['Close'].rolling(window=200).mean().iloc[-1]
        current_price = hist['Close'].iloc[-1]
        
        is_bull = current_price > spy_sma200
        log(f"[Market Check] SPY: {current_price:.2f} vs 200SMA: {spy_sma200:.2f} -> {'BULL' if is_bull else 'BEAR'}")
        return is_bull
    except Exception as e:
        log(f"Error checking market regime: {e}")
        return False

# ... (calculate_rsi unchanged) ...
def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

class StockAnalyzer:
    def __init__(self, ticker, ib_client=None):
        self.ticker = ticker
        self.ib_client = ib_client
        self.stock = None
        self.info = None
        self.history = None

    def fetch_data(self):
        try:
            if self.ib_client:
                # Use IBKR
                self.history = self.ib_client.get_historical_data(self.ticker, duration_str='6 M')
                price = self.ib_client.get_market_price(self.ticker)
                self.info = {'currentPrice': price, 'regularMarketPrice': price}
                try:
                    yf_stock = yf.Ticker(self.ticker)
                    yf_info = yf_stock.info
                    self.info['targetMeanPrice'] = yf_info.get('targetMeanPrice')
                    self.info['recommendationKey'] = yf_info.get('recommendationKey')
                except: pass
                return not self.history.empty
            else:
                self.stock = yf.Ticker(self.ticker)
                self.info = self.stock.info
                self.history = self.stock.history(period="6mo") 
                return not self.history.empty
        except: return False

    def is_undervalued(self):
        if self.info is None or self.history is None or self.history.empty: return False, 0
        current_price = self.info.get('currentPrice') or self.info.get('regularMarketPrice')
        target_price = self.info.get('targetMeanPrice')
        if not current_price: return False, 0
        
        six_mo_high = self.history['High'].max()
        
        if target_price:
            reference_value = min(target_price, six_mo_high * 0.80)
        else:
            # Fallback for Crypto/No-Analyst: Use 80% of 6-mo High as valid "fair value"
            reference_value = six_mo_high * 0.80
            
        alert_threshold = reference_value * 0.75
        is_low = current_price <= alert_threshold
        return is_low, alert_threshold

    def check_technical_filters(self, logger=None):
        if self.history is None or len(self.history) < 15: return False, 0
        rsi_series = calculate_rsi(self.history['Close'])
        current_rsi = rsi_series.iloc[-1]
        is_oversold = current_rsi < 30
        return is_oversold, current_rsi

    def is_highly_rated(self):
        if not self.info: return False
        recommendation = self.info.get('recommendationKey', '').lower()
        return recommendation in ['buy', 'strong_buy']
    
    def analyze(self, market_bullish, logger=None):
        if not self.fetch_data(): return None 
        if not market_bullish: return None

        print(f"Checking {self.ticker}...")
        is_low, threshold_used = self.is_undervalued()
        is_oversold, rsi_val = self.check_technical_filters(logger)
        is_buy_rating = self.is_highly_rated()
        
        current_price = (self.info.get('currentPrice') or self.info.get('regularMarketPrice')) or 0
        rating = self.info.get('recommendationKey', 'N/A')
        
        gap_val = current_price - threshold_used
        gap_pct = (gap_val / threshold_used * 100) if threshold_used != 0 else 0
        
        insight = f"   > [Insight] {self.ticker}: Price: {current_price:.2f} (Limit: {threshold_used:.2f}) | RSI: {rsi_val:.2f} | Rating: {rating}"
        print(insight)
        if logger is not None: logger.append(insight)

        is_recommended = is_low and is_oversold and is_buy_rating
        
        return {
            'ticker': self.ticker,
            'price': round(current_price, 2),
            'threshold': round(threshold_used, 2),
            'rating': rating,
            'rsi': round(rsi_val, 2),
            'gap_val': round(gap_val, 2),
            'gap_pct': round(gap_pct, 1),
            'is_recommended': is_recommended
        }

def load_stocks():
    if not os.path.exists(STOCKS_FILE):
        return []
    with open(STOCKS_FILE, 'r') as f:
        return json.load(f)

def job(ib_client=None):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    job_logs = [f"--- Starting Job at {timestamp} (Broker: {'IBKR' if ib_client else 'YFinance'}) ---"]
    
    def log(msg):
        print(msg)
        job_logs.append(msg)

    market_bullish = get_market_regime(job_logs)
    stocks = load_stocks()
    
    analysis_results = []
    if market_bullish and stocks:
        for ticker in stocks:
            analyzer = StockAnalyzer(ticker, ib_client)
            res = analyzer.analyze(market_bullish, job_logs)
            if res:
                analysis_results.append(res)

    found_opportunities = [r for r in analysis_results if r.get('is_recommended')]

    if not market_bullish:
        log("Market is NOT in a strong uptrend (SPY < 200 SMA). Skipping buys for safety.")
        email_subject = "Stock Alert: Market BEARISH (No Buys)"
    elif found_opportunities:
        log(f"\nFound {len(found_opportunities)} interesting stocks!")
        email_subject = f"Stock Alert: {len(found_opportunities)} OPPS FOUND! 🚀"
        for opp in found_opportunities:
             msg = f"{opp['ticker']} is BUY! Price: {opp['price']} (<= {opp['threshold']}), RSI: {opp['rsi']}"
             AlertSystem.send_alert(msg)
             log(msg)
             if ib_client:
                 log(f"  [AUTO] Order Logic: Entry {opp['price']}, TP {opp['price']*1.15:.2f}, SL (Trailing 10%) {opp['price']*0.90:.2f}")
    else:
        log("\nNo matching stocks found this run.")
        email_subject = "Stock Alert: Daily Insight Report"

    # HTML Generation
    html_template = """
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background-color: #f4f4f7; color: #333; margin: 0; padding: 20px; }}
            .container {{ background-color: #ffffff; padding: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); max-width: 600px; margin: auto; }}
            h2 {{ color: #1a1a1a; font-size: 18px; margin-top: 0; }}
            table {{ width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 13px; }}
            th, td {{ padding: 8px; text-align: left; border-bottom: 1px solid #eee; }}
            th {{ background-color: #f8f9fa; color: #666; font-weight: 600; text-transform: uppercase; font-size: 11px; }}
            .rec {{ background-color: #d4edda !important; color: #155724; font-weight: bold; }}
            .gap-neg {{ color: #d9534f; }}
            .gap-pos {{ color: #5cb85c; }}
            .meta {{ font-size: 12px; color: #888; margin-bottom: 10px; }}
            @media screen and (max-width: 480px) {{
                th, td {{ padding: 6px 4px; font-size: 12px; }}
                .hide-mobile {{ display: none; }}
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h2>Stock Insights - {timestamp}</h2>
            <div class="meta">{market_status}</div>
            <table>
                <thead>
                    <tr>
                        <th>Ticker</th>
                        <th>Price</th>
                        <th>Limit</th>
                        <th>Gap</th>
                        <th>RSI</th>
                        <th class="hide-mobile">Rating</th>
                    </tr>
                </thead>
                <tbody>
                    {table_rows}
                </tbody>
            </table>
            <p style="font-size: 11px; color: #999; margin-top: 20px;">
                * Green highlighting indicates RSI < 30 + Price <= Limit + BUY rating.
            </p>
        </div>
    </body>
    </html>
    """
    
    market_status = "🟢 Market Bullish (SPY > 200 SMA)" if market_bullish else "🔴 Market Bearish (SPY < 200 SMA)"
    
    rows = ""
    for r in analysis_results:
        row_class = 'class="rec"' if r['is_recommended'] else ""
        gap_style = "gap-neg" if r['gap_val'] > 0 else "gap-pos"
        gap_text = f"{r['gap_val']:.2f} ({r['gap_pct']:+.1f}%)"
        
        rows += f"""
        <tr {row_class}>
            <td>{r['ticker']}</td>
            <td>{r['price']}</td>
            <td>{r['threshold']}</td>
            <td class="{gap_style}">{gap_text}</td>
            <td>{r['rsi']}</td>
            <td class="hide-mobile">{r['rating']}</td>
        </tr>
        """
    
    if not analysis_results:
        rows = "<tr><td colspan='6' style='text-align:center;'>No data available or market bearish.</td></tr>"

    email_html = html_template.format(
        timestamp=timestamp,
        market_status=market_status,
        table_rows=rows
    )

    AlertSystem.send_email(email_subject, email_html, is_html=True)
    log("--- Job Finished ---")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--broker', action='store_true', help="Use Interactive Brokers for data/trading")
    parser.add_argument('--port', type=int, default=7497, help="IBKR Port (7497=Paper, 7496=Live)")
    parser.add_argument('--once', action='store_true', help="Run job once and exit (for Cron/CI)")
    args = parser.parse_args()
    
    ib_client = None
    if args.broker:
        print(f"Connecting to Interactive Brokers on Port {args.port}...")
        ib_client = IBClient(port=args.port)
        ib_client.connect()
        if not ib_client.connected:
            print("Failed to connect to IBKR. Exiting.")
            return

    print("Stock Alert System initialized (Advanced Mode).")
    print("Monitoring stocks. Email Notifications Enabled.")
    
    # Run once immediately
    job(ib_client)

    if args.once:
        print("\n[One-Shot Mode] Job complete. Exiting.")
        if ib_client: ib_client.disconnect()
        return

    # Schedule (Fallback for long-running local dev. Production is handled by GitHub Actions)
    # 23:05 local time is a safe daily summary if run locally.
    schedule.every().day.at("23:05").do(job, ib_client=ib_client)

    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        if ib_client: ib_client.disconnect()
        print("\nExiting...")

if __name__ == "__main__":
    main()
