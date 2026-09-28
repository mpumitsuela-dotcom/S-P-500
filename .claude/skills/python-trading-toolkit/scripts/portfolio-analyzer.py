#!/usr/bin/env python3
"""
Portfolio Analyzer — Quick portfolio performance analysis script.

Usage: python portfolio-analyzer.py positions.csv

positions.csv format:
ticker,shares,cost_basis,current_price
AAPL,100,150.00,175.50
MSFT,50,300.00,380.25
GOOGL,20,140.00,175.00
"""

import sys
import pandas as pd
import numpy as np

def analyze_portfolio(csv_path):
    df = pd.read_csv(csv_path)
    
    # Calculate position values
    df['cost_value'] = df['shares'] * df['cost_basis']
    df['current_value'] = df['shares'] * df['current_price']
    df['pnl'] = df['current_value'] - df['cost_value']
    df['pnl_pct'] = df['pnl'] / df['cost_value'] * 100
    df['weight'] = df['current_value'] / df['current_value'].sum() * 100
    
    total_cost = df['cost_value'].sum()
    total_current = df['current_value'].sum()
    total_pnl = total_current - total_cost
    total_pnl_pct = total_pnl / total_cost * 100
    
    print(f"\n{'='*60}")
    print(f"  PORTFOLIO ANALYSIS")
    print(f"{'='*60}")
    print(f"  Total Cost Basis:  ${total_cost:>12,.2f}")
    print(f"  Current Value:     ${total_current:>12,.2f}")
    print(f"  Total P&L:         ${total_pnl:>12,.2f} ({total_pnl_pct:+.2f}%)")
    print(f"{'='*60}\n")
    
    print(f"{'Ticker':<8} {'Shares':>6} {'Cost':>10} {'Current':>10} {'P&L':>10} {'P&L%':>7} {'Weight':>7}")
    print(f"{'-'*8} {'-'*6} {'-'*10} {'-'*10} {'-'*10} {'-'*7} {'-'*7}")
    
    for _, row in df.iterrows():
        print(f"{row['ticker']:<8} {row['shares']:>6} ${row['cost_basis']:>9,.2f} ${row['current_price']:>9,.2f} ${row['pnl']:>9,.2f} {row['pnl_pct']:>+6.2f}% {row['weight']:>6.1f}%")
    
    print(f"\n  Top Position: {df.loc[df['weight'].idxmax(), 'ticker']} ({df['weight'].max():.1f}%)")
    print(f"  Worst Performer: {df.loc[df['pnl_pct'].idxmin(), 'ticker']} ({df.loc[df['pnl_pct'].idxmin(), 'pnl_pct']:+.2f}%)")
    print(f"  Best Performer: {df.loc[df['pnl_pct'].idxmax(), 'ticker']} ({df.loc[df['pnl_pct'].idxmax(), 'pnl_pct']:+.2f}%)")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python portfolio-analyzer.py positions.csv")
        print("\nCSV format: ticker,shares,cost_basis,current_price")
        sys.exit(1)
    
    analyze_portfolio(sys.argv[1])
