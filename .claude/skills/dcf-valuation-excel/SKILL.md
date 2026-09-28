---
name: dcf-valuation-excel
version: "1.0.0"
summary: "Build an institutional-quality DCF valuation in Excel: project revenue, build free cash flow from EBIT through NOPAT, D&A, capex and working capital, compute WA"
tags: [finance, dcf, valuation, excel, modeling]
description: "Build an institutional-quality DCF valuation in Excel: project revenue, build free cash flow from EBIT through NOPAT, D&A, capex and working capital, compute WACC, terminal value, and a sensitivity table. Use when you need a fundamental intrinsic valuation."
_agensi: "1749d6f7-109c-4326-aedf-c5aedf733175"
---

# DCF Valuation in Excel

## When to Use

Use when the question is "what is this business worth on its own cash flows, not on what peers trade at": M&A support, investment memos, internal budgeting against a valuation target, or stress-testing a purchase price. A DCF forces explicit assumptions — which is exactly its value.

Do not use for: early-stage companies with no revenue trajectory to project, highly cyclical businesses whose FCF cannot be anchored, or when you need a quick relative check — use trading comps instead.

## Procedure

1. Set up the workbook: sheets `Assumptions`, `Projections`, `FCF`, `WACC`, `Valuation`, `Sensitivity`. Link everything with cell references; hardcode nothing twice.
2. Project revenue for 5 years: start with the latest full-year revenue, apply yearly growth rates from the Assumptions sheet (e.g. 12%, 10%, 8%, 6%, 5%).
3. Project margin line: EBIT = Revenue × assumed EBIT margin per year; keep explicit margin drivers (gross margin, opex %) in Assumptions.
4. Build the FCF bridge per year in the FCF sheet: `NOPAT = EBIT * (1 - TaxRate)`, then `FCF = NOPAT + D&A - Capex - IncreaseInNWC`. D&A and capex as % of revenue; NWC as % of revenue change year over year.
5. Compute WACC in its sheet: `Ke = rf + Beta * ERP` (CAPM), `Kd = interest rate * (1 - TaxRate)`, then `WACC = E/(E+D)*Ke + D/(E+D)*Kd`. Keep target capital structure in Assumptions.
6. Terminal value with Gordon growth: `TV = FCF_Year5 * (1 + g) / (WACC - g)`. Use a conservative g (0-3%) and confirm WACC − g stays visibly positive.
7. Discount to present: PV of each year's FCF with `=NPV(WACC, FCF_Year1:FCF_Year5)`; discount TV separately: `=TV / (1+WACC)^5`. Enterprise value = both sums.
8. Bridge to equity and per share: `Equity = EV - NetDebt (+/- minority interest, excess cash)`, `PerShare = Equity / DilutedShares`. Build the sensitivity table: rows = WACC (e.g. 7-11%), columns = terminal growth g (0-3%), each cell `PerShare` recalculated via a two-variable data table (`Data > What-If > Data Table`).

## Reference

| FCF bridge line | Formula | Driver |
|---|---|---|
| NOPAT | `=EBIT*(1-TaxRate)` | tax rate |
| + D&A | `=Rev*DA_pct` | % of revenue |
| − Capex | `=Rev*Capex_pct` | % of revenue |
| − ΔNWC | `=(NWC_pct*Rev_t)-(NWC_pct*Rev_(t-1))` | % of revenue |
| FCF | sum of above | — |

Illustrative WACC: rf 4.0% + β 1.1 × ERP 5.5% → Ke 10.05%; Kd 5.0% × (1−20%) → 4.0%; 70/30 equity/debt → WACC ≈ 8.2%.

Sensitivity cell example: `=Valuation!PerShare` is the data-table formula cell; row input = WACC cell, column input = g cell.

## Verification Checklist

- [ ] Every projection cell points to Assumptions (no stray hardcoded numbers)
- [ ] FCF bridge balances: NOPAT + D&A − Capex − ΔNWC equals FCF row
- [ ] WACC − g > 0 and WACC sits in a defensible 7-11% range
- [ ] NPV and discounted-TV cells use the same WACC — no mixing
- [ ] Sensitivity table recomputes when a row/column input changes