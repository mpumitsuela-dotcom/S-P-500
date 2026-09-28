# Multi-Market Trading Guide

## Ticker Formats

| Market | Yahoo Finance | Sina Finance | Example |
|--------|--------------|--------------|---------|
| US | `AAPL` | N/A | Apple Inc |
| HK | `0700.HK` | `hk00700` | Tencent |
| A-Share (Shanghai) | `600519.SS` | `sh600519` | Kweichow Moutai |
| A-Share (Shenzhen) | `000858.SZ` | `sz000858` | Wuliangye |

## Market Hours

| Market | Local Time | UTC | Key Sessions |
|--------|-----------|-----|-------------|
| US (NYSE/NASDAQ) | 9:30-16:00 ET | 14:30-21:00 | Pre-market: 4:00-9:30 ET |
| HK (HKEX) | 9:30-16:00 HKT | 1:30-8:00 | Morning: 9:30-12:00, Afternoon: 13:00-16:00 |
| A-Share (SSE/SZSE) | 9:30-15:00 CST | 1:30-7:00 | Morning: 9:30-11:30, Afternoon: 13:00-15:00 |

## Data Source Reliability

- **Yahoo Finance**: Best for US stocks, decent for HK, limited for A-Shares
- **Sina Finance**: Best for A-Shares and HK, requires proxy outside China
- **AKShare**: Comprehensive China market data (pip install akshare)
- **East Money**: Alternative China data source

## Common Pitfalls

1. **Currency conversion**: HK stocks in HKD, A-Shares in CNY, US in USD
2. **Trading holidays**: Each market has different holiday schedules
3. **Settlement cycles**: US=T+1, HK=T+2, A-Share=T+1
4. **Lot sizes**: HK stocks trade in lots (100-2000 shares), A-Shares in 100-share lots
