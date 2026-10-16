"""Synthetic NSE-only Upstox reference pilot tests; no network or real secrets."""
import csv
from pathlib import Path
import tempfile
import unittest

import upstox_reference_pilot as pilot

TOKEN="synthetic-token-never-print-or-commit"
ISIN="INE002A01018"


class FakeResponse:
    status_code=200
    def __init__(self,data):
        self.data=data
        self.content=b'{"status":"success"}'
    def json(self):
        return {"status":"success","data":self.data}


class FakeSession:
    def __init__(self,fail=None):
        self.calls=[]
        self.fail=fail
    def get(self,url,**kwargs):
        self.calls.append((url,kwargs))
        if self.fail:
            class Failure(FakeResponse):
                status_code=429
            return Failure({})
        kind=url.rsplit("/",1)[-1]
        if kind=="profile":
            data={"sector":"Refineries"}
        elif kind=="balance-sheet":
            data={"type":"consolidated","time_period":"yearly",
                  "units_in":"crore","history":[
                      {"period":"Mar 2023","total_asset":110.},
                      {"period":"Mar 2024","total_asset":120.}
                  ]}
        elif kind=="income-statement":
            data={"type":"consolidated","time_period":"yearly",
                  "units_in":"crore","income_statement":[
                      {"category":"revenue","history":[
                          {"period":"Mar 2023","value":100},
                          {"period":"Mar 2024","value":120}]},
                      {"category":"net_profit","history":[
                          {"period":"Mar 2023","value":20},
                          {"period":"Mar 2024","value":25}]}]}
        elif kind=="cash-flow":
            data={"type":"consolidated","time_period":"yearly",
                  "units_in":"crore","cash_flow":[
                      {"category":"operating","history":[
                          {"period":"Mar 2023","value":18},
                          {"period":"Mar 2024","value":30}]}]}
        else:
            raise AssertionError(kind)
        return FakeResponse(data)


def make_mapping(root,lines):
    path=Path(root)/"EQUITY_L.csv"
    with path.open("w",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=["SYMBOL","ISIN","SERIES"])
        writer.writeheader()
        writer.writerows(lines)
    return path


class UpstoxNSEReferencePilotTests(unittest.TestCase):
    def test_nse_eq_only_and_baseline_intersection(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for name in ("FY2425.csv",
                         "NSE_FY2025-26_Consolidated_Fundamentals.csv"):
                (root/name).write_text("Symbol\nRELIANCE\nICICIBANK\n")
            path=make_mapping(root,[
                {"SYMBOL":"NSE_SMEX","ISIN":"INE111A01011","SERIES":"SM"},
                {"SYMBOL":"RELIANCE","ISIN":ISIN,"SERIES":"EQ"},
                {"SYMBOL":"NOT_IN_TWO_BASELINES","ISIN":"INE111A01011","SERIES":"EQ"},
            ])
            chosen,source=pilot.choose_symbols(root,5,path)
            self.assertEqual(chosen,[("RELIANCE",ISIN)])
            self.assertIn("LOCAL_NSE_EQ_CSV",source)

    def test_reference_data_for_both_years_but_zero_nse_verified(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            path=make_mapping(root,[{"SYMBOL":"RELIANCE","ISIN":ISIN,"SERIES":"EQ"}])
            session=FakeSession()
            report=pilot.run(root,TOKEN,5,mapping=path,
                             session=session,pause=lambda _:None)
            self.assertEqual(report["tested_symbols"],1)
            self.assertEqual(report["requests_success"],4)
            self.assertEqual(report["fy2022_23_reference_complete_symbols"],1)
            self.assertEqual(report["fy2023_24_reference_complete_symbols"],1)
            self.assertEqual(report["annual_strict_pass_changes"],0)
            self.assertEqual(report["nse_xbrl_verified_years_added"],0)
            self.assertEqual(report["nse_official_sectors_added"],0)
            self.assertEqual(report["live_orders"],"DISABLED")
            self.assertFalse(report["results"][0]["is_nse_audited_verified"])
            self.assertFalse(report["results"][0]["nse_sector_verified"])
            self.assertNotIn(TOKEN,str(report))
            self.assertEqual(len(session.calls),4)
            for endpoint,args in session.calls:
                self.assertIn("/v2/fundamentals/"+ISIN+"/",endpoint)
                self.assertEqual(args["headers"]["Authorization"],"Bearer "+TOKEN)
                self.assertFalse(args["allow_redirects"])
                self.assertEqual(args["params"].get("type"),"consolidated") if args["params"] else None
            self.assertEqual(report["results"][0]["financials"]["Mar 2023"][
                "operating_cash_flow_vendor_crore"],18.)

    def test_source_fails_closed_on_wrong_statement_type(self):
        class BadSession(FakeSession):
            def get(self,url,**kwargs):
                response=super().get(url,**kwargs)
                if url.endswith("/income-statement"):
                    response.data["type"]="standalone"
                return response
        with self.assertRaisesRegex(pilot.BlockedSource,"CONSOLIDATED"):
            pilot.read_fundamental(BadSession(),TOKEN,ISIN,"income-statement")

    def test_rate_limit_halts_without_token_echo_or_orders(self):
        with tempfile.TemporaryDirectory() as d:
            path=make_mapping(d,[{"SYMBOL":"RELIANCE","ISIN":ISIN,"SERIES":"EQ"}])
            session=FakeSession(fail=True)
            report=pilot.run(d,TOKEN,5,mapping=path,
                             session=session,pause=lambda _:None)
            self.assertTrue(report["stopped_for_api_block"])
            self.assertEqual(len(session.calls),1)
            self.assertEqual(report["results"][0]["status"],"UPSTOX_HTTP_429")
            self.assertNotIn(TOKEN,str(report))
            self.assertEqual(report["live_orders"],"DISABLED")

    def test_invalid_isin_rejected_before_network(self):
        session=FakeSession()
        for bad in ("NSE_EQ|INE002A01018","../../../orders","123456789012"):
            with self.assertRaises(ValueError):
                pilot.read_fundamental(session,TOKEN,bad,"profile")
        self.assertEqual(session.calls,[])

    def test_fallback_only_documented_nse_example(self):
        with tempfile.TemporaryDirectory() as d:
            chosen,state=pilot.choose_symbols(d,5,mapping=None)
            self.assertEqual(chosen,[("RELIANCE",ISIN)])
            self.assertIn("DOCUMENTED",state)


if __name__=="__main__":
    unittest.main()
