# CQD Architecture — Verified Knowledge Bank

Condensed from a direct read of the repo at `/opt/data/cqd-trading-bot` (session:
produce plain-English fact-sheet). Use for audits/doc tasks; re-verify against
current code before relying on specific numbers.

## Flow (no live trading)

```
cqd_trigger.sh (every 15m)
  └─ quant_evaluator.py  (per watchlist pair)
        fetch OHLCV (ccxt, Binance, read-only)
        indicators: RSI, MACD, EMA 9/21/50, Bollinger, ATR, vol, MFI, CMF, VWAP, CVD
        volatility_clustering() -> regime
        cashflow_analysis()    -> cvd/mfi/cmf/vwap bias
        fetch_sentiment()      -> Fear&Greed (alt.me) + BTC dom/mktcap (CoinGecko, 2h cache)
        generate_signal()      -> conviction 1-10 + direction
        write /tmp/cqd_trigger_<PAIR>.json
        log SCAN row -> logs/cqd_master_log.csv
     conviction >= 7? -> sandbox_engine.py --execute <payload>

sandbox_engine.py --execute
  read payload (action=="EXECUTE" else veto)
  load_wallet(); enforce global_max_open_positions (15 in config; 5 is code default)
  size = clamp(balance * default_position_size_pct[2.5%], min[50], max[500])
  SL/TP from ATR x 1.5 / x 3.0 (fixed 2% / 5% fallbacks)
  atomic wallet txn (fcntl flock reentrant) -> wallet_state.json
  send_telegram_alert(entry ticket) -> tg_sent_log.csv

cqd_monitor.sh (every 5m) -> sandbox_engine.py --monitor
  non-blocking flock on state/cqd_monitor.lock (prevents dup exit)
  for each open pos: fetch price; long: price<=sl->STOP_LOSS, price>=tp->TAKE_PROFIT
  (inverse for short). On hit: pnl = size * price_diff_pct; balance += size + pnl;
  append trade_history; log EXIT; send exit ticket. Idempotency skips closed pairs.

cqd_rotator.sh (daily 04:00 UTC) -> rotate_watchlist.py
  5 fixed core: BTC,ETH,BNB,SOL,ADA. 5 satellites ranked by log-volume(0.35-0.40)
  + 24h-range-volatility(0.35-0.40) + momentum(0.20-0.30); fear-gated scoring.
  Writes config/watchlist.json (10 pairs).

cqd_watchdog.sh (every 10m) -> cqd_watchdog.py
  if master log stale >600s -> Telegram LIVENESS ALERT (CQD's own token).
  self_heal_cron_pins() re-pins drift-blocked cqd_* jobs to tencent/hy3:free / openrouter.
  _detect_drift_blocks() -> SCHEDULER ALERT if blocks remain.
```

## Files of interest
- `core/quant_evaluator.py` — indicators + signal (877 lines). No LLM imports.
- `core/sandbox_engine.py` — execute/monitor, wallet I/O, TG alerts (658 lines).
- `core/cqd_logger.py` — zero-token CSV logger to `logs/cqd_master_log.csv`.
- `core/cqd_watchdog.py` — liveness + cron self-heal.
- `core/rotate_watchlist.py` — daily 10-pair selector.
- `state/wallet_state.json` — `{balance_usdt, open_positions{}, trade_history[]}`.
  Starts at 10000 USDT (sandbox). Default on missing/corrupt = 10000.
- `state/tg_sent_log.csv` — `ts,event_class,pair,snippet` (ENTRY/EXIT/TEST/OTHER).
- `state/macro_cache.json` — FGI/BTC-dom cache, 2h TTL.
- `.env.template` — `CQD_TG_BOT_TOKEN`, `CQD_TG_CHAT_ID`, optional exchange/timeframe/balance.

## Fail-fast isolation (proven, do not weaken)
`cqd_logger.py`, `sandbox_engine.py`, `cqd_watchdog.py` all raise RuntimeError at
import if global `TG_BOT_TOKEN`/`TG_CHAT_ID` present. Wrappers `unset TG_BOT_TOKEN
TG_CHAT_ID` before invoking Python. Sandbox reads only `CQD_TG_*`.

## Honest-limitations baseline (as of this session) — VERIFY before reusing
- Simulation-only; no exchange keys; exchange use is read-only.
- NOT backtested (issue #11 P0 open).
- conviction>=7 gate is in the wrapper, not the engine.
- Trailing-stop params in config.json are unimplemented (fixed ATR SL/TP only).
- No cqd_position_tracker.py; state in sandbox_engine + wallet_state.json.
- Hard-wired to /opt/data/cqd-trading-bot, /opt/data/cqd_venv/bin/python, Binance.
- Track record tiny; trade_history may be empty; BTC EXECUTE had no recorded close.
