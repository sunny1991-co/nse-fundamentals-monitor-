"""Pure synthetic Upstox BOD NSE EQ/ISIN reference tests; no external calls."""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from research_monitor import upstox_instrument_master as m
from upstox_reference_pilot import choose_symbols


def make_record(symbol, isin, *, segment="NSE_EQ", exchange="NSE",
                kind="EQ", key=None, lot=1):
    return {
        "segment":segment,"exchange":exchange,
        "instrument_type":kind,"trading_symbol":symbol,
        "isin":isin,"instrument_key":key or f"{segment}|{isin}",
        "lot_size":lot,"name":f"SYNTHETIC {symbol}",
    }


def isin(i):
    return "INE"+f"{i:09d}"


def baseline(root, symbols):
    for filename in ("FY2425.csv",
                     "NSE_FY2025-26_Consolidated_Fundamentals.csv"):
        with (Path(root)/filename).open("w",newline="") as file:
            writer=csv.writer(file)
            writer.writerow(["Symbol","Company"])
            for symbol in symbols:
                writer.writerow([symbol,"Synthetic "+symbol])


class FakeResponse:
    status_code=200
    def __init__(self, data, code=200):
        self.data=data
        self.status_code=code
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False
    def iter_content(self, chunk_size):
        for start in range(0,len(self.data),19):
            yield self.data[start:start+19]


class FakeSession:
    def __init__(self, data, code=200):
        self.data=data
        self.code=code
        self.calls=[]
        self.closed=False
    def get(self,url,**kwargs):
        self.calls.append((url,kwargs))
        return FakeResponse(self.data,self.code)
    def close(self):
        self.closed=True


class InstrumentReferenceTests(unittest.TestCase):
    def make_master(self):
        symbols=["AAA","BBB","CCC","DDD","EEE","FFF","GGG","HHH"]
        rows=[
            make_record(symbol,isin(i+1))
            for i,symbol in enumerate(symbols)
        ]
        rows.extend([
            make_record("SME",isin(90),kind="SM"),
            make_record("SME",isin(90),segment="BSE_EQ",exchange="BSE"),
            make_record("DERIVATIVE",isin(91),segment="NSE_FO",kind="FUT"),
            make_record("PENNY",isin(92),kind="BE"),
            make_record("AAA",isin(1),segment="BSE_EQ",exchange="BSE"),
            make_record("OUTSIDE_BASELINE",isin(93)),
            make_record("ETF",isin(94),lot=10),
        ])
        return symbols,rows

    def test_nse_only_5_issuer_reference_map_and_no_baseline_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            symbols,rows=self.make_master()
            baseline(td,symbols)
            before={p.name:p.read_bytes() for p in Path(td).glob("*.csv")}
            raw=gzip.compress(json.dumps(rows).encode())
            session=FakeSession(raw)
            result=m.sync(td,session=session)
            self.assertEqual(result["verified_by_vendor_crosswalk"],8)
            self.assertEqual(result["nse_segment_eq_rows"],10)
            self.assertEqual(result["overlapping_baseline_symbols"],8)
            self.assertFalse(result["verified_mainboard_history"])
            self.assertFalse(result["nse_xbrl_verified"])
            self.assertFalse(result["automatic_trading"])
            self.assertEqual(result["source_sha256"],hashlib.sha256(raw).hexdigest())
            url,kwargs=session.calls[0]
            self.assertEqual(url,m.MASTER_URL)
            self.assertEqual(len(session.calls),1)
            self.assertFalse(kwargs["allow_redirects"])
            self.assertTrue(kwargs["stream"])
            self.assertNotIn("Authorization",kwargs["headers"])
            self.assertEqual(before,{
                p.name:p.read_bytes() for p in Path(td).glob("*.csv")
            })
            map_path=Path(result["mapping_path"])
            self.assertTrue(map_path.exists())
            chosen,provenance=choose_symbols(td,5,mapping=map_path)
            self.assertEqual(len(chosen),5)
            self.assertTrue(all(s in symbols for s,_ in chosen))
            self.assertTrue(all(i.startswith("INE") for _,i in chosen))
            self.assertIn("UPSTOX_CURRENT_NSE_EQ_BOD_VENDOR_MAP",provenance)
            metadata_path=map_path.with_name(m.METADATA_NAME)
            self.assertEqual(
                json.loads(metadata_path.read_text())["source_sha256"],
                hashlib.sha256(raw).hexdigest()
            )

    def test_symbol_and_isin_conflicts_never_resurrect(self):
        symbols,rows=self.make_master()
        rows.extend([
            make_record("AAA",isin(45)),
            make_record("AAA",isin(1)),
            make_record("BBB",isin(3)), # collides with CCC ISIN
        ])
        mapped,stats=m._map_symbols(rows,set(symbols))
        self.assertNotIn("AAA",mapped)
        self.assertNotIn("BBB",mapped)
        self.assertNotIn("CCC",mapped)
        self.assertGreaterEqual(stats["conflicting_symbols"],2)
        self.assertGreaterEqual(stats["conflicting_isins"],1)

    def test_unauthorized_http_fails_without_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            symbols,rows=self.make_master()
            baseline(td,symbols)
            response=FakeSession(gzip.compress(json.dumps(rows).encode()),code=403)
            with self.assertRaisesRegex(m.InstrumentMapUnavailable,
                                        "MASTER_HTTP_403"):
                m.sync(td,session=response)
            self.assertFalse(
                list(Path(td).rglob(m.MAP_NAME))
            )

    def test_wrong_json_or_gzip_fails_closed(self):
        for raw in (
            b"not gzip",gzip.compress(b"{}"),
            gzip.compress(b"not JSON"),
        ):
            with self.subTest(input_sha=hashlib.sha256(raw).hexdigest()):
                with self.assertRaises(m.InstrumentMapUnavailable):
                    m._download_master(FakeSession(raw))

    def test_large_expanded_source_bounded(self):
        raw=gzip.compress(json.dumps(self.make_master()[1]).encode())
        with patch.object(m,"MAX_EXPANDED_BYTES",10):
            with self.assertRaisesRegex(m.InstrumentMapUnavailable,
                                        "MASTER_UNCOMPRESSED_SIZE_LIMIT"):
                m._download_master(FakeSession(raw))

    def test_no_baselines_blocks_current_bod_crosswalk(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(m.InstrumentMapUnavailable,
                                        "BOTH_ANNUAL_BASELINE"):
                m.sync(td,session=FakeSession(b""))
            self.assertEqual(list(Path(td).rglob(m.MAP_NAME)),[])

    def test_unknown_or_derivative_instruments_never_pass(self):
        symbols,rows=self.make_master()
        with self.assertRaisesRegex(m.InstrumentMapUnavailable,
                                    "TOO_FEW_UNAMBIGUOUS"):
            m._map_symbols([
                row for row in rows if row["segment"]!="NSE_EQ"
                or row["instrument_type"]!="EQ"
            ],set(symbols))

    def test_original_local_mapping_conflict_does_not_reenter(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"EQUITY_L.csv"
            with p.open("w",newline="") as out:
                w=csv.writer(out)
                w.writerow(["SYMBOL","ISIN","SERIES"])
                w.writerows([
                    ["RELIANCE",isin(1),"EQ"],
                    ["RELIANCE",isin(2),"EQ"],
                    ["RELIANCE",isin(1),"EQ"],
                    ["AAA",isin(3),"EQ"],
                ])
            result,_=choose_symbols(td,5,p)
            self.assertEqual(result,[("AAA",isin(3))])


if __name__=="__main__":
    unittest.main()
