---
name: hunt-targets
description: Work out which low-cap coins are the likeliest pump targets over the next day, and refresh the watchlist the radar runs on.
keywords: target, predict, next, tomorrow, 24h, watchlist, universe, candidate, lowcap, low cap, marketcap, mcap, scan, rank, likeliest, who, which coin, shortlist, ahead
tools: get_predicted_targets, get_universe, scan_universe, get_regime, get_overview
---

This is the day-ahead layer (Xu & Livshits target prediction), not the live one.
It answers "who is worth watching tomorrow", never "who is pumping now".

1. **Start from the ranking, not the universe.** `get_predicted_targets` is the
   walk-forward-evaluated P(pump episode ≤ 24h) per coin, best first. If it is empty
   or stale, the ranking has not been rebuilt — say so and point at
   `predict_targets.py --write` rather than substituting a raw universe scan.

2. **Check the pool it ranked over.** `get_universe` gives the current low-cap
   selection and tier breakdown. `scan_universe` rebuilds it live when the file is
   stale. The targeting filter is the finding, not a preference: low market cap
   (0–60M), low volume, few exchanges — Bolz et al. and Charfeddine & Mahrous both
   land there. A high-ranked coin that has drifted out of that band is a data
   problem, not a discovery.

3. **Probabilities are relative.** These are ranks. A 0.08 at the top of the list is
   the best available candidate, not an 8% promise. Present positions and gaps
   between them; refuse to convert them into expected returns.

4. **Regime scales the whole list.** `get_regime` reports elevated-vs-normal pump
   frequency. It moves every candidate together — it never re-orders them.

5. **Hand off.** The output of this skill is a watchlist for the radar
   (`predicted_targets.txt` → `pump_radar.py --symbols`). Real-time judgement about
   any single name on it belongs to `triage-alert`, after a flag actually fires.

Output: the top candidates with their ranks and why the pool contains them, plus a
one-line statement of how fresh the ranking is. No entries, no sizes, no targets —
a coin being a likely *target* says nothing about it being a good *trade*.
