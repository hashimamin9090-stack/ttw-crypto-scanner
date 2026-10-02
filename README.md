# TTW crypto scanner 2.1

Telegram alerts only. The scanner never places trades.

The third candle must touch the wick line before its reversal impulse. The
line is projected from the two closed wick tips and stays fixed. A near touch
uses the existing 0.05% midpoint tolerance; a body intersection is invalid.
All six hypothetical shapes approved by the user are regression fixtures.

The scanner reconstructs candle 3 from its actual UTC start, first using hourly
bars, then refining possible touch/impulse events to minute and second bars.
It rejects a prior impulse, unresolved ordering inside a second, missing data,
old first touches, and setups that already impulsed after the touch and returned.
Restarting cannot turn a historical touch into a fresh alert.

Alerts are provisional at the first fresh touch. Candle 3 can still be red for
a bullish alert or green for a bearish alert at that instant. At the close,
the original Telegram message is updated with a confirmed/invalid verdict based
on candle colour, wick tolerance and body clearance against the fixed line.

Starting calibration (inferred from examples, not independently backtested):

| Environment variable | Default | Meaning |
| --- | --- | --- |
| MAX_OPEN_TO_TOUCH_PCT | 1.0 | Maximum percentage gap from C3 open to projected touch |
| IMPULSE_RANGE_FRACTION | 0.5 | Minimum reversal excursion as a fraction of C2 true range |
| MAX_ALERT_DELAY_SECONDS | 90 | Maximum age of the first touch |
| MAX_ENTRY_MOVE_FRACTION | 0.25 | Maximum current displacement from touch, as a fraction of impulse distance |

True range is max(C2 high-low, abs(C2 high-C1 close), abs(C2 low-C1 close)).
Before the touch, bullish excursion is measured up from C3's running low;
bearish excursion down from its running high. The impulse floor is four times
the touch-zone tolerance, or 0.01% of C3 open, whichever is larger. This avoids
treating numerical tolerance as an impulse. Small fluctuations remain allowed.

Precision and timing: second bars still cannot prove every trade's order. If
both possible events occupy the same second and the open does not establish
touch first, skip the setup. The existing polling target defaults to 30 seconds;
this is not a tick-by-tick instantaneous alert guarantee. Rapid moves between
polls can be skipped to avoid a late entry. Higher timeframes retain their
existing aggregation alignment; current candle history can span up to 5000 hours.

Required existing settings: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID. Other
existing settings, chart images, feedback buttons and symbol configuration are
retained. RLUSD is excluded and TAO is retained. Never put credentials in source.
Local state retains schema version 2 and adds sequence rejection records; old
alerts and feedback are not discarded by the migration. Render's existing
STATE_DIR durability is unchanged.

Run the regression suite: `python -m unittest -v test_ttw_touch_first.py`.
Run the bot: `python TTW_BOT_V2.py`.

Market-data API reference:
https://github.com/binance/binance-spot-api-docs/blob/master/rest-api.md#klinecandlestick-data
