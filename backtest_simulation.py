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

def run_portfolio_simulation(stocks, strategy_name, spy_hist, start_capital=10000):
    print(f"\n--- Running Portfolio Sim: {strategy_name} ---")
    
    # 1. Pre-fetch and process all data
    # We need a unified timeline.
    market_data = {} # {ticker: DataFrame}
    all_dates = set()
    
    valid_tickers = []
    
    for ticker in stocks:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="1y") # Last 1 year for the test
        info = stock.info
        target_price = info.get('targetMeanPrice')
        
        if not target_price or hist.empty:
            continue
            
        # Indicators
        hist['6mHigh'] = hist['High'].rolling(window=126).max()
        hist['RSI'] = calculate_rsi(hist['Close'])
        
        # Join SPY
        hist = hist.join(spy_hist[['Close', 'SMA200']].rename(columns={'Close': 'SPY_Close', 'SMA200': 'SPY_SMA200'}))
        
        # Formula Threshold
        # We need to apply the formula row-by-row or vectorized
        # Value = min(Target, 0.8 * 6mHigh)
        # Threshold = 0.75 * Value
        # Note: target_price is constant (Scalar), which is a limitation but consistent with previous tests
        
        # Vectorized Formula
        # limit 6mHigh to avoid NaNs
        hist['RefVal'] = hist['6mHigh'].apply(lambda x: min(target_price, x * 0.80) if pd.notnull(x) else None)
        hist['BuyThreshold'] = hist['RefVal'] * 0.75
        
        market_data[ticker] = hist
        all_dates.update(hist.index)
        valid_tickers.append(ticker)

    sorted_dates = sorted(list(all_dates))
    
    # 2. Simulation Loop
    cash = start_capital
    position = None # {'ticker': 'AAPL', 'shares': 50, 'entry_price': 100, 'highest_price': 105, 'date': buy_date}
    
    trade_log = []
    
    for current_date in sorted_dates:
        # A. Check Exit if holding
        if position:
            ticker = position['ticker']
            df = market_data[ticker]
            
            if current_date not in df.index:
                continue # No data for held stock today
                
            row = df.loc[current_date]
            high_price = row['High']
            low_price = row['Low']
            close_price = row['Close']
            
            # Record Highest Price for Trailing Stop
            if high_price > position['highest_price']:
                position['highest_price'] = high_price
                
            days_held = (current_date - position['date']).days
            entry_price = position['entry_price']
            
            sell_reason = None
            sell_price = 0
            
            if strategy_name == "Baseline":
                # Exit: +15% Profit
                if high_price >= entry_price * 1.15:
                    sell_reason = "Target (+15%)"
                    sell_price = entry_price * 1.15
            
            elif strategy_name == "Advanced":
                # Exit 1: +15% Target
                if high_price >= entry_price * 1.15:
                    sell_reason = "Target (+15%)"
                    sell_price = entry_price * 1.15
                
                # Exit 2: Trailing Stop 10%
                elif low_price <= (position['highest_price'] * 0.90):
                    sell_reason = "Trailing Stop (-10%)"
                    sell_price = position['highest_price'] * 0.90
                    
                # Exit 3: Time Stop 45d
                elif days_held >= 45:
                    sell_reason = "Time Stop (45d)"
                    sell_price = close_price
            
            if sell_reason:
                # EXECUTE SELL
                revenue = position['shares'] * sell_price
                gain = revenue - (position['shares'] * entry_price)
                pct = (gain / (position['shares'] * entry_price)) * 100
                
                cash = revenue
                trade_log.append({
                    'type': 'SELL',
                    'date': current_date.date(),
                    'ticker': ticker,
                    'price': sell_price,
                    'reason': sell_reason,
                    'pnl': gain,
                    'pnl_pct': pct,
                    'new_balance': cash
                })
                print(f"  [SELL] {ticker} on {current_date.date()} @ {sell_price:.2f} ({sell_reason}) -> PnL: ${gain:.2f} ({pct:+.1f}%)")
                position = None
                continue # Position closed, can't buy same day (simple rule)

        # B. Check Entry if Cash Available
        # Note: If we just sold, 'position' is None, so we *could* buy same day.
        # But let's assume one action per day for simplicity.
        
        if position is None:
            # Find Potential Buys
            candidates = []
            
            for ticker in valid_tickers:
                df = market_data[ticker]
                if current_date not in df.index: continue
                
                row = df.loc[current_date]
                price = row['Close']
                threshold = row['BuyThreshold']
                
                if pd.isna(threshold): continue
                
                # Formula Check
                if price <= threshold:
                    # Filter Checks
                    if strategy_name == "Advanced":
                        # RSI < 30 & SPY > 200SMA
                        if row['RSI'] >= 30: continue
                        if row['SPY_Close'] <= row['SPY_SMA200']: continue
                        
                    candidates.append((ticker, price, row['RSI'] if 'RSI' in row else 0))
            
            if candidates:
                # Pick one.
                # Rule: Pick the one with lowest RSI (most oversold)? Or just first?
                # Let's pick lowest RSI to be 'smart'
                candidates.sort(key=lambda x: x[2]) 
                best_pick = candidates[0]
                
                tick, price, rsi = best_pick
                shares = cash / price
                
                position = {
                    'ticker': tick,
                    'shares': shares,
                    'entry_price': price,
                    'highest_price': price,
                    'date': current_date
                }
                cash = 0 # All in
                
                print(f"  [BUY]  {tick} on {current_date.date()} @ {price:.2f}")
                trade_log.append({
                    'type': 'BUY',
                    'date': current_date.date(),
                    'ticker': tick,
                    'price': price,
                    'rsi': rsi
                })
    
    # End of Sim
    final_value = cash
    if position:
        # Mark to market
        ticker = position['ticker']
        curr_price = market_data[ticker].iloc[-1]['Close']
        val = position['shares'] * curr_price
        final_value = val
        print(f"  [HOLD] {ticker} @ {curr_price:.2f} (Value: ${val:.2f})")
    
    return final_value, trade_log

def run_backtest():
    stocks = load_stocks()
    print(f"Backtesting on: {stocks}")
    
    # Fetch SPY once
    spy = yf.Ticker("SPY")
    spy_hist = spy.history(period="2y")
    spy_hist['SMA200'] = spy_hist['Close'].rolling(window=200).mean()

    # Run Portfolio Sims
    baseline_final, _ = run_portfolio_simulation(stocks, "Baseline", spy_hist)
    advanced_final, _ = run_portfolio_simulation(stocks, "Advanced", spy_hist)
    
    print("\n" + "="*40)
    print(f"PORTFOLIO PERFORMANCE (Start: $10,000)")
    print("="*40)
    print(f"BASELINE Final Value: ${baseline_final:,.2f} (ROI: {((baseline_final-10000)/10000)*100:+.1f}%)")
    print(f"ADVANCED Final Value: ${advanced_final:,.2f} (ROI: {((advanced_final-10000)/10000)*100:+.1f}%)")
    print("="*40)

if __name__ == "__main__":
    run_backtest()
