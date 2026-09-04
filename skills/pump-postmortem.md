---
name: pump-postmortem
description: A pump has been confirmed — measure whether the detector saw it coming, how much warning it gave, and what it missed.
keywords: postmortem, post mortem, after, happened, missed, foreseen, lead, warning, review, hit rate, confirmed, history, detected, did we, retrospective, replay, catch, why
tools: get_pumps, get_pressure_flags, replay_detector, get_klines, get_overview
---

The point of this skill is honest scoring. The failure mode is retrofitting a story
in which the detector performed better than it did.

1. **Take the recorded verdict.** `get_pumps` rows already carry `foreseen` (did the
   radar pre-flag inside the window?) and `lead_seconds`. Use those. Re-deriving
   foresight by eye from a chart after the fact is how leakage gets in.

2. **Lead time is the metric that matters.** A confirmation with two seconds of lead
   is a detection in name only. Report the distribution across recent pumps
   (`get_overview` gives the aggregate foreseen % and average lead), not just this
   one.

3. **Reconstruct the near-miss.** `get_pressure_flags` around the event shows what
   the radar was seeing. A flag that fired and expired, or one just under threshold,
   is far more informative than the confirmation itself — it is where the threshold
   is actually costing you.

4. **Replay is causal, use it.** `replay_detector(symbol, days)` re-runs the 1-minute
   price+volume detector bar-by-bar over that symbol's own history, with no lookahead.
   It covers `pump_monitor.py`'s layer only — the order-book and rush signals have no
   historical source, so a replay miss does **not** prove the live radar would have
   missed it. Say which layer you are scoring.

5. **One event is an anecdote.** Any conclusion about detector quality needs the
   population from `get_overview`, not this single row. Resist tuning a threshold to
   catch the specific pump you just looked at — that is fitting the sample.

Output: foreseen yes/no with lead time, the near-miss flags around it, which layer
was scored, and how this event sits against the base rate. If the detector missed
it, say so first and plainly.
