# TTW 3.7.1: SNDK miss and early-alert review

Review date: 5 November 2026. Source baseline: main commit a3b5732e86a6b40fcb799ddf612b7f91b3c62513, running 3.7.0. All times below are UTC (also UK local time on this date).

## SNDK 4H: a recognised pattern blocked by level confluence

The user screenshot is SNDK/USDT perpetual, 4H, with a forming bullish C3 that began at 08:00. The screenshot still showed roughly 1h38 remaining. Being unclosed did not itself prevent an EARLY C3 alert.

The actual logged candidate is SNDKUSDT|4H|BULLISH|1793865600000, Binance Futures. The bot accepted its opposing approach at 08:00:21. At 09:20:04 it recorded a hypothetical entry at 1008.57, with lows 990.46, 994.33 and 997.14. Its C2 midpoint deviation was 0.0538957%, within the bounded policy. Live reversal movement was initially only 0.55 versus a required 1.644, so RECLAIM_TOO_SMALL was initially legitimate under the existing early-entry policy.

At 09:35:36 live reversal movement reached 1.83. The reason then became NO_ESTABLISHED_LEVEL. The bot continued alternating between those two temporary holds as price moved around the reclaim threshold. At 10:20:29 and 10:20:47 it again had enough reversal movement but no qualifying historical level. The missing-level gate, rather than malformed geometry or a closed-candle requirement, explains the screenshot's missed opportunity.

The drawn 985.42 support was not a configured manual bot input. Its derivation is not established by the screenshot, so this review does not claim that the bot had independently confirmed that exact level.

## Implemented correction

Intraday QCOM, SNDK, MU and USAR perpetuals now use historical levels as context, matching the existing Spot BTC exception. Their own opposing levels and target clearance are still evaluated. Reclaim, opposing approach, entry freshness, bounded wick alignment, body clearance, stop validity and touch-first chronology still apply. Metals and other crypto profiles are unchanged. No extra wick tolerance, waiting period, candle-close prerequisite or universal stronger-body gate is introduced.

Old 3.7.0 alerts remain compatible with close, outcome and follow-through updates. The saved exact exchange snapshot in fixtures/sndk_4h_20261105.json reproduces NO_ESTABLISHED_LEVEL with the old prerequisite and QUALITY_READY with the corrected profile. Its C3 is unfinished. The delivery regression injects an already-verified watch state and mocks Telegram: it establishes delivery eligibility, not a complete independent historical trade-sequence replay.

## Early-alert audit

The fixed cohort is the 26 delivered 3.7.0 alerts from 3 November 20:20 through 5 November 10:36:56, retrieved from TTW_AUDIT_V3 and matched to TTW_SENT. Outcome evidence was fetched around 10:42 on 5 November. Later signals and later outcomes are outside this cohort.

- Nineteen cohort C3s had closed: eleven CONFIRMED and eight INVALID. All eight invalid close records cite GEOMETRY_FAILED. Confirmation is shape classification, not a winning trade.
- All 26 saved alert-time trios pass the production bounded geometry when replayed. A later invalid shape must not be relabelled as malformed at entry using future highs/lows.
- Recorded SL1-to-TP1 paths: eleven STOP, one TP, one EXPIRED, three UNKNOWN and ten UNRESOLVED. The latter means no terminal outcome was retrieved for that path; it is not counted as a win or loss.
- Some results use conservative complete-candle recovery after stream gaps. UNKNOWN results remain unknown. This incomplete and strongly correlated sample does not establish a strategy win rate.
- Fourteen of the 26 are BTC alerts on overlapping timeframes. They are not fourteen independent market reversals.

Concrete failures:

| Alert | Entry | Recorded failure | Assessment |
| --- | ---: | --- | --- |
| FET 3H, 5 Nov 03:07:56 | 0.1634 | SL1 around 03:17:39, SL2 around 03:19:01; C3 geometry invalid at close | Small live rejection passed, then both stops failed in about 10–11 minutes. |
| SOL 2H, 5 Nov 06:02:47 | 95.69 | SL1 around 06:11:31; SL2 around 08:24:29; C3 geometry invalid at close | The initial reaction failed quickly; the secondary stop was reference-only on this alert. |
| ZEC 8H, 5 Nov 00:38:54 | 1291.57 | SL1 in the completed 01:26 minute, SL2 in the completed 02:59 minute; recovered after a stream gap | Both stop paths failed before TP1; exact intraminute ordering is not invented. |
| BTC 3H, 4 Nov 21:26:01 | 80100.01 | SL1 around 22:09:54, SL2 around 22:35:02; C3 geometry invalid at close | Not merely an altcoin issue. |

The trigger accepts a live colour reversal and the existing minimum reclaim before the C3 wick is fixed. A short-lived reaction can therefore produce an eligible early call and still lose. The current lesson exhaustion measurement also describes the live candle, not proof that the final candle or trade will succeed. A generic added colour/body rule would be redundant on this cohort: all delivered calls already had a reversal-colour body and passed that reclaim. A longer timer or a tighter wick cap cannot be justified as a demonstrated improvement by these outcomes alone.

The SNDK correction addresses a documented false rejection. It is not an early-alert quality fix or evidence of improved profitability. The failure cohort is retained here so future selection changes can be checked against actual failures and accepted examples, rather than advertised on synthetic tests alone.

## Cohort ledger

UNRESOLVED and UNKNOWN are excluded from any settled-result claims. C3 close verdict and stop/target outcome are separate columns deliberately.

| Alert time (UTC) | Market / timeframe | Entry | C3 close | SL1 → TP1 | SL2 → TP1 |
| --- | --- | ---: | --- | --- | --- |
| 11-03 20:20:10 | LINKUSDT 2H | 11.061 | CONFIRMED | STOP | STOP |
| 11-03 22:04:45 | BTCUSDT 3H | 77838.01 | CONFIRMED | TP | TP |
| 11-04 12:15:45 | BTCUSDT 16H | 78029.41 | INVALID | STOP | STOP |
| 11-04 18:37:48 | LTCUSDT 2H | 55.99 | CONFIRMED | STOP | EXPIRED |
| 11-04 21:26:01 | BTCUSDT 3H | 80100.01 | INVALID | STOP | STOP |
| 11-05 00:38:54 | ZECUSDT 8H | 1291.57 | INVALID | STOP | STOP |
| 11-05 00:54:25 | QNTUSDT 6H | 203.7 | INVALID | STOP | STOP |
| 11-05 00:56:09 | FETUSDT 8H | 0.1616 | INVALID | STOP | UNKNOWN |
| 11-05 00:57:27 | LTCUSDT 6H | 56.2 | CONFIRMED | UNKNOWN | UNKNOWN |
| 11-05 01:15:39 | BTCUSDT 6H | 80442.6 | CONFIRMED | UNKNOWN | UNKNOWN |
| 11-05 01:15:42 | BTCUSDT 8H | 80434.43 | CONFIRMED | UNKNOWN | UNKNOWN |
| 11-05 01:15:56 | SOLUSDT 8H | 95.48 | INVALID | STOP | UNKNOWN |
| 11-05 02:31:29 | BTCUSDT 2H | 80638.2 | CONFIRMED | EXPIRED | EXPIRED |
| 11-05 02:31:31 | BTCUSDT 10H | 80638.2 | DEVELOPING | UNRESOLVED | UNRESOLVED |
| 11-05 03:07:56 | FETUSDT 3H | 0.1634 | INVALID | STOP | STOP |
| 11-05 04:00:04 | BTCUSDT 3H | 80520 | CONFIRMED | UNRESOLVED | UNRESOLVED |
| 11-05 05:00:26 | BTCUSDT 4H | 80495.77 | CONFIRMED | UNRESOLVED | UNRESOLVED |
| 11-05 06:02:47 | SOLUSDT 2H | 95.69 | INVALID | STOP | STOP |
| 11-05 07:18:33 | BTCUSDT 5H | 80627.01 | DEVELOPING | UNRESOLVED | UNRESOLVED |
| 11-05 07:31:49 | LINKUSDT 2H | 11.494 | CONFIRMED | STOP | STOP |
| 11-05 07:56:09 | BTCUSDT 3H | 80362.56 | CONFIRMED | UNRESOLVED | UNRESOLVED |
| 11-05 07:56:18 | BTCUSDT 6H | 80311.12 | DEVELOPING | UNRESOLVED | UNRESOLVED |
| 11-05 09:14:47 | BTCUSDT 4H | 80319.99 | DEVELOPING | UNRESOLVED | UNRESOLVED |
| 11-05 09:25:09 | BTCUSDT 8H | 80308.35 | DEVELOPING | UNRESOLVED | UNRESOLVED |
| 11-05 10:20:10 | SOLUSDT 3H | 96.75 | DEVELOPING | UNRESOLVED | UNRESOLVED |
| 11-05 10:36:56 | LTCUSDT 5H | 55.88 | DEVELOPING | UNRESOLVED | UNRESOLVED |

## Validation

The unmodified baseline passed 374 tests. The corrected suite passes 378 tests, including recorded SNDK eligibility with real quality checks and mocked delivery, policy boundaries, preserved reclaim/risk/target vetoes, unverified-touch rejection, original QCOM eligibility, and all four previously rejected excessive BTC/SNDK contacts. No live Telegram messages are sent by tests.
