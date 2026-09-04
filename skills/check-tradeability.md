---
name: check-tradeability
description: Someone wants to act on a signal — apply the honest-edge gate and the paper-only sizing before any talk of entering.
keywords: trade, trading, buy, sell, enter, entry, position, size, sizing, risk, stop, stoploss, take profit, profit, equity, edge, worth, should i, backtest, slippage, profitable, money, live
tools: advise_trade, get_radar, get_weights, get_overview, get_klines
---

Start from the project's own finding, not from the request: the walk-forward
backtest shows a **marginal** edge (PF ~1.1–1.27), concentrated in *liquid* coins —
which are not the micro-caps this toolkit watches — and the strongest known
predictor (the Telegram announcement trigger) is deliberately omitted. The honest
default answer to "should I trade this" is no.

1. **Say the gate out loud first.** Any output of this skill leads with the PF range
   and the liquid-vs-micro-cap mismatch. A number produced without that framing will
   be read as a recommendation.

2. **Detection quality is not edge.** `get_overview` and `get_weights` give hit rate,
   AUC and lead time. A detector can be genuinely good at spotting accumulation and
   still lose money after slippage — the flag is upstream of the fill. Never carry a
   high hit rate over into a claim about profitability.

3. **Size only after that.** `advise_trade(symbol, equity)` returns a
   fixed-fractional (1% risk) depth-capped size with stop-loss and take-profit from
   the frozen triple-barrier config. Explain *why* the size is small on a micro-cap:
   the depth cap, not conservatism — the book cannot absorb more without the
   slippage that erases the edge.

4. **Everything here is paper.** `advise_trade` places no orders. Every executor in
   the toolkit defaults to paper and needs an explicit `--live` flag plus the
   operator's own API key; `paper_trader.py` has no `--live` at all. Do not describe
   a live path unless asked, and never present one as the next step.

5. **The ethical fact belongs in the answer.** Profiting from a pump means selling
   into the dump — onto the retail latecomers this research exists to protect. State
   it plainly, once, without lecturing.

Output: the gate, then the honest detection numbers, then the paper sizing if it was
asked for. No forecasts, no expected value, no financial advice.
