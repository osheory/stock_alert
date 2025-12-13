import argparse
import yfinance as yf
import schedule
import time
import json
import os
import datetime
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
    def send_email(subject, body):
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
        payload = {
            "sender": {"name": "Stock Alert Bot", "email": sender_email},
            "to": [{"email": recipient}],
            "subject": subject,
            "textContent": body
        }
        
        try:
            import requests # Lazy import to ensure it exists
            response = requests.post(url, json=payload, headers=headers)
            if response.status_code in [201, 202]:
                print("\n[Email] Notification sent successfully via Brevo API! �")
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
        if not current_price or not target_price: return False, 0
        six_mo_high = self.history['High'].max()
        reference_value = min(target_price, six_mo_high * 0.80)
        alert_threshold = reference_value * 0.75
        is_low = current_price <= alert_threshold
        return is_low, alert_threshold

    def check_technical_filters(self, logger=None):
        if self.history is None or len(self.history) < 15: return False, 0
        rsi_series = calculate_rsi(self.history['Close'])
        current_rsi = rsi_series.iloc[-1]
        is_oversold = current_rsi < 30
        
        msg = f"  > {self.ticker}: RSI={current_rsi:.2f} ({'Oversold' if is_oversold else 'Neutral'}) - Price: {self.info.get('currentPrice', 0):.2f}"
        if logger is not None and is_oversold: logger.append(msg)
        print(msg)
        
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
        if not is_low: return None

        is_oversold, rsi_val = self.check_technical_filters(logger)
        if not is_oversold: return None

        is_buy_rating = self.is_highly_rated()
        if not is_buy_rating: return None

        return {
            'ticker': self.ticker,
            'price': self.info.get('currentPrice'),
            'threshold': round(threshold_used, 2),
            'rating': self.info.get('recommendationKey'),
            'rsi': round(rsi_val, 2)
        }

def load_stocks():
    if not os.path.exists(STOCKS_FILE):
        return []
    with open(STOCKS_FILE, 'r') as f:
        return json.load(f)

def job(ib_client=None):
    job_logs = []
    def log(msg):
        job_logs.append(msg)
        print(msg)

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log(f"\n--- Starting Job at {timestamp} (Broker: {'IBKR' if ib_client else 'YFinance'}) ---")
    
    market_bullish = get_market_regime(job_logs)
    if not market_bullish:
        log("Market is NOT in a strong uptrend (SPY < 200 SMA). Skipping manual buys.")
        log("--- Job Finished ---")
        # AlertSystem.send_email(f"Stock Alert: Job Ran ({timestamp}) - BEAR MARKET", "\n".join(job_logs)) # <-- DISABLED
        return

    stocks = load_stocks()
    if not stocks:
        log("No stocks to check.")
        return

    found_opportunities = []

    for ticker_symbol in stocks:
        analyzer = StockAnalyzer(ticker_symbol, ib_client)
        # We pass job_logs only if we want detailed logs for every check. 
        # Let's keep email clean: only market status + found opps + summary.
        # But analyze() prints "Checking..." which we might not want in email.
        # Let's intentionally NOT pass job_logs to analyze() to keep email short, 
        # unless we want to debug.  Actually, let's pass it to capture RSI values of *candidates*.
        result = analyzer.analyze(market_bullish, job_logs)
        if result:
            found_opportunities.append(result)

    if found_opportunities:
        log(f"\nFound {len(found_opportunities)} interesting stocks!")
        
        email_subject = f"Stock Alert: {len(found_opportunities)} OPPS FOUND! 🚀"
        
        for opp in found_opportunities:
             msg = f"{opp['ticker']} is BUY! Price: {opp['price']} (<= {opp['threshold']}), RSI: {opp['rsi']}"
             AlertSystem.send_alert(msg)
             log(msg)
             
             if ib_client:
                 log(f"  [AUTO] Placing Bracket Order for {opp['ticker']}...")
                 qty = 1 
                 entry = opp['price']
                 take_profit = entry * 1.15
                 stop_loss = entry * 0.90
                 # ib_client.submit_bracket_order(opp['ticker'], qty, entry, take_profit, stop_loss)
                 log(f"  [AUTO] Order Logic Ready (Qty {qty}, TP {take_profit:.2f}, SL {stop_loss:.2f})")
        
        # Send Email ONLY if opps found
        email_body = "\n".join(job_logs)
        AlertSystem.send_email(email_subject, email_body)

    else:
        log("\nNo matching stocks found this run.")
        # email_subject = f"Stock Alert: Job Ran ({timestamp}) - No Opps"
        # AlertSystem.send_email(email_subject, email_body) # <-- DISABLED per user request
        
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

    # Schedule
    schedule.every(1).hours.do(job, ib_client=ib_client)

    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        if ib_client: ib_client.disconnect()
        print("\nExiting...")

if __name__ == "__main__":
    main()
