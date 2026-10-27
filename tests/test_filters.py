#!/usr/bin/env python3
"""Uji filter tanpa jaringan: python3 -m unittest discover -s tests -v"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import filters  # noqa: E402

POOL = "PoolAddress111"
MINT = "MintAddress111"
NOW = datetime(2026, 10, 26, 1, 30, tzinfo=timezone.utc)


def make_report(top=None, lp_pct=100.0, mint_auth=None, freeze_auth=None,
                rugged=False, lp_holders=None, launchpad=None):
    top = top or []
    holders = [
        {"owner": owner, "address": "ata" + str(i), "pct": pct, "insider": False}
        for i, (owner, pct) in enumerate(top)
    ]
    return {
        "mint": MINT,
        "mintAuthority": mint_auth,
        "freezeAuthority": freeze_auth,
        "rugged": rugged,
        "launchpad": launchpad,
        "knownAccounts": {POOL: {"name": "Pool", "type": "AMM"}},
        "topHolders": holders,
        "markets": [{
            "pubkey": POOL,
            "lp": {
                "lpLockedPct": lp_pct,
                "holders": lp_holders or [],
            },
        }],
    }


def make_pool(age=120, liq=20000, mc=50000, buyers5=20):
    return {
        "pool": POOL, "mint": MINT, "name": "Test / SOL", "symbol": "TEST",
        "dex": "pumpswap", "age_sec": age, "liq_usd": liq, "mc_usd": mc,
        "buyers5": buyers5,
        "created_at": NOW - timedelta(seconds=age),
    }


def make_trades(count=15, kind="buy", age=60):
    return [
        {
            "ts": NOW - timedelta(seconds=age + i),
            "kind": kind,
            "wallet": "wallet%d" % i,
            "volume_usd": 10.0,
            "anomalous": False,
        }
        for i in range(count)
    ]


class FilterTests(unittest.TestCase):
    def test_pool_gets_through_all_security_checks(self):
        # pool 30% dibuang; sisa 10 holder = 28% -> lolos < 35
        report = make_report(
            top=[(POOL, 30.0)] + [("w%d" % i, 2.8) for i in range(10)],
            lp_pct=100.0,
        )
        result = filters.evaluate_security(make_pool(), report)
        self.assertTrue(result["passed"], result)
        self.assertAlmostEqual(result["top10_pct"], 28.0, places=1)
        self.assertEqual(result["details"]["lp_status"], "locked")

    def test_top10_too_concentrated(self):
        report = make_report(
            top=[(POOL, 20.0)] + [("whale", 50.0)] + [("w%d" % i, 1.0) for i in range(9)],
            lp_pct=100.0,
        )
        result = filters.evaluate_security(make_pool(), report)
        self.assertFalse(result["passed"])
        self.assertIn("top10", result["reasons"])
        self.assertGreaterEqual(result["top10_pct"], 35.0)

    def test_mint_authority_active(self):
        report = make_report(
            top=[(POOL, 20.0)] + [("w%d" % i, 1.0) for i in range(10)],
            mint_auth="ActiveAuthority",
        )
        result = filters.evaluate_security(make_pool(), report)
        self.assertFalse(result["passed"])
        self.assertIn("check_authorities", result["reasons"])

    def test_freeze_authority_active(self):
        report = make_report(
            top=[(POOL, 20.0)] + [("w%d" % i, 1.0) for i in range(10)],
            freeze_auth="ActiveAuthority",
        )
        result = filters.evaluate_security(make_pool(), report)
        self.assertFalse(result["passed"])
        self.assertIn("check_authorities", result["reasons"])

    def test_lp_unlocked_rejected(self):
        report = make_report(
            top=[(POOL, 20.0)] + [("w%d" % i, 1.0) for i in range(10)],
            lp_pct=12.0,
        )
        result = filters.evaluate_security(make_pool(), report)
        self.assertFalse(result["passed"])
        self.assertIn("lp", result["reasons"])
        self.assertEqual(result["details"]["lp_status"], "unlocked")

    def test_lp_burned_to_incinerator(self):
        report = make_report(
            top=[(POOL, 20.0)] + [("w%d" % i, 1.0) for i in range(10)],
            lp_pct=0.0,
            lp_holders=[{
                "owner": "1nc1nerator11111111111111111111111111111111",
                "address": "burn",
                "pct": 100.0,
            }],
        )
        result = filters.evaluate_security(make_pool(), report)
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["details"]["lp_status"], "burned")

    def test_rugged_rejected(self):
        report = make_report(
            top=[(POOL, 20.0)] + [("w%d" % i, 1.0) for i in range(10)],
            rugged=True,
        )
        result = filters.evaluate_security(make_pool(), report)
        self.assertFalse(result["passed"])

    def test_pumpfun_without_lp_token_is_burned(self):
        report = make_report(
            top=[(POOL, 20.0)] + [("w%d" % i, 1.0) for i in range(10)],
            launchpad="pumpfun",
        )
        report["markets"] = []
        pool = make_pool()
        pool["dex"] = "pump-fun"
        result = filters.evaluate_security(pool, report)
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["details"]["lp_status"], "burned")

    def test_top10_needs_enough_real_holders(self):
        # Bonding curve 99%, hanya 2 holder nyata -> top10 belum berarti.
        report = make_report(
            top=[(POOL, 99.0), ("a", 0.5), ("b", 0.5)],
            lp_pct=100.0,
        )
        result = filters.evaluate_security(make_pool(), report)
        self.assertFalse(result["passed"])
        self.assertIn("top10", result["reasons"])

    def test_top10_needs_meaningful_float(self):
        # 10 holder nyata, tapi totalnya cuma 2% supply -> float terlalu tipis.
        report = make_report(
            top=[(POOL, 98.0)] + [("w%d" % i, 0.2) for i in range(10)],
            lp_pct=100.0,
        )
        result = filters.evaluate_security(make_pool(), report)
        self.assertFalse(result["passed"])
        self.assertIn("top10", result["reasons"])

    def test_cheap_filters(self):
        cases = [
            (make_pool(age=601), False),
            (make_pool(liq=4999), False),
            (make_pool(mc=7999), False),
            (make_pool(mc=100001), False),
            (make_pool(buyers5=14), False),
            (make_pool(), True),
        ]
        for pool, expected in cases:
            ok, _name, detail = filters.evaluate_cheap(pool)
            self.assertEqual(ok, expected, (pool, detail))

    def test_buyer_window_unique_wallets(self):
        trades = make_trades(15)
        trades += [
            {"ts": NOW - timedelta(seconds=20), "kind": "buy", "wallet": "wallet0",
             "anomalous": False},
            {"ts": NOW - timedelta(seconds=200), "kind": "buy", "wallet": "old",
             "anomalous": False},
        ]
        stats = filters.buyer_window(trades, NOW, window_sec=120)
        self.assertEqual(stats["buyers"], 15)
        self.assertEqual(stats["buys"], 16)  # wallet0 beli dua kali

    def test_buyer_window_rejects_old_and_anomalous(self):
        trades = make_trades(15, age=200)
        stats = filters.buyer_window(trades, NOW, window_sec=120)
        self.assertEqual(stats["buyers"], 0)

        trades = make_trades(20)
        for trade in trades:
            trade["anomalous"] = True
        stats = filters.buyer_window(trades, NOW, window_sec=120)
        self.assertEqual(stats["buyers"], 0)

    def test_buyer_window_uses_pool_age_when_young(self):
        created = NOW - timedelta(seconds=45)
        stats = filters.buyer_window(make_trades(15, age=5), NOW, created_at=created)
        self.assertEqual(stats["window_sec"], 45)

    def test_min_buyers_threshold(self):
        ok, _detail, stats = filters.check_buyers_window(make_trades(14), NOW)
        self.assertFalse(ok)
        self.assertEqual(stats["buyers"], 14)
        ok, _detail, _stats = filters.check_buyers_window(make_trades(15), NOW)
        self.assertTrue(ok)

    def test_truncated_trades_still_pass_when_enough_buyers(self):
        trades = make_trades(300, age=1)
        for trade in trades:
            trade["ts"] = NOW - timedelta(seconds=1)
        stats = filters.buyer_window(trades, NOW, window_sec=120)
        self.assertTrue(stats["truncated"])
        ok, _detail, _stats = filters.check_buyers_window(trades, NOW)
        self.assertTrue(ok)

    # -------------------------------------------------------------- Meteora
    def test_meteora_blacklisted_pool_is_dead(self):
        report = make_report(
            top=[(POOL, 20.0)] + [("w%d" % i, 1.0) for i in range(10)],
            lp_pct=0.0,
        )
        pool = make_pool()
        pool["dex"] = "meteora-damm-v2"
        meteora = {"is_blacklisted": True, "tvl": 0.0}
        result = filters.evaluate_security(pool, report, meteora=meteora)
        self.assertFalse(result["passed"])
        self.assertEqual(result["details"]["lp_status"], "dead")
        self.assertIn("pool_dead", result["reasons"])

    def test_meteora_empty_tvl_is_dead(self):
        pool = make_pool()
        pool["dex"] = "meteora-damm-v2"
        self.assertTrue(filters.meteora_dead({"tvl": 0.0, "is_blacklisted": False}))

    def test_meteora_permanent_lock_overrides_rugcheck_zero(self):
        # Rugcheck melihat 0%, tapi API Meteora menyebut 99,5% terkunci permanen.
        report = make_report(
            top=[(POOL, 20.0)] + [("w%d" % i, 1.0) for i in range(10)],
            lp_pct=0.0,
        )
        pool = make_pool()
        pool["dex"] = "meteora-damm-v2"
        meteora = {
            "is_blacklisted": False,
            "tvl": 20000.0,
            "permanent_lock_liquidity": 19900.0,
        }
        result = filters.evaluate_security(pool, report, meteora=meteora)
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["details"]["lp_status"], "locked")
        self.assertAlmostEqual(result["details"]["lp_pct"], 99.5, places=1)

    def test_meteora_dbc_is_bonding_curve(self):
        report = make_report(top=[(POOL, 20.0)] + [("w%d" % i, 1.0) for i in range(10)])
        report["markets"] = []
        pool = make_pool()
        pool["dex"] = "meteora-dbc"
        result = filters.evaluate_security(pool, report)
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["details"]["lp_status"], "burned")


    def test_launchpad_as_object_is_handled(self):
        # Rugcheck kadang mengirim launchpad sebagai objek, bukan string.
        report = make_report(
            top=[("w%d" % i, 3.0) for i in range(10)],
            lp_pct=0.0,
        )
        report["markets"] = []
        report["launchpad"] = {"name": "pump.fun", "id": "pumpfun"}
        result = filters.evaluate_security(make_pool(), report)
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["details"]["lp_status"], "burned")


if __name__ == "__main__":
    unittest.main(verbosity=2)
