#!/usr/bin/env python3
"""
Adaptor sumber data. Semua perbedaan bentuk API dinormalisasi di sini.

Sumber:
  - GeckoTerminal  : pool baru + daftar trade per pool (tanpa API key)
  - Rugcheck       : mint/freeze authority, top holder, status LP per pool
"""

import time
from datetime import datetime, timezone

import config
import net

GECKO = net.limiter("gecko", config.GECKO_MIN_INTERVAL)
RUGCHECK = net.limiter("rugcheck", config.RUGCHECK_MIN_INTERVAL)
METEORA = net.limiter("meteora", config.METEORA_MIN_INTERVAL)


def _f(value, default=None):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _i(value, default=None):
    v = _f(value)
    return default if v is None else int(v)


def parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _pool_from_gecko(item):
    attrs = item.get("attributes") or {}
    rel = item.get("relationships") or {}
    base = ((rel.get("base_token") or {}).get("data") or {}).get("id", "")
    quote = ((rel.get("quote_token") or {}).get("data") or {}).get("id", "")
    dex = ((rel.get("dex") or {}).get("data") or {}).get("id", "")
    name = attrs.get("name") or ""
    symbol = name.split(" / ")[0].strip() if " / " in name else name.strip()
    tx = attrs.get("transactions") or {}
    m5 = tx.get("m5") or {}
    m15 = tx.get("m15") or {}
    created = parse_ts(attrs.get("pool_created_at"))
    now = datetime.now(timezone.utc)
    age = max(0, int((now - created).total_seconds())) if created else None
    market_cap = _f(attrs.get("market_cap_usd"))
    fdv = _f(attrs.get("fdv_usd"))
    return {
        "pool": attrs.get("address") or item.get("id", "").split("_", 1)[-1],
        "mint": base.split("_", 1)[-1] if base else "",
        "quote_mint": quote.split("_", 1)[-1] if quote else "",
        "name": name,
        "symbol": symbol,
        "dex": dex,
        "created_at": created,
        "age_sec": age,
        "liq_usd": _f(attrs.get("reserve_in_usd"), 0.0),
        "fdv_usd": fdv,
        "market_cap_usd": market_cap,
        "mc_usd": market_cap if market_cap is not None else fdv,
        "price_usd": _f(attrs.get("base_token_price_usd")),
        "buyers5": _i(m5.get("buyers"), 0),
        "buys5": _i(m5.get("buys"), 0),
        "sells5": _i(m5.get("sells"), 0),
        "buyers15": _i(m15.get("buyers"), 0),
        "volume5_usd": _f((attrs.get("volume_usd") or {}).get("m5"), 0.0),
        "source": "gecko:new_pools",
    }


def fetch_new_pools(pages=None):
    """Ambil pool baru dari GeckoTerminal, halaman 1..pages."""
    pages = config.SCAN_PAGES if pages is None else pages
    out = []
    seen = set()
    errors = []
    for page in range(1, pages + 1):
        url = "%s/networks/%s/new_pools?page=%d" % (
            config.GECKO_BASE, config.GECKO_NETWORK, page
        )
        try:
            data = net.get_json(url, lim=GECKO)
        except net.SourceError as exc:
            errors.append(str(exc))
            continue
        for item in data.get("data") or []:
            pool = _pool_from_gecko(item)
            if not pool["pool"] or not pool["mint"] or pool["pool"] in seen:
                continue
            seen.add(pool["pool"])
            out.append(pool)
    if not out and errors:
        raise net.SourceError("; ".join(errors))
    return out, errors


def fetch_pool(pool_address):
    """Ambil data terbaru satu pool (untuk watchlist)."""
    if not pool_address:
        return None
    url = "%s/networks/%s/pools/%s" % (
        config.GECKO_BASE, config.GECKO_NETWORK, net.quote(pool_address)
    )
    data = net.get_json(url, lim=GECKO)
    item = data.get("data") if isinstance(data, dict) else None
    if not item:
        return None
    pool = _pool_from_gecko(item)
    if not pool["pool"] or not pool["mint"]:
        return None
    return pool


def _trade_from_gecko(item):
    attrs = item.get("attributes") or {}
    ts = parse_ts(attrs.get("block_timestamp"))
    return {
        "ts": ts,
        "kind": (attrs.get("kind") or "").lower(),
        "wallet": attrs.get("tx_from_address") or "",
        "tx": attrs.get("tx_hash") or "",
        "from_token": attrs.get("from_token_address") or "",
        "to_token": attrs.get("to_token_address") or "",
        "price_from_usd": _f(attrs.get("price_from_in_usd")),
        "price_to_usd": _f(attrs.get("price_to_in_usd")),
        "volume_usd": _f(attrs.get("volume_in_usd"), 0.0),
        "anomalous": bool(attrs.get("is_anomalous")),
    }


def trade_price(trade, mint):
    """Harga mint pada satu trade, dari sisi mana pun mint berada."""
    if not trade or trade.get("anomalous"):
        return None
    if trade.get("to_token") == mint:
        price = trade.get("price_to_usd")
    elif trade.get("from_token") == mint:
        price = trade.get("price_from_usd")
    else:
        return None
    try:
        price = float(price)
    except (TypeError, ValueError):
        return None
    return price if price > 0 else None


def latest_trade_price(trades, mint, max_age_sec=180, now=None):
    """
    Harga dari trade terakhir yang wajar (bukan anomalous).

    Snapshot ``new_pools`` bisa tertinggal beberapa menit; trade terakhir
    lebih dekat ke harga yang benar-benar terjadi. Kalau tidak ada trade
    segar, kembalikan None supaya pemanggil memakai snapshot.
    """
    now = now or datetime.now(timezone.utc)
    best_ts = None
    best_price = None
    for trade in trades or []:
        price = trade_price(trade, mint)
        if price is None:
            continue
        ts = trade.get("ts")
        if ts is None:
            continue
        age = (now - ts).total_seconds()
        if age < -30:
            continue
        if max_age_sec is not None and age > max_age_sec:
            continue
        if best_ts is None or ts > best_ts:
            best_ts = ts
            best_price = price
    return best_price


def fetch_trades(pool):
    """Trade terakhir untuk satu pool. Gecko membatasi jumlah baris."""
    url = "%s/networks/%s/pools/%s/trades" % (
        config.GECKO_BASE, config.GECKO_NETWORK, net.quote(pool)
    )
    data = net.get_json(url, lim=GECKO)
    trades = [_trade_from_gecko(x) for x in (data.get("data") or [])]
    return [t for t in trades if t["ts"] and t["wallet"]]


def fetch_ohlcv(pool, timeframe="minute", aggregate=1, limit=1000, before_ts=None):
    """
    Lilin OHLCV dari GeckoTerminal.

    Bentuk asli: data.attributes.ohlcv_list = [[ts, open, high, low, close, vol], ...]
    Dikembalikan urut naik sebagai list dict.
    """
    url = "%s/networks/%s/pools/%s/ohlcv/%s?aggregate=%d&limit=%d&currency=usd&token=base" % (
        config.GECKO_BASE, config.GECKO_NETWORK, net.quote(pool),
        timeframe, aggregate, max(1, min(int(limit), 1000)),
    )
    if before_ts:
        url += "&before_timestamp=%d" % int(before_ts)
    data = net.get_json(url, lim=GECKO)
    raw = ((data.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
    candles = []
    for row in raw:
        if not isinstance(row, list) or len(row) < 6:
            continue
        candles.append({
            "ts": int(row[0]),
            "open": _f(row[1]),
            "high": _f(row[2]),
            "low": _f(row[3]),
            "close": _f(row[4]),
            "volume": _f(row[5]),
        })
    candles.sort(key=lambda c: c["ts"])
    return candles


def fetch_meteora_pool(address):
    """
    Info pool dari API resmi Meteora DAMM v2.

    Dipakai untuk membedakan pool hidup dari pool mati/blacklist. Kalau pool
    tidak ada di API ini (mis. masih di bonding curve DBC), kembalikan None.
    """
    url = "%s/pools/%s" % (config.METEORA_BASE, net.quote(address))
    try:
        data = net.get_json(url, lim=METEORA, timeout=12, retries=1)
    except net.SourceError:
        return None
    if not isinstance(data, dict) or not data.get("address"):
        return None
    return data


_cache = {}


def fetch_rugcheck(mint):
    """Laporan keamanan Rugcheck. None kalau token belum terindeks."""
    now = time.time()
    hit = _cache.get(mint)
    if hit and now - hit[0] < config.SECURITY_CACHE_SEC:
        return hit[1]
    url = "%s/tokens/%s/report" % (config.RUGCHECK_BASE, net.quote(mint))
    try:
        report = net.get_json(url, lim=RUGCHECK)
    except net.SourceError:
        report = None
    if not isinstance(report, dict) or not report.get("mint"):
        report = None
    _cache[mint] = (now, report)
    return report


def clear_cache():
    _cache.clear()
