---
name: tune-detector
description: Decide whether the radar's learned ranking and alert threshold should be retrained, and what the current weights say drives a real pump.
keywords: tune, tuning, retrain, train, weights, threshold, calibrate, calibration, learn, model, auc, feature, importance, drift, adjust, sensitivity, too many alerts, too few
tools: get_weights, get_pressure_flags, get_overview, get_pumps
---

The radar ranks flags with a logistic scorer learned from its own outcome log
(`learn_pressure.py --write-weights` → `pressure_weights.json`, auto-loaded at
start). This skill decides whether that model has earned a retrain — it does not
hand-tune the threshold.

1. **Read the current model honestly.** `get_weights` gives the coefficients, AUC and
   `suggested_alert`. An AUC near 0.5 means the scorer is not separating confirmed
   from expired flags, and no threshold choice rescues that — report it as a data
   problem, not a tuning knob.

2. **Watch for the z_version mismatch.** Weights trained under v1 (mean/std) applied
   to a v2 (log1p + median/MAD) feature pipeline are silently wrong. `get_weights`
   flags this. Never reason about coefficients across that boundary.

3. **Retrain on volume, not on vibes.** `get_pressure_flags` and `get_pumps` say how
   many *resolved* flags exist since the last fit. A retrain on a handful of new
   outcomes moves the threshold on noise. The trigger is a materially larger log or a
   confirmed regime change — not a bad week.

4. **Both directions cost something.** Raising the threshold cuts false alarms and
   lead time together; lowering it buys candidates and buries them. Frame any
   proposed change as that trade, quantified from the log's hit rate, and never
   propose a number derived from one recent event (`pump-postmortem` covers why).

5. **Feature order is a finding.** `rushZ` leading, volume next, is what the
   literature predicts; a fit that reverses it is a signal something changed in the
   data, and is worth investigating before it is worth shipping.

Output: whether to retrain and why, what the current weights say drives confirmation,
and the explicit alerts-versus-lead-time trade for any threshold change proposed.
The actual retrain is a command for the operator to run, not something to assert has
happened.
