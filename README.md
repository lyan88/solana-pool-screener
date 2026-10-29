# Solana Pool Screener

[![CI](https://github.com/lyan88/solana-pool-screener/actions/workflows/ci.yml/badge.svg)](https://github.com/lyan88/solana-pool-screener/actions/workflows/ci.yml)

**English** | [Bahasa Indonesia](README.id.md)

A new-pool screener for Solana. It has one job: find tokens that pass strict
filters and send a single Telegram alert. **It never holds a private key and
never swaps.**

## Filters

| Filter | Default | Source |
|---|---|---|
| Mint authority revoked | required | Rugcheck |
| Freeze authority revoked | required | Rugcheck |
| LP burned / locked | ≥ 99% (burned preferred) | Rugcheck per pool |
| Top 10 holders | < 35% (excluding pool vaults & burn, min 10 holders and 5% supply) | Rugcheck |
| Market cap / FDV | $8,000 – $100,000 | GeckoTerminal |
| Liquidity | ≥ $5,000 | GeckoTerminal |
| Pool age | < 10 minutes | GeckoTerminal |
| Unique buyers | ≥ 15 in 2 minutes | GeckoTerminal trades |

Every threshold can be changed in `.env` (see `.env.example`).

## One scan round

1. Fetch 2 pages of GeckoTerminal `new_pools` (±40 pools).
2. Cheap prefilter: age, liquidity, MC, and m5 `buyers`.
3. For Meteora DAMM v2 pools, check the official Meteora API. Blacklisted
   pools or pools with near-zero TVL are rejected immediately (`pool_dead`)
   without calling Rugcheck — GeckoTerminal reserves are often misleading for
   these pools.
4. For remaining candidates, fetch the Rugcheck report: mint/freeze
   authorities, top holders, and LP status for the matching pool. If the
   Meteora API reports a permanent lock ≥ 99%, that number wins.
5. Only after everything passes, fetch the pool's trades and count **unique
   wallets** buying in the last 120 seconds. The alert price comes from the
   latest sane trade, not the `new_pools` snapshot which can be stale.
6. Passing tokens enter a **priority alert queue** and are sent within the
   Telegram quota. Near-misses enter a **watchlist** and are re-evaluated
   every 2 minutes until they are 10 minutes old.

Detection runs every 2 minutes via cron, so a token can be evaluated several
times while its holders become more distributed.

### What the alert shows

- **Price** uses the latest trade that is not flagged `is_anomalous`. If there
  is no fresh trade (≤ 180 seconds), the GeckoTerminal snapshot is used as a
  fallback. Prices are plain decimals, never scientific notation.
- **Mint auth / Freeze auth** are rendered from the Rugcheck report as-is:
  `revoked ✅`, `ACTIVE ⚠️`, or `unknown ❔`. The screener never assumes
  revoked without data.
- **LP** is labelled `burned` when `lpMint` is a burn address on a DEX that
  uses fungible LP tokens (pump.fun/pumpswap/raydium/Meteora Dynamic).
  Position-NFT DEXes such as Meteora DAMM v2 are not treated as burned just
  because `lpMint` is empty; their status follows Meteora's permanent lock or
  Rugcheck's `lpLockedPct`. `locked` is only used when there is evidence of a
  lock ≥ 99%.
- Alerts are **English by default**. Set `ALERT_LANG=id` in `.env` for
  Indonesian. The daily 07:00 UTC report follows the same setting.

### Alert queue

Alerts are not dropped when the Telegram quota is full:

- stored in the `alert_queue` table with a priority score
- sent immediately when they pass; the queue only holds alerts blocked by
  quota/rate limits, always sending the highest score first
- expire after 15 minutes so stale signals are not delivered
- delayed more than 30 seconds: the alert gets a banner saying age & price are
  as of detection
- delayed more than 90 seconds: the stored entry price is no longer used; the
  tracker falls back to the candle price when the alert was actually sent

### Watchlist

Candidates rejected for reasons that can still improve (top10 close to the
threshold, LP almost locked, LP not readable yet, slightly too few buyers,
failed trades) are re-checked without having to reappear in `new_pools`.
Limits are `WATCH_TTL_SEC` (10 minutes) and `WATCH_MAX_CHECKS` (5 checks).

## Outcome tracking

Every alert is measured automatically at +5 minutes, +30 minutes, +1 hour,
and +6 hours:

- **MFE** — maximum upside from the alert price (candle high)
- **MAE** — maximum drawdown from the alert price (candle low)
- **win** — MFE ≥ +50% (`WIN_MFE_MIN=1.5`)

```bash
python3 track.py --backfill   # prepare rows for old alerts
python3 track.py --status     # inspect the queue
python3 track.py              # measure alerts that are due
python3 track.py --dry-run    # measure without writing to the DB
python3 report.py --dry-run   # 24-hour report
python3 report.py --telegram  # send the report
```

Cron runs the tracker every 5 minutes and the report at 07:00 UTC.

### Watchdog

`watchdog.py` runs every 5 minutes and checks the newest row in `scans`. If
the scanner has not written anything for 10 minutes (cron dead or the process
crashing), it sends one Telegram alert. While the problem persists it
re-alerts hourly, and it sends a recovery message when scans resume. Partial
API failures still write a scan row, so those show up as `errors` in the daily
report rather than triggering the watchdog.

Tune it in `.env`:

```bash
WATCHDOG_ENABLED=true
WATCHDOG_STALE_SEC=600    # 10 minutes
WATCHDOG_REPEAT_SEC=3600  # re-alert hourly
```

> MAE is calculated from the **1-minute candle low**, so it can look more
> extreme than the closing price. For new tokens, -30% to -60% wicks within
> 5 minutes are common. Treat it as a risk measure, not a realistic exit.

## Running

```bash
git clone https://github.com/lyan88/solana-pool-screener.git
cd solana-pool-screener
cp .env.example .env      # fill in the Telegram token & chat id
python3 scanner.py --once --dry-run --verbose
python3 scanner.py --test-telegram
python3 scanner.py --once
```

Local loop:

```bash
python3 scanner.py --loop 120 --verbose
```

Run the tests without network access:

```bash
python3 -m unittest discover -s tests -v
```

## Deploy on a VPS

```bash
sudo ./deploy/install.sh
```

The script copies the app to `/opt/screener2`, runs the tests + `doctor.py`,
then installs `/etc/cron.d/screener2` (scan every 2 minutes, tracker and
watchdog every 5 minutes, report at 07:00 UTC; user `screener`, `flock`).
If `doctor.py` fails, cron is not enabled.

Manual checks:

```bash
sudo -u screener python3 /opt/screener2/doctor.py
sudo -u screener python3 /opt/screener2/scanner.py --once --dry-run --verbose
tail -f /opt/screener2/logs/scan.log
```

## GitHub sync

Repo: https://github.com/lyan88/solana-pool-screener

On the development VPS, `deploy/git-sync.sh` runs every 15 minutes via cron:

- commits & pushes only when something changed
- unit tests must pass before pushing
- `.env`, `data/`, and `logs/` are never included

Pull updates from GitHub into production manually, after CI is green:

```bash
sudo ./deploy/update.sh
```

It runs `git pull --ff-only` as the repo owner, then
`deploy/install.sh --force`.

## Known limitations

- **Not real-time.** GeckoTerminal is a public API, not an RPC stream. The
  newest pools can be 1–2 minutes late. Sub-second sniping needs
  RPC/WebSocket, not this screener.
- **Top holders from Rugcheck are a snapshot.** They can change seconds later;
  this is a risk filter, not a guarantee.
- **LP "locked" ≠ "burned".** By default both are accepted because many pools
  use a locker. Set `LP_ACCEPT_LOCKED=false` if you only want LP actually sent
  to the incinerator.
- **Strict filters mean few alerts.** At minute 2, top 10 often still holds
  50–90%. New tokens usually pass after a few minutes, if they distribute.
- **Rugcheck may not index a token that is only seconds old.** Such
  candidates are rejected with `security data unavailable` and retried on the
  next round.
- **No profit guarantee.** The screener only filters; buying decisions and
  risk management remain yours.
- **Alert limits** default to 40 messages/hour and 400/day. Alerts that cannot
  be sent are queued, not dropped. Change `TELEGRAM_MAX_PER_HOUR` /
  `TELEGRAM_MAX_PER_DAY` if needed.

## Layout

```
scanner.py     one scan round orchestration
sources.py     GeckoTerminal + Rugcheck + Meteora normalization
filters.py     all filter rules (pure, easy to test)
net.py         HTTP + adaptive rate limit + 429 retry (cross-process lock)
track.py       outcome tracker +5m/+30m/+1h/+6h
report.py      daily report to Telegram
telegram.py    alert formatting & sending, rate limits
i18n.py        English/Indonesian alert strings
watchdog.py    alert when scans stop
db.py          SQLite: evaluations, alerts, scans, outcomes, cache
doctor.py      environment check before enabling cron
tests/         unit tests for filters, outcomes, and alert formatting
deploy/        cron + install scripts
```

Data lives in `data/screener2.db`:

- `evaluations` — every candidate evaluated, with pass/reject reasons
- `alerts` — one row per mint that was actually sent
- `alert_queue` — prioritized alerts not sent yet
- `watchlist` — near-miss candidates being re-checked
- `scans` — per-round summary (pools, candidates, alerts, errors)
- `outcomes` — result per alert per horizon (MFE/MAE)
- `security_cache` — Rugcheck reports, 120-second TTL

## Quick tuning

| Symptom | Change |
|---|---|
| Too many alerts | lower `TOP10_MAX_PCT`, raise `MIN_UNIQUE_BUYERS` |
| No alerts at all | raise `TOP10_MAX_PCT` to 50, or set `LP_ACCEPT_LOCKED=true` |
| Missing pools | raise `SCAN_PAGES`, lower the cron interval (429 risk) |
| Hitting 429 | raise `GECKO_MIN_INTERVAL` (6 → 10 seconds) |
| Want safer liquidity | raise `MIN_LIQ_USD` |

## License & disclaimer

MIT — see [LICENSE](LICENSE).

This project is a screening tool only and **not financial advice**. There is
no profit guarantee, and no guarantee that a token passing the filters is free
from a rug. Always verify yourself before buying.

Every push/PR runs the unit tests through GitHub Actions
(`.github/workflows/ci.yml`).
