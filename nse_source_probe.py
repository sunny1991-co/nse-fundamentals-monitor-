#!/usr/bin/env python3
"""Temporary, small diagnostic to verify where NSE exposes consolidated/standalone metadata."""
import json
import re
import time
import requests
import xml.etree.ElementTree as ET

LANDING = "https://www.nseindia.com/companies-listing/corporate-integrated-filing"
API = "https://www.nseindia.com/api/integrated-filing-results"

def local(tag):
    return tag.rsplit("}", 1)[-1].split(":")[-1]

def items(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "results", "records", "result", "items"):
            val = payload.get(key)
            if isinstance(val, list):
                return val
            if isinstance(val, dict):
                found = items(val)
                if found:
                    return found
    return []

s = requests.Session()
s.headers.update({
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json, text/plain, */*",
    "Referer": LANDING,
    "X-Requested-With": "XMLHttpRequest",
})
page = s.get(LANDING, timeout=30)
print("LANDING_STATUS", page.status_code)
page.raise_for_status()
r = s.get(API, params={"type":"Integrated Filing- Financials","index":"equities","page":0,"size":3}, timeout=30)
print("API_STATUS", r.status_code)
r.raise_for_status()
payload = r.json()
print("PAYLOAD_KEYS", list(payload) if isinstance(payload, dict) else type(payload).__name__)
records = items(payload)
print("RECORD_COUNT", len(records))
for i, rec in enumerate(records[:3]):
    print("RECORD", i, json.dumps(rec, ensure_ascii=False, default=str)[:6000])
    url = str(rec.get("xbrl") or rec.get("xbrlUrl") or "")
    if not url:
        continue
    xml_resp = s.get(url, timeout=30)
    print("XML_STATUS", xml_resp.status_code, "XML_BYTES", len(xml_resp.content), "URL", url)
    xml_resp.raise_for_status()
    root = ET.fromstring(xml_resp.content)
    print("ROOT", root.tag)
    found = []
    for node in root.iter():
        name = local(node.tag)
        text = (node.text or "").strip()
        combined = (name + " " + text).lower()
        if text and len(text) < 180 and any(k in combined for k in ("consolidated", "standalone", "natureofreport", "natureofstatement", "reportingtype", "auditedunaudited")):
            found.append((name, text, node.attrib))
    print("BASIS_FACT_CANDIDATES", json.dumps(found[:60], ensure_ascii=False, default=str))
    contexts = []
    for node in root.iter():
        if local(node.tag).lower() != "context":
            continue
        members = []
        for child in node.iter():
            if local(child.tag).lower() in ("explicitmember", "typedmember"):
                members.append((child.attrib, (child.text or "").strip()))
        if members:
            contexts.append((node.attrib.get("id"), members))
    print("DIMENSION_CONTEXTS", json.dumps(contexts[:15], ensure_ascii=False, default=str))
    break
