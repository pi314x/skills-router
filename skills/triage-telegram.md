---
name: triage-telegram
description: A Telegram or chat message may be pump coordination — classify its phase, extract the target coin and exchange, and turn it into a watch instruction.
keywords: telegram, message, chat, text, announcement, countdown, coordination, group, channel, organiser, organizer, extract, ticker, phase, pumpsense, signal message, posted, screenshot
tools: detect_pump_text, get_universe, get_radar, get_klines
---

The message layer is the earliest possible signal — it reads the organisers' own
words before any price moves (Mahrous & Di Pietro, PumpSense). It is also the
easiest to be fooled by, because tickers are ambiguous words.

1. **Run the cascade, don't eyeball it.** `detect_pump_text(message)` applies the
   cheap rule pre-filter and then, only if `PD_USE_AI=true`, the LLM extractor. With
   AI off it returns a rule flag and no extraction — report that honestly instead of
   guessing the coin yourself. Regex/dictionary extraction scores ~0% on crypto
   tickers; that is exactly why the extractor stage exists.

2. **Phase drives urgency.** `announcement` and `countdown` mean a pump is scheduled
   but the target is still hidden — the useful action is to widen the watchlist, not
   to name a coin. `target_release` is the only phase that yields a symbol.
   `results` is bragging after the fact; `cancellation` and `noise` end the trail.

3. **Never trust an extracted ticker on its own.** Cross-check it against
   `get_universe`: a symbol that is not a tradeable low-cap pair on the named
   exchange is almost always the extractor latching onto an ordinary English word.
   Report the mismatch rather than resolving it in the coin's favour.

4. **Confirm on the tape.** With a plausible symbol, `get_radar` and
   `get_klines(symbol, '1m', 90)` say whether pressure is already building. Text
   plus corroborating order-book pressure is the strongest combination this toolkit
   produces; text alone is a hypothesis.

Scope: this analyses messages the operator already has. It does not join groups,
scrape channels, or subscribe to anything — and the output is a watch instruction
for the radar, never a trade. Detecting a scheme early is for getting out of its
way, not for getting in front of it.
