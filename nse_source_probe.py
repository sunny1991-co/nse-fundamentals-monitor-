#!/usr/bin/env python3
"""Diagnostic probe of annual XBRL context periods and fundamental aliases."""
import json
import requests
import xml.etree.ElementTree as ET
from datetime import date

URL = "https://nsearchives.nseindia.com/corporate/xbrl/INTEGRATED_FILING_INDAS_1736893_13102026033734_WEB.xml"
LANDING = "https://www.nseindia.com/companies-listing/corporate-integrated-filing"

def local(tag):
    return tag.rsplit("}", 1)[-1].split(":")[-1]

s = requests.Session()
s.headers.update({"User-Agent":"Mozilla/5.0","Accept":"application/json, text/plain, */*","Referer":LANDING,"X-Requested-With":"XMLHttpRequest"})
r=s.get(LANDING,timeout=30)
print("NSE_LANDING_HTTP",r.status_code)
r.raise_for_status()
r=s.get(URL,timeout=40)
print("ANNUAL_XBRL_HTTP",r.status_code,"bytes",len(r.content))
r.raise_for_status()
root=ET.fromstring(r.content)
contexts={}
for c in root.iter():
    if local(c.tag).lower()!="context" or not c.attrib.get("id"):
        continue
    p=next((x for x in c.iter() if local(x.tag).lower()=="period"),None)
    if p is None: continue
    start=end=instant=None
    for x in p:
        name=local(x.tag).lower()
        if name=="startdate": start=(x.text or "").strip()
        elif name=="enddate": end=(x.text or "").strip()
        elif name=="instant": instant=(x.text or "").strip()
    members=[]
    for x in c.iter():
        if local(x.tag).lower()=="explicitmember":
            members.append((x.attrib.get("dimension",""),(x.text or "").strip()))
    days=None
    try:
        if start and end: days=(date.fromisoformat(end)-date.fromisoformat(start)).days
    except Exception: pass
    contexts[c.attrib["id"]]={"start":start,"end":end,"instant":instant,"days":days,"members":members}
print("CONTEXT_COUNT",len(contexts))
print("ANNUAL_CONTEXTS",json.dumps({k:v for k,v in contexts.items() if v["days"] and 330<=v["days"]<=400 and not v["members"]},ensure_ascii=False)[:5000])
print("INSTANT_CONTEXTS",json.dumps({k:v for k,v in contexts.items() if v["instant"]=="2026-03-31" and not v["members"]},ensure_ascii=False)[:3000])
aliases={
"Revenue_Operations_Cr":["RevenueFromOperations","RevenueFromOperationsNetOfExciseDuty"],
"Net_Profit_Cr":["ProfitLossForPeriod","ProfitLoss"],
"Basic_EPS_Rs":["BasicEarningsLossPerShare","EarningsPerShareBasic","BasicEPS"],
"Profit_Before_Tax_Cr":["ProfitLossBeforeTax","ProfitBeforeTax"],
"Total_Expenses_Cr":["TotalExpenses"],
"Finance_Cost_Cr":["FinanceCosts","FinanceCost"],
"Depreciation_Amortisation_Cr":["DepreciationDepletionAndAmortisationExpense","DepreciationAndAmortisationExpense"],
"Cash_Flow_Operating_Cr":["NetCashFlowsFromUsedInOperatingActivities","NetCashFlowsFromOperatingActivities","CashFlowsFromUsedInOperatingActivities","CashFlowsFromOperatingActivities","NetCashFlowFromOperatingActivities"],
"Total_Assets_Cr":["Assets","TotalAssets"],
"Total_Equity_Cr":["Equity","TotalEquity"],
"Current_Liabilities_Cr":["LiabilitiesCurrent","CurrentLiabilities"],
"Cash_CashEquivalents_Cr":["CashAndCashEquivalents","CashCashEquivalents","CashAndCashEquivalentsAtCarryingValue"],
"CapEx_Cr":["PaymentsToAcquirePropertyPlantAndEquipment","PurchaseOfPropertyPlantAndEquipment","PaymentsToAcquirePropertyPlantAndEquipmentAndIntangibleAssets","PurchaseOfPropertyPlantAndEquipmentAndIntangibleAssets","PaymentsToAcquireTangibleAssets"],
"Total_Borrowings_Cr":["Borrowings","TotalBorrowings","BorrowingsCurrent","CurrentBorrowings","BorrowingsNoncurrent","NoncurrentBorrowings","BorrowingsNonCurrent"],
}
annual_ids={k for k,v in contexts.items() if v["days"] and 330<=v["days"]<=400 and not v["members"]}
instant_ids={k for k,v in contexts.items() if v["instant"]=="2026-03-31" and not v["members"]}
matched={}
for node in root.iter():
    name=local(node.tag)
    cref=node.attrib.get("contextRef","")
    if not cref: continue
    for metric,names in aliases.items():
        if name.lower() not in {x.lower() for x in names}: continue
        if cref not in annual_ids and cref not in instant_ids: continue
        bucket="annual_duration" if cref in annual_ids else "instant"
        matched.setdefault(metric,[]).append({"tag":name,"context":cref,"period":bucket,"value":(node.text or "").strip(),"unitRef":node.attrib.get("unitRef","")})
print("ALIAS_MATCHES",json.dumps(matched,ensure_ascii=False)[:12000])
basis=[]
for node in root.iter():
    if local(node.tag).lower()=="natureofreportstandaloneconsolidated":
        basis.append({"value":(node.text or "").strip(),"context":node.attrib.get("contextRef")})
print("REPORT_BASIS_FACTS",json.dumps(basis,ensure_ascii=False))

print("EARNINGS_AND_CAPEX_TAG_CANDIDATES")
found=[]
seen=set()
needles=("earnings","per share","eps","acquire","purchase","capex","propertyplant","intangible","capitalexpenditure","purchaseproperty","paymentsfor")
for node in root.iter():
    name=local(node.tag)
    low=name.lower()
    if not any(x in low for x in needles):
        continue
    cref=node.attrib.get("contextRef","")
    if cref not in annual_ids and cref not in instant_ids:
        continue
    raw=(node.text or "").strip()
    if not raw or len(raw)>100:
        continue
    key=(name,cref,raw)
    if key in seen: continue
    seen.add(key)
    found.append({"tag":name,"context":cref,"value":raw,"unitRef":node.attrib.get("unitRef","")})
print(json.dumps(found[:100],ensure_ascii=False)[:14000])

