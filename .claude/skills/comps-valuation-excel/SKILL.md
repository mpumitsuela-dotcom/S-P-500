---
name: comps-valuation-excel
version: "1.0.0"
summary: "Build a comparable-company (trading comps) analysis in Excel: pick a peer set, gather operating metrics and valuation multiples like EV/EBITDA and P/E, bucket b"
tags: [finance, valuation, excel, comps, modeling]
description: "Build a comparable-company (trading comps) analysis in Excel: pick a peer set, gather operating metrics and valuation multiples like EV/EBITDA and P/E, bucket by size, and construct a comps table with statistical summary."
_agensi: "4ed10bba-764e-4cef-826f-52b392d4aaba"
---

# Comps Valuation in Excel

## When to Use

Use when you need a market-based valuation of a company by benchmarking against its public peers: "what multiple should our target trade at?" Typical jobs — buy-side screens, fairness opinions, pitch book support, or sanity-checking a DCF. The deliverable is one Excel workbook with a clean comps table.

Do not use for: companies with no credible public peers (use a DCF or precedent transactions), trailing-twelve-month comparisons when the target's financials are stale, or precise intrinsic valuation — comps are relative, not fundamental.

## Procedure

1. Fix the peer set: pick 6-10 public companies in the same industry and similar business mix. Document the selection logic in a "Notes" sheet; every peer needs a one-line reason.
2. Collect per-peer inputs in one raw sheet: columns for share price, shares outstanding, net debt (total debt − cash), and the operating metrics (revenue, EBITDA, net income, book value, EPS).
3. Compute market cap: `=Price * Shares`. Then enterprise value: `=MarketCap + TotalDebt - Cash`.
4. Calculate multiples per peer, one column each: `=EV / EBITDA`, `=EV / Revenue`, `=Price / EPS` (P/E), `=Price / BookPerShare` (P/B). Use TTM figures consistently — mixing FY and TTM breaks the table.
5. Add size buckets as a categorical column: classify by market cap, e.g. Mega ≥ $50B, Large $10-50B, Mid $2-10B, Small < $2B, using `=IFS(...)`.
6. Build the comps table: rows = peers, columns = the four multiples plus size bucket. Below the rows add the statistical summary block.
7. Summary statistics block with formulas: Mean `=AVERAGE(range)`, Median `=MEDIAN(range)`, Min `=MIN(range)`, Max `=MAX(range)`, and quartiles `=QUARTILE.INC(range,1)` and `=QUARTILE.INC(range,3)`. Highlight the median row — it is the standard anchor.
8. Derive an implied value range for the target: apply the median multiple to the target's own metric, e.g. `=MedianEVEBITDA * TargetEBITDA` for EV, then `=ImpliedEV - NetDebt` and `= / Shares` for a per-share range. State the range as Low (25th percentile) to High (75th percentile).

## Reference

| Multiples | Formula (per peer) | Typical range |
|---|---|---|
| EV/EBITDA | `=EV/EBITDA` | 6-15x |
| EV/Revenue | `=EV/Revenue` | 1-8x (varies wildly) |
| P/E | `=Price/EPS` | 10-30x |
| P/B | `=Price/BookPerShare` | 1-5x |

Footnote rules: state the as-of date for prices, the source for net debt, and any one-off items stripped from EBITDA. If one peer's multiple is a clear outlier (>2x the median), flag it rather than silently deleting.

Implied value example (illustrative): median EV/EBITDA 9.1x, target EBITDA $120M → EV ≈ $1,092M; less net debt $140M → equity ≈ $952M; at 40M shares ≈ $23.80/share, versus size-bucket median 8.4x ≈ $21.70/share. Report both.

## Verification Checklist

- [ ] Peer set has 6-10 companies with a documented selection reason each
- [ ] EV = MarketCap + TotalDebt − Cash for every peer, no hardcoded EV
- [ ] All multiples use consistent TTM figures from the same source date
- [ ] Median/mean/min/max formulas cover the exact peer range (no gaps)
- [ ] Implied per-share range sits between the 25th and 75th percentile values