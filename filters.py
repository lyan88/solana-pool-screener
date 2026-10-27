#!/usr/bin/env python3
"""
Filter murni: tidak menyentuh jaringan, supaya bisa diuji tanpa API.

Urutan evaluasi sengaja dari yang paling murah:
  umur -> likuiditas -> market cap -> buyers m5
  -> mint/freeze -> top holder -> LP -> unique buyer 2 menit (butuh trades)
"""

from datetime import datetime

import config

BURN_ADDRESSES = {
    "1nc1nerator11111111111111111111111111111111",
    "11111111111111111111111111111111",
    "So11111111111111111111111111111111111111112",
}
AMM_TYPES = {"AMM", "POOL", "MARKET"}
# DEX yang memakai bonding curve: tidak ada token LP yang bisa ditarik dev.
BONDING_DEXES = {"pump-fun", "pumpfun", "meteora-dbc"}


def _f(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _revoked(value):
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict)):
        # Rugcheck kadang mengirim {"authority": []}; anggap revoked kalau kosong.
        if isinstance(value, dict):
            authority = value.get("authority")
            if isinstance(authority, list):
                return len(authority) == 0
        return len(value) == 0
    return False


def check_age(pool):
    age = pool.get("age_sec")
    if age is None:
        return False, "umur tidak diketahui"
    if age < config.MIN_AGE_SEC:
        return False, "terlalu baru (%ds)" % age
    if age >= config.MAX_AGE_SEC:
        return False, "umur %dm >= %dm" % (age / 60.0, config.MAX_AGE_SEC / 60.0)
    return True, "umur %dm%02ds" % (age // 60, age % 60)


def check_liquidity(pool):
    liq = _f(pool.get("liq_usd"))
    if liq < config.MIN_LIQ_USD:
        return False, "likuiditas $%s < $%s" % (fmt_usd(liq), fmt_usd(config.MIN_LIQ_USD))
    return True, "likuiditas $%s" % fmt_usd(liq)


def check_market_cap(pool):
    mc = pool.get("mc_usd")
    if mc is None or mc <= 0:
        return False, "market cap tidak tersedia"
    if mc < config.MIN_MC_USD:
        return False, "MC $%s < $%s" % (fmt_usd(mc), fmt_usd(config.MIN_MC_USD))
    if mc > config.MAX_MC_USD:
        return False, "MC $%s > $%s" % (fmt_usd(mc), fmt_usd(config.MAX_MC_USD))
    return True, "MC $%s" % fmt_usd(mc)


def check_buyers5(pool):
    """Prefilter murah: unique buyer 5 menit tidak mungkin lebih kecil dari 2 menit."""
    buyers = pool.get("buyers5") or 0
    if buyers < config.MIN_UNIQUE_BUYERS:
        return False, "buyer 5m %d < %d" % (buyers, config.MIN_UNIQUE_BUYERS)
    return True, "buyer 5m %d" % buyers


def check_authorities(report):
    if not report:
        return False, "data keamanan tidak tersedia"
    token = report.get("token") or {}
    mint_auth = report.get("mintAuthority", token.get("mintAuthority"))
    freeze_auth = report.get("freezeAuthority", token.get("freezeAuthority"))
    if config.REQUIRE_MINT_REVOKED and not _revoked(mint_auth):
        return False, "mint authority masih aktif"
    if config.REQUIRE_FREEZE_REVOKED and not _revoked(freeze_auth):
        return False, "freeze authority masih aktif"
    if config.REJECT_RUGGED and report.get("rugged") is True:
        return False, "token ditandai rugged"
    return True, "mint & freeze revoked"


def _market_for_pool(report, pool_address):
    markets = report.get("markets") or []
    for market in markets:
        if market.get("pubkey") == pool_address:
            return market
    if len(markets) == 1:
        return markets[0]
    return None


def top10(report, pool_address):
    """
    Persentase 10 holder terbesar setelah membuang akun AMM/pool dan burn.

    Token di vault pool bukan "holder" dalam arti risiko jual, jadi tidak
    dihitung. Token yang dikunci (LOCKER) tetap dihitung: masih milik satu
    pihak, hanya belum bisa dijual.
    """
    if not report:
        return None, [], "data holder tidak tersedia"
    holders = report.get("topHolders")
    if not holders:
        return None, [], "top holder tidak tersedia"

    known = report.get("knownAccounts") or {}
    market = _market_for_pool(report, pool_address) or {}
    excluded_owners = {market.get("pubkey")} if market.get("pubkey") else set()
    for key in ("liquidityAAccount", "liquidityBAccount"):
        owner = (market.get(key) or {}).get("owner")
        if owner:
            excluded_owners.add(owner)

    kept = []
    excluded = []
    for holder in holders:
        owner = holder.get("owner") or ""
        address = holder.get("address") or ""
        info = known.get(owner) or known.get(address) or {}
        typ = (info.get("type") or "").upper()
        pct = _f(holder.get("pct"))
        if pct <= 0:
            continue
        is_pool = owner in excluded_owners
        is_amm = typ in AMM_TYPES
        is_burn = owner in BURN_ADDRESSES or address in BURN_ADDRESSES
        if is_pool or is_amm or is_burn:
            excluded.append((owner or address, pct, typ or "POOL/BURN"))
            continue
        kept.append((owner or address, pct))
        if len(kept) >= 10:
            break

    if not kept:
        return None, excluded, "tidak ada holder yang bisa dihitung"
    if len(kept) < config.MIN_TOP10_HOLDERS:
        return (
            None,
            excluded,
            "hanya %d holder nyata (< %d); top10 belum berarti"
            % (len(kept), config.MIN_TOP10_HOLDERS),
        )
    total = sum(pct for _, pct in kept)
    if total <= 0:
        return None, excluded, "total holder nol"
    if total < config.MIN_TOP10_TOTAL_PCT:
        return (
            None,
            excluded,
            "top10 hanya %.2f%% supply (< %.1f%%); float terlalu tipis"
            % (total, config.MIN_TOP10_TOTAL_PCT),
        )
    return round(total, 4), excluded, "%d holder dihitung" % len(kept)


def meteora_dead(meteora):
    """Pool Meteora dianggap mati kalau diblacklist atau TVL-nya nyaris nol."""
    if not meteora:
        return False
    if meteora.get("is_blacklisted"):
        return True
    return _f(meteora.get("tvl"), 0.0) < config.METEORA_MIN_TVL_USD


def meteora_lock_pct(meteora):
    """Persentase likuiditas yang dikunci permanen menurut API Meteora."""
    if not meteora:
        return None
    tvl = _f(meteora.get("tvl"), 0.0)
    locked = _f(meteora.get("permanent_lock_liquidity"), 0.0)
    if tvl <= 0 or locked <= 0:
        return None
    return min(100.0, locked / tvl * 100.0)


def _launchpad_name(report):
    """Rugcheck bisa mengirim launchpad sebagai string atau objek."""
    value = (report or {}).get("launchpad")
    if isinstance(value, dict):
        value = value.get("name") or value.get("id") or ""
    if not isinstance(value, str):
        return ""
    return value.strip().lower()


def lp_state(report, pool_address, dex=None, meteora=None):
    """
    Kembalikan (status, pct, detail).
    status: burned | locked | unlocked | unknown | dead
    """
    dex = (dex or "").lower()

    if meteora_dead(meteora):
        reason = "blacklist" if meteora.get("is_blacklisted") else "tvl $%s" % fmt_usd(
            _f(meteora.get("tvl"), 0.0)
        )
        return "dead", None, "pool Meteora mati (%s)" % reason

    if not report:
        if dex in BONDING_DEXES:
            return "burned", 100.0, "bonding curve, tanpa token LP"
        return "unknown", None, "data LP tidak tersedia"

    market = _market_for_pool(report, pool_address)
    if not market:
        # Pool bonding curve tidak punya token LP sama sekali.
        launchpad = _launchpad_name(report)
        if launchpad.startswith("pump") or dex in BONDING_DEXES:
            return "burned", 100.0, "bonding curve, tanpa token LP"
        return "unknown", None, "pool tidak ada di laporan"

    lp = market.get("lp") or {}
    if not lp:
        return "unknown", None, "detail LP kosong"

    pct = lp.get("lpLockedPct")
    pct = None if pct is None else _f(pct)

    holders = lp.get("holders") or []
    burned = False
    for holder in holders:
        owner = holder.get("owner") or ""
        address = holder.get("address") or ""
        if owner in BURN_ADDRESSES or address in BURN_ADDRESSES:
            burned = True
            break

    if burned:
        return "burned", 100.0, "LP dikirim ke incinerator"

    # Untuk DAMM v2, Rugcheck kadang tidak melihat posisi yang dikunci.
    # Angka resmi Meteora dipakai lebih dulu kalau ada.
    mlock = meteora_lock_pct(meteora)
    if mlock is not None and mlock >= config.LP_MIN_LOCKED_PCT:
        return "locked", mlock, "LP terkunci %.2f%% (Meteora)" % mlock

    if pct is not None and pct >= config.LP_MIN_LOCKED_PCT:
        return "locked", pct, "LP terkunci %.2f%%" % pct
    if pct is None:
        if mlock is not None and mlock > 0:
            return "unlocked", mlock, "LP terkunci sebagian %.1f%% (Meteora)" % mlock
        return "unknown", None, "persentase LP tidak tersedia"
    return "unlocked", pct, "LP terbuka %.2f%%" % pct


def check_lp(report, pool_address, dex=None, meteora=None):
    status, pct, detail = lp_state(report, pool_address, dex=dex, meteora=meteora)
    if status == "burned":
        return True, detail, status, pct
    if status == "locked":
        if config.LP_ACCEPT_LOCKED:
            return True, detail, status, pct
        return False, "LP terkunci, bukan burned", status, pct
    return False, detail, status, pct


def buyer_window(trades, now, window_sec=None, created_at=None):
    """
    Hitung unique buyer dalam jendela terakhir.

    Kalau pool lebih muda dari jendela, pakai seluruh umur pool.
    """
    window_sec = config.BUYER_WINDOW_SEC if window_sec is None else window_sec
    if now is None:
        return None
    if created_at is not None:
        age = (now - created_at).total_seconds()
        if age < window_sec:
            window_sec = max(1.0, age)
    cutoff = now.timestamp() - window_sec

    buyers = set()
    buys = sells = 0
    for trade in trades or []:
        ts = trade.get("ts")
        if ts is None or ts.timestamp() < cutoff:
            continue
        if trade.get("anomalous"):
            continue
        wallet = trade.get("wallet")
        if not wallet:
            continue
        if trade.get("kind") == "buy":
            buys += 1
            buyers.add(wallet)
        elif trade.get("kind") == "sell":
            sells += 1

    truncated = False
    if len(trades or []) >= 300:
        oldest = min((t.get("ts") for t in trades if t.get("ts")), default=None)
        if oldest is not None and oldest.timestamp() > cutoff:
            truncated = True

    return {
        "buyers": len(buyers),
        "buys": buys,
        "sells": sells,
        "window_sec": int(window_sec),
        "truncated": truncated,
    }


def check_buyers_window(trades, now, created_at=None):
    stats = buyer_window(trades, now, created_at=created_at)
    if stats is None:
        return False, "trade tidak tersedia", None
    if stats["truncated"] and stats["buyers"] < config.MIN_UNIQUE_BUYERS:
        return False, "data trade terpotong, buyer tidak bisa dipastikan", stats
    if stats["buyers"] < config.MIN_UNIQUE_BUYERS:
        return (
            False,
            "buyer unik %ds: %d < %d"
            % (stats["window_sec"], stats["buyers"], config.MIN_UNIQUE_BUYERS),
            stats,
        )
    return (
        True,
        "buyer unik %ds: %d (%d beli / %d jual)"
        % (stats["window_sec"], stats["buyers"], stats["buys"], stats["sells"]),
        stats,
    )


def alert_score(pool, sec, buyers=None):
    """
    Skor pengurutan antrean (makin tinggi makin layak dikirim lebih dulu).

    Bukan filter: hanya dipakai saat kuota terbatas. Rumusnya sengaja
    konservatif dan memakai data yang sudah lolos filter.
    """
    score = 0.0

    top10_pct = (sec or {}).get("top10_pct")
    if top10_pct is not None:
        score += max(0.0, config.TOP10_MAX_PCT - float(top10_pct)) * 2.0

    buyer_count = (buyers or {}).get("buyers")
    if buyer_count is None:
        buyer_count = pool.get("buyers2") or pool.get("buyers5") or 0
    score += min(float(buyer_count), 150.0) / 15.0

    mc = float(pool.get("mc_usd") or 0)
    liq = float(pool.get("liq_usd") or 0)
    if mc > 0:
        score += min(liq / mc, 0.6) * 20.0

    age = float(pool.get("age_sec") or 0)
    score += max(0.0, (config.MAX_AGE_SEC - age) / 60.0) * 0.5
    return round(score, 3)


def near_miss(row):
    """
    Apakah penolakan ini masih mungkin membaik dalam beberapa menit?

    Hanya alasan yang bisa berubah sendiri yang dipantau. Mint/freeze aktif
    atau pool mati tidak akan berubah, jadi langsung dibuang.
    """
    reasons = set(row.get("reasons") or [])
    if not reasons or reasons & {"check_authorities", "pool_dead"}:
        return False

    if "trades_unavailable" in reasons:
        return True

    if "top10" in reasons:
        pct = row.get("top10_pct")
        if pct is None:
            return True
        if config.TOP10_MAX_PCT <= float(pct) <= (
            config.TOP10_MAX_PCT + config.WATCH_TOP10_MARGIN_PCT
        ):
            return True

    if "lp" in reasons:
        status = row.get("lp_status")
        pct = row.get("lp_pct")
        if status == "unknown":
            return True
        if status == "unlocked" and pct is not None and float(pct) >= config.WATCH_LP_MIN_PCT:
            return True

    if "buyers" in reasons:
        buyers = row.get("buyers2") or 0
        if buyers >= config.MIN_UNIQUE_BUYERS * config.WATCH_BUYERS_RATIO:
            return True

    return False


def fmt_usd(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "?"
    if value >= 1_000_000:
        return "%.2fM" % (value / 1_000_000)
    if value >= 1_000:
        return "%.1fk" % (value / 1_000)
    return "%.0f" % value


def evaluate_cheap(pool):
    """Filter tanpa API tambahan. Kembalikan (lolos, alasan_gagal)."""
    for fn in (check_age, check_liquidity, check_market_cap, check_buyers5):
        ok, detail = fn(pool)
        if not ok:
            return False, fn.__name__, detail
    return True, None, "lolos filter murah"


def evaluate_security(pool, report, meteora=None):
    """Filter keamanan + holder + LP. Kembalikan dict hasil."""
    if meteora_dead(meteora):
        _status, _pct, detail = lp_state(
            None, pool.get("pool"), dex=pool.get("dex"), meteora=meteora
        )
        return {
            "passed": False,
            "reasons": ["pool_dead"],
            "details": {
                "lp_status": "dead",
                "lp_pct": None,
                "lp_detail": detail,
                "top10_pct": None,
                "top10_detail": "dilewati: pool mati",
            },
            "top10_pct": None,
            "excluded_holders": [],
        }

    passed = True
    reasons = []
    details = {}

    for fn in (check_authorities,):
        ok, detail = fn(report)
        details[fn.__name__] = detail
        if not ok:
            passed = False
            reasons.append(fn.__name__)

    pct, excluded, top_detail = top10(report, pool.get("pool"))
    details["top10_pct"] = pct
    details["top10_detail"] = top_detail
    if pct is None or pct >= config.TOP10_MAX_PCT:
        passed = False
        reasons.append("top10")
        if pct is not None:
            details["top10_detail"] = "top10 %.1f%% >= %.0f%%" % (pct, config.TOP10_MAX_PCT)

    ok, lp_detail, lp_status, lp_pct = check_lp(
        report, pool.get("pool"), dex=pool.get("dex"), meteora=meteora
    )
    details["lp_status"] = lp_status
    details["lp_pct"] = lp_pct
    details["lp_detail"] = lp_detail
    if not ok:
        passed = False
        reasons.append("pool_dead" if lp_status == "dead" else "lp")

    return {
        "passed": passed,
        "reasons": reasons,
        "details": details,
        "top10_pct": pct,
        "excluded_holders": excluded,
    }
