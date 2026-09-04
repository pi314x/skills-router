---
name: triage-alert
description: A coin is scoring high on the live radar right now — decide whether real accumulation is forming or it is a single-signal false alarm.
keywords: alert, flag, triage, hot, score, threshold, false alarm, noise, accumulation, orderbook, imbalance, taker, rush, spike, firing, confirm, right now, live
tools: get_radar, get_pressure_flags, get_weights, get_regime, get_klines
---

Most flags are noise. The job is to reject fast and cheaply, not to find a reason
to believe. Work in this order and stop at the first step that rejects.

1. **Read the board, not the coin.** `get_radar` returns every watched symbol with
   its composite score and the alert threshold in force. The score is only
   meaningful *cross-sectionally* — a 5.1 when the board's median is 4.8 is a quiet
   market, not a signal. Rank first; look at absolute numbers second.

2. **Count signal families, not signals.** Each row carries Z-scores from three
   independent families: order-book imbalance (`obiZ`), taker/rush pressure
   (`rushZ`), and volume (`volZ`). One family spiking is how a large single order
   looks. Two or more moving together is what accumulation looks like. A row above
   threshold on one family only is a reject, whatever its composite score.

3. **Check what the threshold is made of.** `get_weights` returns the learned
   scorer (`learn_pressure.py --write-weights`) with its AUC and `suggested_alert`.
   If the weights file is absent the radar is running on hand-set defaults and the
   score is much weaker evidence — say so rather than quoting it as if learned.

4. **Reinforcers, not deciders.** `pumpedtimes` (Xu & Livshits: repeat targets) and
   `vol_peak_ratio` (Nakhli V/Vmax: pre-accumulated vs fresh) adjust confidence in a
   candidate that already passed step 2. Neither promotes a single-family flag.

5. **Base rate.** `get_pressure_flags(outcome='confirmed')` versus the full log gives
   the hit rate this detector actually achieves. Quote it next to any call you make.
   `get_regime` says whether pumps are currently elevated (high BTC volatility,
   Sunday) — context for the prior, never a signal on its own.

6. **Look at the tape last.** `get_klines(symbol, '1m', 90)` confirms the pressure is
   visible in price/volume. Doing this first invites you to fit a story to a chart.

Output: a ranked shortlist, each with score, which families corroborate, the
reinforcers, and an explicit **watch** or **reject**. Never invent a symbol that is
not in the radar payload. Detection is not an edge — end with what to watch, not
what to buy.
