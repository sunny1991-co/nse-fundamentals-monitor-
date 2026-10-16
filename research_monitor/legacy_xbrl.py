"""Strict legacy annual source checks before reusing the annual fact extractor."""
import hashlib
import io
import math
from datetime import date
from decimal import Decimal, InvalidOperation
import xml.etree.ElementTree as ET
import nse_annual_history as annual
from nse_legacy_annual_recovery import official_xbrl_link

VERSION = 'legacy-source-v1'
MAX_XML_BYTES = 5_000_000


def verify(record, url, body):
    if not official_xbrl_link(url):
        return None, 'OFFICIAL_XML_URL_REQUIRED'
    if len(body) > MAX_XML_BYTES or b'<!DOCTYPE' in body.upper() or b'<!ENTITY' in body.upper():
        return None, 'UNSUPPORTED_XML_DOCUMENT'
    try:
        # iterparse yields ('start-ns', (prefix, URI)); build the actual QName map.
        namespaces = {prefix: uri for _, (prefix, uri) in ET.iterparse(io.BytesIO(body), events=['start-ns'])}
        root = ET.fromstring(body)
    except (ET.ParseError, ValueError):
        return None, 'INVALID_XML'
    end = annual.end_date(record)
    if end is None or (end.month, end.day) != (3, 31):
        return None, 'MARCH_FISCAL_YEAR_REQUIRED'
    expected_start = date(end.year-1, 4, 1)
    symbol = annual.symbol(record)
    facts = list(root.iter())
    for kind in ('context', 'unit'):
        ids = [n.get('id') for n in facts if annual.local_name(n.tag) == kind]
        if any(not item for item in ids) or len(ids) != len(set(ids)):
            return None, 'MISSING_OR_DUPLICATE_CONTEXT_UNIT_ID'
    for kind in ('context', 'unit'):
        ids = [n.get('id') for n in facts if annual.local_name(n.tag) == kind]
        if any(not item for item in ids) or len(ids) != len(set(ids)):
            return None, 'MISSING_OR_DUPLICATE_CONTEXT_UNIT_ID'
    symbols = {(n.text or '').strip().upper() for n in facts if annual.local_name(n.tag) == 'Symbol'}
    if not symbol or symbols != {symbol}:
        return None, 'XML_SYMBOL_MISMATCH_OR_MISSING'
    if annual.fact_basis(root) != {'Consolidated'}:
        return None, 'XML_BASIS_MISSING_OR_CONFLICTING'
    contexts = annual.parse_contexts(root)
    duration, instant = set(), set()
    for node in facts:
        if annual.local_name(node.tag) != 'context':
            continue
        cid = node.get('id'); ctx = contexts.get(cid, {})
        if ctx.get('other_dimensions') or ctx.get('basis') in ('Standalone', 'MIXED'):
            continue
        if any(annual.local_name(x.tag) == 'typedMember' or
               (annual.local_name(x.tag) == 'explicitMember' and
                any(word in (x.text or '').lower() for word in ('standalone', 'separate')))
               for x in node.iter()):
            continue
        ids = {(x.text or '').strip().upper() for x in node.iter()
               if annual.local_name(x.tag) == 'identifier' and x.get('scheme') in
               ('http://www.nseindia.com/NSESymbol', 'https://www.nseindia.com/NSESymbol')}
        if ids != {symbol}:
            continue
        if ctx.get('start') == expected_start and ctx.get('end') == end:
            duration.add(cid)
        if ctx.get('instant') == end:
            instant.add(cid)
    if not duration:
        return None, 'EXACT_APRIL_MARCH_CONTEXT_NOT_VERIFIED'
    if not instant:
        return None, 'YEAR_END_BALANCE_SHEET_CONTEXT_NOT_VERIFIED'
    for cid in duration:
        labels = {(n.text or '').strip().lower() for n in facts
                  if n.get('contextRef') == cid and annual.local_name(n.tag) == 'WhetherResultsAreAuditedOrUnaudited'}
        if labels != {'audited'}:
            return None, 'ANNUAL_CONTEXT_AUDIT_NOT_VERIFIED'
        for tag, expected in (('DateOfStartOfReportingPeriod', expected_start), ('DateOfEndOfReportingPeriod', end)):
            labels = {(n.text or '').strip() for n in facts if n.get('contextRef') == cid and annual.local_name(n.tag) == tag}
            if labels and labels != {expected.isoformat()}:
                return None, 'CONTEXT_AND_REPORTING_PERIOD_FACT_CONFLICT'

    def measure(text):
        prefix, _, name = (text or '').strip().partition(':')
        return namespaces.get(prefix), name

    units = {}
    for node in facts:
        if annual.local_name(node.tag) != 'unit':
            continue
        direct = [measure(x.text) for x in node if annual.local_name(x.tag) == 'measure']
        if direct == [('http://www.xbrl.org/2003/iso4217', 'INR')]:
            units[node.get('id')] = 'INR'
        numerator = [measure(x.text) for p in node.iter() if annual.local_name(p.tag) == 'unitNumerator'
                     for x in p if annual.local_name(x.tag) == 'measure']
        denominator = [measure(x.text) for p in node.iter() if annual.local_name(p.tag) == 'unitDenominator'
                       for x in p if annual.local_name(x.tag) == 'measure']
        if numerator == [('http://www.xbrl.org/2003/iso4217', 'INR')] and denominator == [('http://www.xbrl.org/2003/instance', 'shares')]:
            units[node.get('id')] = 'INR_PER_SHARE'

    groups = [(aliases, duration, 'INR_PER_SHARE' if name == 'Basic_EPS_Rs' else 'INR')
              for name, aliases in annual.ANNUAL_ALIASES.items()]
    groups += [(aliases, instant, 'INR') for aliases in annual.INSTANT_ALIASES.values()]
    groups += [(aliases, instant, 'INR') for aliases in
               (annual.TOTAL_BORROWING_ALIASES, annual.CURRENT_BORROWING_ALIASES, annual.NONCURRENT_BORROWING_ALIASES)]
    groups += [(aliases, duration, 'INR') for aliases in
               [annual.CAPEX_PPE_ALIASES, annual.CAPEX_INTANGIBLE_ALIASES, annual.CAPEX_COMBINED_ALIASES, *annual.CAPEX_ADDITIONAL_COMPONENTS]]
    for aliases, ids, unit in groups:
        values = set()
        for n in facts:
            if n.get('contextRef') not in ids or annual.local_name(n.tag) not in aliases:
                continue
            if n.get('{http://www.w3.org/2001/XMLSchema-instance}nil') in ('true', '1'):
                continue
            if not (n.text or '').strip():
                continue
            if units.get(n.get('unitRef')) != unit or 'scale' in n.attrib:
                return None, 'UNSUPPORTED_NUMERIC_UNIT_OR_SCALE'
            try:
                v = Decimal(n.text.strip())
                if not v.is_finite() or not math.isfinite(float(v)):
                    raise InvalidOperation
                values.add(v)
            except InvalidOperation:
                return None, 'NONFINITE_OR_INVALID_NUMERIC_FACT'
        if len(values) > 1:
            return None, 'CONFLICTING_DUPLICATE_OR_ALIAS_FACTS'
    # Restrict the existing extractor to exact annual contexts, never repair a
    # quarter context merely because human-readable annual labels are present.
    for node in list(root):
        if annual.local_name(node.tag) == 'context' and node.get('id') not in duration | instant:
            root.remove(node)
    row, reason = annual.parse_annual(record, url, ET.tostring(root))
    if row is None:
        return None, reason
    row.update(Source_SHA256=hashlib.sha256(body).hexdigest(), Source_Verifier=VERSION,
               Source_Verification='Official HTTPS XML; symbol/entity, exact annual period, audit, INR units and duplicate-fact checks')
    return row, ''
