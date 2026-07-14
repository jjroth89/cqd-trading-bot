# CQD Trading Bot — Plain-English Fact Sheet

> **What this document is:** A non-technical, evidence-based description of what
> the CQD ("crypto-quant-desk") bot actually does, based on a direct read of its
> source code, config, logs, and the GitHub issue board. It is a **fact sheet**,
> not the final README. Every claim below is backed by code read; anything not
> verified is flagged in ⚠️ boxes.

---

## 1. One-paragraph summary — what CQD is
<!-- Simulation-only crypto analysis + paper-trading sandbox. No real money, no exchange keys. -->

## 2. What it trades and how decisions are made
<!-- watchlist (5 core + 5 rotating satellites), data sources (Binance read-only, FGI, CoinGecko),
     indicator scoring -> conviction 1-10, conviction>=7 gate in cqd_trigger.sh, ATR SL/TP sizing. -->

## 3. The cron job schedule
| Job | Schedule | What it does |
|-----|----------|--------------|
| cqd_monitor.sh | every 5m | closes positions on SL/TP |
| cqd_trigger.sh | every 15m | scores watchlist, opens conviction>=7 |
| cqd_rotator.sh | daily 04:00 UTC | rewrites 10-pair watchlist |
| cqd_watchdog.sh | every 10m | pages if engine stale / drift-blocked |

## 4. Sandbox vs. real trading — status
<!-- PROVE with scripts/verify_sandbox_only.sh. State: no live keys, no orders. -->

## 5. Known limitations / where this honestly stands
<!-- Use the honest-limitations checklist from SKILL.md §7. Be blunt. -->

## 6. Telegram alerting setup
<!-- CQD's OWN bot via CQD_TG_BOT_TOKEN/CQD_TG_CHAT_ID; entry/exit tickets; tg_sent_log.csv. -->

## 7. Glossary
<!-- conviction, SL/TP, watchlist, sandbox/paper, FGI, ATR, satellite pairs, watchdog -->

### Gaps I could not fully verify (flagged for the parent agent)
<!-- List any ⚠️ items: missing module, gate discrepancy, unverified claims. -->
