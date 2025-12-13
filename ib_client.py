from ib_insync import *
import asyncio
import datetime
import pandas as pd

class IBClient:
    def __init__(self, host='127.0.0.1', port=7497, client_id=1):
        self.ib = IB()
        self.host = host
        self.port = port
        self.client_id = client_id
        self.connected = False

    def connect(self):
        try:
            if not self.ib.isConnected():
                self.ib.connect(self.host, self.port, clientId=self.client_id)
                self.connected = True
                print(f"[IBKR] Connected to {self.host}:{self.port} (ID: {self.client_id})")
        except Exception as e:
            print(f"[IBKR] Connection Failed: {e}")
            self.connected = False
            
    def disconnect(self):
        if self.ib.isConnected():
            self.ib.disconnect()
            self.connected = False
            print("[IBKR] Disconnected.")

    def get_historical_data(self, ticker_symbol, duration_str='6 M', bar_size='1 day'):
        """
        Fetches historical data to replace yfinance history.
        """
        if not self.connected:
            self.connect()
            if not self.connected: return pd.DataFrame()

        contract = Stock(ticker_symbol, 'SMART', 'USD')
        
        # Qualify contract
        try:
            self.ib.qualifyContracts(contract)
        except Exception as e:
            print(f"[IBKR] Error qualifying contract {ticker_symbol}: {e}")
            return pd.DataFrame()

        bars = self.ib.reqHistoricalData(
            contract,
            endDateTime='',
            durationStr=duration_str,
            barSizeSetting=bar_size,
            whatToShow='TRADES',
            useRTH=True
        )
        
        if not bars:
            return pd.DataFrame()
            
        df = util.df(bars)
        # Normalize columns to match yfinance ('date' -> index, 'open', 'high', 'low', 'close', 'volume')
        # ib_insync returns: date, open, high, low, close, volume, barCount, average
        df['date'] = pd.to_datetime(df['date'])
        df.set_index('date', inplace=True)
        # Rename columns to Title Case to match yfinance
        df.rename(columns={'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'volume': 'Volume'}, inplace=True)
        
        return df

    def get_market_price(self, ticker_symbol):
        if not self.connected: self.connect()
        
        contract = Stock(ticker_symbol, 'SMART', 'USD')
        self.ib.qualifyContracts(contract)
        
        # Request market data
        ticker = self.ib.reqMktData(contract, '', False, False)
        self.ib.sleep(2) # Wait for data
        
        price = ticker.last if ticker.last > 0 else ticker.close
        if price <= 0:
            price = ticker.marketPrice()
            
        return price

    def submit_bracket_order(self, ticker_symbol, quantity, limit_price, take_profit_price, stop_loss_price):
        """
        Submits a bracket order:
        1. Parent: Limit Buy
        2. Child 1: Limit Sell (Take Profit)
        3. Child 2: Stop Loss
        """
        if not self.connected: self.connect()
        
        contract = Stock(ticker_symbol, 'SMART', 'USD')
        self.ib.qualifyContracts(contract)
        
        # Parent Order (Entry)
        parent = Order()
        parent.action = 'BUY'
        parent.totalQuantity = quantity
        parent.orderType = 'LMT'
        parent.lmtPrice = limit_price
        parent.transmit = False
        
        # Profit Taker
        take_profit = Order()
        take_profit.action = 'SELL'
        take_profit.totalQuantity = quantity
        take_profit.orderType = 'LMT'
        take_profit.lmtPrice = take_profit_price
        take_profit.parentId = parent.orderId
        take_profit.transmit = False
        
        # Stop Loss
        stop_loss = Order()
        stop_loss.action = 'SELL'
        stop_loss.totalQuantity = quantity
        stop_loss.orderType = 'STP'
        stop_loss.auxPrice = stop_loss_price
        stop_loss.parentId = parent.orderId
        stop_loss.transmit = True # Transmit the whole bracket
        
        orders = self.ib.bracketOrder(
            'BUY', 
            quantity, 
            limit_price, 
            take_profit_price, 
            stop_loss_price
        )
        
        # BracketOrder returns list [parent, takeProfit, stopLoss]
        # But we need to place them.
        for o in orders:
            trade = self.ib.placeOrder(contract, o)
            
        print(f"[IBKR] Bracket Order Placed for {ticker_symbol}: Entry {limit_price}, TP {take_profit_price}, SL {stop_loss_price}")
        return orders
