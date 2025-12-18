import yfinance as yf
import json
import pandas as pd
import os

STOCKS_FILE = "stocks.json"

def load_stocks():
    if not os.path.exists(STOCKS_FILE):
        return []
    with open(STOCKS_FILE, 'r') as f:
        return json.load(f)

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def run_strategy(stocks, strategy_name, spy_hist):
    print(f"\n--- Running {strategy_name} Strategy ---")
    
    total_trades = 0
    wins = 0
    losses = 0
    open_positions = 0

    for ticker in stocks:
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period="2y")
            info = stock.info
            target_price = info.get('targetMeanPrice')
            
            if not target_price or hist.empty:
                continue

            # Indicators
            hist['6mHigh'] = hist['High'].rolling(window=126).max()
            if strategy_name != "Baseline":
                hist['RSI'] = calculate_rsi(hist['Close'])
                hist = hist.join(spy_hist[['Close', 'SMA200']].rename(columns={'Close': 'SPY_Close', 'SMA200': 'SPY_SMA200'}))

            # Sim Start
            sim_start_index = max(0, len(hist) - 252)
            sim_data = hist.iloc[sim_start_index:].copy()
            
            position = None
            
            for date, row in sim_data.iterrows():
                close_price = row['Close']
                open_price = row['Open']
                high_price = row['High']
                low_price = row['Low']
                six_mo_high = row['6mHigh']
                
                if pd.isna(six_mo_high): continue

                # --- EXIT ---
                if position:
                    entry_price = position['price']
                    days_held = (date - position['date']).days
                    
                    if strategy_name == "Baseline":
                        # Fixed 15% Profit Target
                        sell_target = entry_price * 1.15
                        if high_price >= sell_target:
                            wins += 1
                            position = None
                        continue
                        
                    elif strategy_name == "Advanced":
                        # Hybrid: Target OR Trailing Stop
                        
                        # 1. Profit Target (15%) - Capture the win!
                        if high_price >= (entry_price * 1.15):
                            wins += 1
                            position = None
                            continue

                        # 2. Trailing Stop 10%
                        if high_price > position['highest_price']:
                            position['highest_price'] = high_price
                        
                        trailing_stop = position['highest_price'] * 0.90
                        
                        # Check Stops
                        if low_price <= trailing_stop:
                            if trailing_stop > entry_price: wins += 1
                            else: losses += 1
                            position = None
                        elif days_held >= 45:
                            if close_price > entry_price: wins += 1
                            else: losses += 1
                            position = None
                        continue
                     
                    elif strategy_name == "PatientHunter":
                        # Simplest Optimization: Baseline + Time Stop
                        # No Trailing Stop. Just Target or Time.
                        
                        # 1. Target Exit (Like Baseline)
                        sell_target = entry_price * 1.15
                        if high_price >= sell_target:
                            wins += 1
                            position = None
                            
                        # 2. Time Stop (60 Days)
                        elif days_held >= 60:
                            if close_price > entry_price: wins += 1
                            else: losses += 1
                            position = None
                        continue

                # --- ENTRY ---
                # Check Filters
                if strategy_name == "Advanced":
                     if row['SPY_Close'] <= row['SPY_SMA200']: continue
                     if row['RSI'] >= 30: continue
                
                # PatientHunter = Baseline Entry (No filters)

                # Formula
                reference_value = min(target_price, six_mo_high * 0.80)
                threshold = reference_value * 0.75
                
                if close_price <= threshold:
                    position = {'price': close_price, 'date': date, 'highest_price': close_price}
                    total_trades += 1
            
            if position:
                open_positions += 1

        except Exception as e:
            print(f"Error {ticker}: {e}")

    return {
        'trades': total_trades,
        'wins': wins,
        'losses': losses, 
        'open': open_positions,
        'win_rate': (wins / (wins + losses)) * 100 if (wins + losses) > 0 else 0
    }

def run_portfolio_simulation(stocks, strategy_name, spy_hist, period_years=1, start_capital=10000, time_stop_days=60):
    print(f"\n--- Sim: {strategy_name} ({period_years}y, Stop: {time_stop_days}d) ---")
    
    # 1. Pre-fetch and process all data
    market_data = {} # {ticker: DataFrame}
    all_dates = set()
    valid_tickers = []
    
    fetch_period = "2y" if period_years >= 2 else "1y"
    
    for ticker in stocks:
        stock = yf.Ticker(ticker)
        hist = stock.history(period=fetch_period)
        info = stock.info
        target_price = info.get('targetMeanPrice')
        
        if hist.empty:
            continue
            
        # Indicators
        hist['6mHigh'] = hist['High'].rolling(window=126).max()
        hist['RSI'] = calculate_rsi(hist['Close'])
        
        # Join SPY
        hist = hist.join(spy_hist[['Close', 'SMA200']].rename(columns={'Close': 'SPY_Close', 'SMA200': 'SPY_SMA200'}))
        
        # Value Logic with Fallback
        def get_ref_val(row, tgt):
            high_6m = row['6mHigh']
            if pd.isna(high_6m): return None
            
            if tgt:
                return min(tgt, high_6m * 0.80)
            else:
                return high_6m * 0.80

        hist['RefVal'] = hist.apply(lambda row: get_ref_val(row, target_price), axis=1)
        hist['BuyThreshold'] = hist['RefVal'] * 0.75
        
        market_data[ticker] = hist
        all_dates.update(hist.index)
        valid_tickers.append(ticker)

    sorted_dates = sorted(list(all_dates))
    
    # Filter dates based on requested period simulation duration
    # We want to simulate only the last N years
    # If we fetched 2y but want 1y sim, slice the dates
    if period_years == 1:
        cutoff_date = sorted_dates[-1] - pd.DateOffset(years=1)
        sorted_dates = [d for d in sorted_dates if d > cutoff_date]
    
    # 2. Simulation Loop
    cash = start_capital
    position = None 
    
    trade_log = []
    
    for current_date in sorted_dates:
        # A. Check Exit if holding
        if position:
            ticker = position['ticker']
            df = market_data[ticker]
            
            if current_date not in df.index: continue
            row = df.loc[current_date]
            high_price = row['High']
            low_price = row['Low']
            close_price = row['Close']
            
            # PATIENT HUNTER LOGIC
            entry_price = position['entry_price']
            days_held = (current_date - position['date']).days
            
            sell_reason = None
            sell_price = 0
            
            if strategy_name == "Baseline":
                if high_price >= entry_price * 1.15:
                    sell_reason = "Target (+15%)"
                    sell_price = entry_price * 1.15
            else:
                # PATIENT HUNTER logic (Strategy: Advanced)
                if high_price > position['highest_price']:
                    position['highest_price'] = high_price
                
                initial_sl = entry_price * 0.85
                
                # Activate Trailing Stop if hit +10%
                if not position.get('trailing_active') and high_price >= entry_price * 1.10:
                    position['trailing_active'] = True
                
                if high_price >= entry_price * 1.15:
                    sell_reason = "Target (+15%)"
                    sell_price = entry_price * 1.15
                elif position.get('trailing_active') and low_price <= (position['highest_price'] * 0.90):
                    sell_reason = "Trailing Stop (-10%)"
                    sell_price = position['highest_price'] * 0.90
                elif not position.get('trailing_active') and low_price <= initial_sl:
                    sell_reason = "Initial Stop (-15%)"
                    sell_price = initial_sl
                elif days_held >= time_stop_days:
                    sell_reason = f"Time Stop ({time_stop_days}d)"
                    sell_price = close_price
            
            if sell_reason:
                revenue = position['shares'] * sell_price
                gain = revenue - (position['shares'] * entry_price)
                pct = (gain / (position['shares'] * entry_price)) * 100
                cash = revenue
                trade_log.append({'type': 'SELL', 'ticker': ticker, 'pnl': gain})
                print(f"  [SELL] {ticker} on {current_date.date()} @ {sell_price:.2f} ({sell_reason}) -> PnL: ${gain:.2f} ({pct:+.1f}%)")
                position = None
                continue 

        # B. Check Entry
        if position is None:
            candidates = []
            for ticker in valid_tickers:
                df = market_data[ticker]
                if current_date not in df.index: continue
                row = df.loc[current_date]
                price = row['Close']
                
                if pd.isna(row['BuyThreshold']): continue

                if price <= row['BuyThreshold']:
                    if strategy_name == "Advanced":
                        if row['RSI'] >= 30: continue
                        if row['SPY_Close'] <= row['SPY_SMA200']: continue
                        if row['Close'] <= row['Open']: continue # Green Day Rule
                    candidates.append((ticker, price, row['RSI'] if 'RSI' in row else 0))
            
            if candidates:
                candidates.sort(key=lambda x: x[2]) 
                tick, price, rsi = candidates[0]
                shares = cash / price
                position = {'ticker': tick, 'shares': shares, 'entry_price': price, 'highest_price': price, 'date': current_date, 'trailing_active': False}
                cash = 0
                print(f"  [BUY]  {tick} on {current_date.date()} @ {price:.2f}")

    final_value = cash
    if position:
        ticker = position['ticker']
        curr_price = market_data[ticker].iloc[-1]['Close']
        val = position['shares'] * curr_price
        final_value = val
        print(f"  [HOLD] {ticker} @ {curr_price:.2f} (Value: ${val:.2f})")
    
    print(f"  -> Final: ${final_value:,.2f} (ROI: {((final_value-start_capital)/start_capital)*100:+.1f}%)")
    return final_value

def run_backtest():
    stocks = load_stocks()
    # Fetch SPY once
    spy = yf.Ticker("SPY")
    spy_hist = spy.history(period="5y") 
    spy_hist['SMA200'] = spy_hist['Close'].rolling(window=200).mean()

    # Run Standard Backtest (Default 60 Days)
    for period in [1, 2]:
        print(f"\n{'='*20} {period}-YEAR BACKTEST {'='*20}")
        run_portfolio_simulation(stocks, "Baseline", spy_hist, period_years=period)
        run_portfolio_simulation(stocks, "Advanced", spy_hist, period_years=period, time_stop_days=60)
    print("="*60)

if __name__ == "__main__":
    run_backtest()
