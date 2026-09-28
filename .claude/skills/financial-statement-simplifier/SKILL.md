---
name: financial-statement-simplifier
version: "1.0.0"
summary: "Turn dense financial statements into plain-language summaries with key ratios and risk flags."
tags: [finance, accounting, statements, ratios, analysis]
description: "Translate income statements, balance sheets, and cash flow statements into plain-language business narratives with key ratios and 3-5 bullet insights. Use when a user pastes financial statements or asks what a company's numbers mean, whether margins are healthy, or how to explain results to non-finance stakeholders."
_agensi: "34b09804-38f5-4ad6-8844-43cc32ce1cbe"
---

# Financial Statement Simplifier

Read raw financial statements and produce a plain-language readout: what happened, why it matters, and the numbers that support it.

## When to Use

- User pastes an income statement / balance sheet / cash flow (CSV, table, PDF extract) 
- "Is this company profitable?" "Are margins good?" "Explain the cash burn"
- Prepping meetings, internal memos, or investment summary notes
- User wants ratios computed and contextualized

Do not use for: full DCF/valuation modeling, tax/legal advice, or audit-grade verification of disclosed figures (treat inputs as given).

## Steps

1. **Normalize** — convert statement(s) into a clean table: line items as rows, periods as columns (period labels kept verbatim; mark currency and unit). Flag missing periods / restated numbers.
2. **Compute the core set** (from whatever is available):
   - Income: Revenue, Gross margin %, Operating margin %, Net margin %, YoY/QoQ growth on revenue and net income
   - Balance: Current ratio = `current assets ÷ current liabilities`; Debt-to-equity; Working capital; Days of receivables if data available
   - Cash flow: `Operating cash flow − Capex = Free cash flow`; check sign of FCF and whether net income aligns with OCF (quality-of-earnings: `OCF ÷ Net income`)
3. **Spot the story** — compare margins trend across periods, note one-off items (mark "*one-off"), identify where cash goes vs profit.
4. **Flag risk lines** with severity:
   - ⚠️ Revenue up but receivables growing faster → collection risk
   - ⚠️ OCF much lower than net income → accrual gap
   - ⚠️ Negative equity / tightening liquidity ratios
5. **Write the memo**:

```markdown
# Financial Read — {Company} ({periods})
**TL;DR:** {2 sentences}
## The story
{3 paragraphs max: growth → margins → cash}
## Key numbers
| Metric | {P1} | {P2} | Trend |
## Flags
- [ ] {each risk line + why}
## Notes
{assumptions, currency, one-off items}
```

6. Keep it readable by a smart non-accountant; show the numbers you cite.

## Reference

| Ratio | Formula | Healthy sign |
|---|---|---|
| Gross margin | (Rev − COGS) ÷ Rev | stable/increasing |
| Operating margin | EBIT ÷ Rev | covers SG&A sustainably |
| Current ratio | CA ÷ CL | 1–2 typical |
| FCF | OCF − Capex | positive & not just due to working capital |

## Pitfall
Periods must be aligned (e.g. quarterly vs annual). Never mix fiscal year-end and quarter figures in the same trend without labeling.