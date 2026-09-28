---
name: python-trading-toolkit
description: Analyze stocks, manage portfolios, and monitor markets using Python. Use when the user asks to check stock prices, analyze a portfolio, track market movements, calculate trading metrics, or run quantitative analysis on equities. Supports US, HK, and A-share markets.
_agensi: "3f0124c1-e637-4e47-9003-f7b8e6fc39da"
---

# Python Trading Toolkit

A comprehensive skill for AI agents to perform stock market analysis, portfolio management, and quantitative trading research using Python.

## When to Use

- User asks to check stock prices, market data, or ticker information
- User wants to analyze portfolio performance, risk metrics, or attribution
- User requests market monitoring, screening, or technical analysis
- User needs trading calculations (position sizing, R-multiples, Sharpe ratio, etc.)
- User asks about options flow, earnings data, or economic events
- "Analyze my portfolio" / "Check stock prices" / "Run market screening"

## Capabilities

### 1. Market Data Retrieval
- Real-time and historical price data via Yahoo Finance and Sina Finance APIs
- Support for US (NYSE/NASDAQ), HK (HKEX), and A-Share (SSE/SZSE) markets
- Earnings calendars, economic events, and SEC filing data

### 2. Portfolio Analysis
- Position-weighted performance calculation
- Multi-factor attribution (market, sector, style factors)
- Risk metrics: Sharpe ratio, Sortino ratio, max drawdown, VaR, beta
- Correlation matrix and concentration analysis

### 3. Technical Analysis
- Moving averages (SMA, EMA), RSI, MACD, Bollinger Bands
- Support/resistance level detection
- Volume analysis and anomaly detection
- Pattern recognition (head and shoulders, double top/bottom)

### 4. Options Analysis
- Options chain retrieval and analysis
- Implied volatility calculation
- Greeks estimation (delta, gamma, theta, vega)
- Unusual activity detection (volume/OI ratio spikes)

### 5. Screening and Alerts
- Multi-criteria stock screening (P/E, revenue growth, volume, etc.)
- Price alert thresholds (gap up/down, volume spikes, new highs/lows)
- Watchlist monitoring with configurable triggers

## Process

### Step 1: Identify the Request Type

Determine which capability the user needs:
- **Market data** → Step 2A
- **Portfolio analysis** → Step 2B
- **Technical analysis** → Step 2C
- **Options analysis** → Step 2D
- **Screening/alerts** → Step 2E

### Step 2A: Market Data Retrieval

```python
import yfinance as yf
import requests
import pandas as pd

def get_stock_data(ticker, period="1mo", interval="1d"):
    """Fetch stock data from Yahoo Finance."""
    stock = yf.Ticker(ticker)
    hist = stock.history(period=period, interval=interval)
    info = stock.info
    return hist, info

def get_hk_stock_data(ticker):
    """Fetch HK stock data via Sina Finance API."""
    url = f"https://hq.sinajs.cn/list={ticker}"
    headers = {"Referer": "https://finance.sina.com.cn"}
    resp = requests.get(url, headers=headers)
    return resp.text

def get_ashare_data(ticker):
    """Fetch A-share data via Sina Finance API."""
    url = f"https://hq.sinajs.cn/list={ticker}"
    headers = {"Referer": "https://finance.sina.com.cn"}
    resp = requests.get(url, headers=headers)
    return resp.text
```

### Step 2B: Portfolio Analysis

```python
import numpy as np
import pandas as pd

def calculate_portfolio_metrics(positions_df, returns_df):
    """
    Calculate portfolio performance metrics.
    
    positions_df: DataFrame with columns [ticker, weight, current_price, cost_basis]
    returns_df: DataFrame with daily returns for each ticker (columns = tickers)
    """
    portfolio_returns = (returns_df * positions_df.set_index('ticker')['weight']).sum(axis=1)
    
    total_return = (1 + portfolio_returns).prod() - 1
    annualized_return = (1 + total_return) ** (252 / len(portfolio_returns)) - 1
    volatility = portfolio_returns.std() * np.sqrt(252)
    sharpe_ratio = annualized_return / volatility if volatility > 0 else 0
    
    cumulative = (1 + portfolio_returns).cumprod()
    running_max = cumulative.cummax()
    drawdown = (cumulative - running_max) / running_max
    max_drawdown = drawdown.min()
    
    return {
        'total_return': total_return,
        'annualized_return': annualized_return,
        'volatility': volatility,
        'sharpe_ratio': sharpe_ratio,
        'max_drawdown': max_drawdown,
        'trading_days': len(portfolio_returns)
    }

def calculate_attribution(positions_df, benchmark_returns, factor_returns=None):
    """
    Calculate portfolio attribution vs benchmark.
    Returns alpha, beta, tracking error, and information ratio.
    """
    portfolio_returns = (factor_returns * positions_df.set_index('ticker')['weight']).sum(axis=1) \
        if factor_returns is not None else None
    
    if portfolio_returns is not None:
        beta = np.cov(portfolio_returns, benchmark_returns)[0][1] / np.var(benchmark_returns)
        alpha = portfolio_returns.mean() - beta * benchmark_returns.mean()
        tracking_error = (portfolio_returns - benchmark_returns).std() * np.sqrt(252)
        info_ratio = alpha / tracking_error if tracking_error > 0 else 0
        
        return {
            'alpha': alpha * 252,
            'beta': beta,
            'tracking_error': tracking_error,
            'information_ratio': info_ratio
        }
    return {}
```

### Step 2C: Technical Analysis

```python
def calculate_technical_indicators(df):
    """Calculate common technical indicators from OHLCV data."""
    df['SMA_20'] = df['Close'].rolling(window=20).mean()
    df['SMA_50'] = df['Close'].rolling(window=50).mean()
    df['EMA_12'] = df['Close'].ewm(span=12).mean()
    df['EMA_26'] = df['Close'].ewm(span=26).mean()
    
    delta = df['Close'].diff()
    gain = delta.where(delta > 0, 0).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))
    
    df['MACD'] = df['EMA_12'] - df['EMA_26']
    df['MACD_Signal'] = df['MACD'].ewm(span=9).mean()
    df['MACD_Hist'] = df['MACD'] - df['MACD_Signal']
    
    df['BB_Middle'] = df['Close'].rolling(window=20).mean()
    df['BB_Upper'] = df['BB_Middle'] + 2 * df['Close'].rolling(window=20).std()
    df['BB_Lower'] = df['BB_Middle'] - 2 * df['Close'].rolling(window=20).std()
    
    df['Volume_Z'] = (df['Volume'] - df['Volume'].rolling(20).mean()) / df['Volume'].rolling(20).std()
    
    return df

def detect_signals(df):
    """Detect trading signals from technical indicators."""
    signals = []
    latest = df.iloc[-1]
    
    if df['SMA_20'].iloc[-2] < df['SMA_50'].iloc[-2] and latest['SMA_20'] > latest['SMA_50']:
        signals.append("BULLISH: Golden Cross (SMA 20 crossed above SMA 50)")
    elif df['SMA_20'].iloc[-2] > df['SMA_50'].iloc[-2] and latest['SMA_20'] < latest['SMA_50']:
        signals.append("BEARISH: Death Cross (SMA 20 crossed below SMA 50)")
    
    if latest['RSI'] < 30:
        signals.append(f"BULLISH: RSI oversold at {latest['RSI']:.1f}")
    elif latest['RSI'] > 70:
        signals.append(f"BEARISH: RSI overbought at {latest['RSI']:.1f}")
    
    if latest['Volume_Z'] > 2:
        signals.append(f"ALERT: Volume spike detected (Z-score: {latest['Volume_Z']:.1f})")
    
    if latest['Close'] > latest['BB_Upper']:
        signals.append("BULLISH: Price broke above upper Bollinger Band")
    elif latest['Close'] < latest['BB_Lower']:
        signals.append("BEARISH: Price broke below lower Bollinger Band")
    
    return signals
```

### Step 2D: Options Analysis

```python
def analyze_options_chain(ticker):
    """Analyze options chain for a given ticker."""
    stock = yf.Ticker(ticker)
    
    try:
        expirations = stock.options
        if not expirations:
            return "No options data available"
        
        nearest = expirations[0]
        chain = stock.option_chain(nearest)
        calls = chain.calls
        puts = chain.puts
        
        call_volume = calls['volume'].sum()
        put_volume = puts['volume'].sum()
        pc_ratio = put_volume / call_volume if call_volume > 0 else float('inf')
        
        unusual_calls = calls[calls['volume'] > 2 * calls['openInterest']]
        unusual_puts = puts[puts['volume'] > 2 * puts['openInterest']]
        
        return {
            'expiration': nearest,
            'put_call_ratio': pc_ratio,
            'total_call_volume': call_volume,
            'total_put_volume': put_volume,
            'unusual_calls': unusual_calls[['strike', 'volume', 'openInterest', 'impliedVolatility']].to_dict('records'),
            'unusual_puts': unusual_puts[['strike', 'volume', 'openInterest', 'impliedVolatility']].to_dict('records'),
            'current_price': stock.info.get('currentPrice', 'N/A')
        }
    except Exception as e:
        return f"Error fetching options data: {e}"
```

### Step 2E: Screening and Alerts

```python
def screen_stocks(tickers, min_volume=1000000, min_market_cap=1e9, max_pe=25):
    """Screen stocks based on basic criteria."""
    results = []
    
    for ticker in tickers:
        try:
            stock = yf.Ticker(ticker)
            info = stock.info
            
            volume = info.get('volume', 0)
            market_cap = info.get('marketCap', 0)
            pe_ratio = info.get('trailingPE', float('inf'))
            
            if volume >= min_volume and market_cap >= min_market_cap and pe_ratio <= max_pe:
                results.append({
                    'ticker': ticker,
                    'name': info.get('shortName', 'N/A'),
                    'price': info.get('currentPrice', 'N/A'),
                    'volume': volume,
                    'market_cap': market_cap,
                    'pe_ratio': pe_ratio,
                    'sector': info.get('sector', 'N/A')
                })
        except Exception:
            continue
    
    return pd.DataFrame(results)

def check_price_alerts(watchlist_df, current_prices):
    """Check watchlist for price alerts."""
    alerts = []
    
    for _, row in watchlist_df.iterrows():
        ticker = row['ticker']
        current = current_prices.get(ticker, {}).get('currentPrice')
        prev_close = current_prices.get(ticker, {}).get('previousClose')
        
        if current and prev_close:
            change_pct = (current - prev_close) / prev_close * 100
            
            if abs(change_pct) >= row.get('alert_threshold', 5):
                direction = "GAP UP" if change_pct > 0 else "GAP DOWN"
                alerts.append(f"{direction}: {ticker} {change_pct:+.1f}% (threshold: {row.get('alert_threshold', 5)}%)")
    
    return alerts
```

## Output Format

When presenting results:

### Market Data Summary
```
{ TICKER } — {Company Name}
   Price: ${price} ({change_pct:+.2f}%)
   Volume: {volume:,} ({vs_avg})
   Market Cap: ${market_cap:.1f}B
   P/E: {pe_ratio} | EPS: ${eps}
   52W Range: ${low} — ${high}
```

### Portfolio Analysis
```
Portfolio Summary
   Total Return: {return:.2f}%
   Annualized: {annualized:.2f}%
   Sharpe Ratio: {sharpe:.2f}
   Max Drawdown: {drawdown:.2f}%
   Volatility: {vol:.2f}%
```

### Technical Signals
```
Technical Signals for {TICKER}
   {signal_1}
   {signal_2}
   ...
   Current Price: ${price}
   RSI: {rsi:.1f} | MACD: {macd:.2f}
```

## Dependencies

- `yfinance` — Yahoo Finance data
- `pandas` — Data manipulation
- `numpy` — Numerical computations
- `requests` — HTTP requests for Sina Finance API

Install via: `pip install yfinance pandas numpy requests`

## Important Notes

- **Yahoo Finance** may have rate limits (~2000 requests/hour). Cache results when possible.
- **Sina Finance API** requires `Referer` header and may be blocked outside China. Use a proxy if needed.
- **A-Share tickers** use format `sh{code}` (Shanghai) or `sz{code}` (Shenzhen).
- **HK tickers** use format `hk{code}` for Sina API, or `{code}.HK` for Yahoo Finance.
- **Options data** availability depends on the exchange and may be delayed.
- Always validate data before making trading recommendations. This tool is for analysis only, not financial advice.
