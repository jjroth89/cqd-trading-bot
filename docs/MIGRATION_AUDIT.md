# CQD Trading Bot Migration Audit

**Audit Date:** 2026-07-11  
**Repository:** https://github.com/jjroth89/cqd-trading-bot  
**Audit Scope:** Read-only analysis for container persistence compliance

---

## Executive Summary

The CQD trading bot is well-architected for the `/opt/data/` persistence model but contains several path mismatches that would cause the bot to fail in the current environment. The codebase assumes deployment to `/opt/data/cqd/` while the audit workspace was staged at `/opt/data/cqd-trading-bot-audit/`. Key findings:

- **No hardcoded API secrets found** - Bot correctly reads Telegram credentials from environment/file
- **All canonical I/O paths reference `/opt/data/cqd/`** - Requires directory rename or path updates
- **Virtual environment path hardcoded** to `/opt/data/.venv/cqd/bin/python` - exists but venv may need creation
- **Runtime temp files use `/tmp/`** - compliant with ephemeral storage pattern

---

## Credential Mapping

### Required Secrets (None Hardcoded in Source)

| Secret Key | Source | Purpose | Status |
|------------|--------|---------|--------|
| `CQD_TG_BOT_TOKEN` | `/opt/data/cqd/config/credentials.env` or environment | Telegram Bot API authentication | ✅ Externalized correctly |
| `CQD_TG_CHAT_ID` | `/opt/data/cqd/config/credentials.env` or environment | Telegram chat identifier | ✅ Externalized correctly |

### Credential Loading Chain

**sandbox_engine.py (lines 56-74):**
- First checks `os.getenv("CQD_TG_BOT_TOKEN")` and `os.getenv("CQD_TG_CHAT_ID")`
- Falls back to parsing `/opt/data/cqd/config/credentials.env` file
- No secrets are hardcoded in source code

**cron_wrappers/*.sh (lines 34-45):**
- All scripts export `CQD_TG_BOT_TOKEN` and `CQD_TG_CHAT_ID` from environment
- Reads from credentials file if environment variables not set
- Pattern is consistent across `cqd_trigger.sh`, `cqd_monitor.sh`, `cqd_rotator.sh`

---

## Persistence Violations

### Critical: Hardcoded Canonical Path `/opt/data/cqd/`

The codebase expects deployment under `/opt/data/cqd/` but was cloned to `/opt/data/cqd-trading-bot-audit/`. This creates path mismatches:

| File | Line(s) | Violation | Required Fix |
|------|---------|-----------|--------------|
| `core/quant_evaluator.py` | 24, 49-54 | `sys.path.insert(0, "/opt/data/cqd/core")`, `CQD_ROOT = Path("/opt/data/cqd")` | Update path constant or rename deployment directory |
| `core/sandbox_engine.py` | 35-41 | All canonical paths hardcoded to `/opt/data/cqd/` | Update path constants |
| `core/cqd_logger.py` | 28 | `LOG_FILE = Path("/opt/data/cqd/logs/cqd_master_log.csv")` | Update path constant |
| `core/rotate_watchlist.py` | 23 | Uses `Path(__file__).resolve().parent` - will resolve to correct location if moved | No change needed if deployed correctly |
| All cron wrappers | Various | Reference `${CQD_ROOT}/...` where `CQD_ROOT="/opt/data/cqd"` | Update `CQD_ROOT` variable |

### State File Dependencies

| State File | Referenced By | Line(s) | Persistence Required |
|------------|---------------|---------|---------------------|
| `/opt/data/cqd/state/wallet_state.json` | sandbox_engine.py, cqd_logger.py | 36, 178, 185, 341, 322 | ✅ Yes - wallet balances & positions |
| `/opt/data/cqd/state/tg_sent_log.csv` | sandbox_engine.py | 37, 114 | ✅ Yes - Telegram delivery audit trail |
| `/opt/data/cqd/state/cqd_monitor.lock` | sandbox_engine.py | 40, 322-324 | ✅ Yes - flock lock for idempotency |
| `/opt/data/cqd/state/macro_cache.json` | quant_evaluator.py | 70, 86 | ✅ Yes - Fear & Greed / BTC dominance cache |
| `/opt/data/cqd/logs/cqd_master_log.csv` | cqd_logger.py, cqd_health.sh | 28, 77, 40, 67 | ✅ Yes - master audit trail |

### Runtime Ephemeral Files (Compliant)

| File Pattern | Location | Purpose | Migration Status |
|--------------|----------|---------|-----------------|
| `/tmp/cqd_payload_{PAIR}.json` | cqd_trigger.sh line 69 | Pair-isolated evaluator output | ✅ Compliant - ephemeral temp |
| `/tmp/cqd_trigger_{PAIR}.json` | cqd_trigger.sh line 70 | EXECUTE payload for sandbox engine | ✅ Compliant - ephemeral temp |
| `/tmp/cqd_evaluator.lock` | quant_evaluator.py line 730 | Flock lock for concurrent runs | ✅ Compliant - ephemeral temp |

---

## Execution Blueprint

### Step 1: Directory Structure Setup

```bash
# Create canonical deployment directory (required for hardcoded paths)
mkdir -p /opt/data/cqd/{config,core,state,logs,cron_wrappers}

# Move code from audit workspace to canonical location
cp -r /opt/data/cqd-trading-bot-audit/core/* /opt/data/cqd/core/
cp -r /opt/data/cqd-trading-bot-audit/config/* /opt/data/cqd/config/
cp -r /opt/data/cqd-trading-bot-audit/cron_wrappers/* /opt/data/cqd/cron_wrappers/
```

### Step 2: Virtual Environment Setup

```bash
# Create venv at expected location
uv venv /opt/data/.venv/cqd

# Install dependencies
/opt/data/.venv/cqd/bin/uv pip install ccxt pandas pandas-ta numpy requests
```

### Step 3: Initialize State Files

```bash
# Create initial wallet state
echo '{"balance_usdt": 10000.0, "open_positions": {}, "trade_history": []}' \
  > /opt/data/cqd/state/wallet_state.json

# Create credentials file (user must populate)
touch /opt/data/cqd/config/credentials.env
chmod 600 /opt/data/cqd/config/credentials.env
# User must add:
# CQD_TG_BOT_TOKEN=<bot_token>
# CQD_TG_CHAT_ID=<chat_id>
```

### Step 4: Coolify Volume Mount Configuration

```yaml
# docker-compose.yml or Coolify service config
volumes:
  - cqd-persistence:/opt/data/cqd

volumes:
  cqd-persistence:
    driver: local
```

### Step 5: Cron Job Registration

```bash
# Evaluator (15-min intervals)
cronjob action=create schedule='*/15 * * * *' \
  script=/opt/data/cqd/cron_wrappers/cqd_trigger.sh no_agent=true \
  name='cqd-evaluator'

# Monitor (5-min intervals)
cronjob action=create schedule='*/5 * * * *' \
  script=/opt/data/cqd/cron_wrappers/cqd_monitor.sh no_agent=true \
  name='cqd-monitor'

# Watchlist Rotator (daily 04:00 UTC)
cronjob action=create schedule='0 4 * * *' \
  script=/opt/data/cqd/cron_wrappers/cqd_rotator.sh no_agent=true \
  name='cqd-rotator'

# Health Check (hourly)
cronjob action=create schedule='0 * * * *' \
  script=/opt/data/cqd/cron_wrappers/cqd_health.sh no_agent=true \
  name='cqd-health'

# Telegram Verification (daily 09:00 UTC)
cronjob action=create schedule='0 9 * * *' \
  script=/opt/data/cqd/cron_wrappers/cqd_telegram_verification.sh no_agent=true \
  name='cqd-tg-verify'
```

### Step 6: Manual Execution Commands

```bash
# Single pair evaluation
/opt/data/.venv/cqd/bin/python /opt/data/cqd/core/quant_evaluator.py --pair BTC/USDT

# Position monitoring
/opt/data/.venv/cqd/bin/python /opt/data/cqd/core/sandbox_engine.py --monitor

# Health check
bash /opt/data/cqd/cron_wrappers/cqd_health.sh

# Watchlist rotation
/opt/data/.venv/cqd/bin/python /opt/data/cqd/core/rotate_watchlist.py
```

---

## Architecture Summary

### Entry Points
- **`cqd_trigger.sh`** - Main cron entry for signal evaluation and execution
- **`quant_evaluator.py --pair <symbol>`** - Technical analysis engine
- **`sandbox_engine.py --monitor`** - Position SL/TP monitor
- **`sandbox_engine.py --execute <payload>`** - Manual trade execution

### External Dependencies
- `ccxt` - Exchange connectivity (Binance)
- `pandas` - Data manipulation
- `pandas-ta` - Technical indicators
- `numpy` - Numerical operations
- `requests` - HTTP session (sentiment APIs, Telegram)

### API Endpoints Called
| Service | Endpoint | Purpose | Cache |
|---------|----------|---------|-------|
| Binance | `ccxt.binance()` | OHLCV candles | None |
| Alternative.me | `api.alternative.me/fng` | Fear & Greed Index | 2-hour TTL |
| CoinGecko | `api.coingecko.com/v3/global` | BTC dominance | 2-hour TTL |
| Telegram | `api.telegram.org/bot{token}/sendMessage` | Position alerts | None |

---

## Migration Punch-List

### Required Actions Before Deployment

1. **Directory Rename** - Move code to `/opt/data/cqd/` OR update all hardcoded paths
2. **Virtual Environment** - Create `/opt/data/.venv/cqd/` with required packages
3. **Credentials Setup** - Populate `/opt/data/cqd/config/credentials.env` with Telegram tokens
4. **Volume Mounts** - Configure Coolify to persist `/opt/data/cqd/` directory
5. **State Initialization** - Create initial `wallet_state.json`
6. **Cron Registration** - Register all 5 cron jobs with `no_agent=true`

### Optional Enhancements

- Consider parameterizing `CQD_ROOT` via environment variable for portability
- Add retry logic for transient network failures in macro API calls
- Implement log rotation for `cqd_master_log.csv` to prevent unbounded growth