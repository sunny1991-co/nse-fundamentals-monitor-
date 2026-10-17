"""Bounded legacy source recovery; index metadata alone never promotes facts."""
from research_monitor.paths import data_root
import argparse
import csv
import hashlib
import time
from datetime import datetime, timezone
from pathlib import Path
import requests
import nse_annual_history as annual
import nse_legacy_annual_recovery as legacy
from research_monitor.legacy_xbrl import verify, VERSION, MAX_XML_BYTES
from nse_official_annual_pdf_auto_index import baseline_coverage

ROOT = data_root()
LEDGER = 'legacy_xbrl_verification.csv'
RUN_STATUS = 'legacy_xbrl_run_status.csv'


def write_rows(path, rows, fields=None):
    if fields is None:
        fields = list(dict.fromkeys(k for row in rows for k in row))
    temp = path.with_suffix('.tmp')
    with temp.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)
    temp.replace(path)


def candidate_priority(symbol, financial_year, baseline_present,
                       baseline_complete, verified_old_years):
    """Research source-order only; NEVER implies an issuer is trade-ready.

    First pursue an already verified opposite old FY and BOTH complete
    newer-FY baseline rows. Treat missing annual data as missing, not 0.
    """
    opposite = '2023-24' if financial_year == '2022-23' else '2022-23'
    has_other_old = (symbol, opposite) in verified_old_years
    complete_newer = baseline_complete.get(symbol, 0)
    present_newer = baseline_present.get(symbol, 0)
    if complete_newer == 2:
        return 0 if has_other_old else 1
    if present_newer == 2:
        return 2 if has_other_old else 3
    if present_newer == 1:
        return 4
    return 5


def download(session, url):
    if not legacy.official_xbrl_link(url):
        raise ValueError('OFFICIAL_XML_URL_REQUIRED')
    with session.get(url, timeout=30, allow_redirects=False, stream=True) as response:
        if response.status_code != 200:
            raise requests.HTTPError('HTTP_'+str(response.status_code), response=response)
        chunks, size = [], 0
        for chunk in response.iter_content(65536):
            size += len(chunk)
            if size > MAX_XML_BYTES:
                raise ValueError('XML_SIZE_LIMIT')
            chunks.append(chunk)
        return b''.join(chunks)


def collect(root=ROOT, limit=100, session=None, fetch=download, pause=time.sleep,
            require_preflight=False, plan_only=False):
    root = Path(root)
    if not 1 <= limit <= 500:
        raise ValueError('Limit must be 1..500')
    if plan_only and not require_preflight:
        raise ValueError('OFFLINE_PLAN_REQUIRES_FRESH_OFFICIAL_SOURCE_PREFLIGHT')
    manifest, _ = legacy.build(root)  # Rebuild from baselines/index; do not trust an edited manifest.
    candidates = [r for r in manifest if r['Recovery_Status'] == 'PENDING_INDEPENDENT_XBRL_REVIEW']
    total_index_candidates=len(candidates)
    if require_preflight:
        # The official NSE EQ status+catalog, exact source fiscal year,
        # broadcast date, index revision uniqueness, and existing source
        # failure ledger MUST all be current. Fail closed on any error.
        # No network is called during this independently regenerated
        # read-only source metadata preflight.
        import nse_legacy_xbrl_preflight as gate
        gate.snapshot(root)
        approved={
            (r.get('Symbol'),r.get('Financial_Year'),
             r.get('Official_XBRL_Source_URL'))
            for r in legacy.csv_rows(root/gate.OUTPUT)
            if r.get('Source_Review_State')==
                'READY_FOR_INDEPENDENT_ORIGINAL_XML_CONTEXT_REVIEW'
            and r.get('Metadata_Ready_For_XML')=='true'
            and r.get('Independent_Audited_Consolidated_Annual_XML_Checked_This_Run')=='false'
        }
        candidates=[
            r for r in candidates
            if (r['Symbol'],r['Financial_Year'],r['Source_URL']) in approved
        ]
    history = legacy.csv_rows(root/'annual_historical_results.csv')
    ledger = {r['Source_URL']: r for r in legacy.csv_rows(root/LEDGER)}
    baseline_present, baseline_complete = baseline_coverage(root)
    verified_old_years = legacy.verified_legacy_years(root)
    candidates.sort(key=lambda r: (
        # A never-attempted, freshly preflighted source is preferable to
        # repeated 404/timeout attempts. Within that group, prioritize
        # shorter paths to a genuine FOUR-year research comparison.
        r['Source_URL'] in ledger,
        candidate_priority(r['Symbol'],r['Financial_Year'],
                           baseline_present,baseline_complete,
                           verified_old_years),
        ledger.get(r['Source_URL'], {}).get('Checked_At_UTC', ''),
        r['Symbol'], r['Financial_Year']))
    stamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
    if plan_only:
        # The preview does NOT instantiate an HTTP session, issue XML
        # source requests, write verification attempts or promote a fact.
        preview = candidates[:min(10,limit)]
        return {
            'Checked_At_UTC':stamp,
            'Status':'SAFE_PREFLIGHT_RESEARCH_PLAN_ONLY_NO_HTTP',
            'Original_Index_Candidates_Before_Preflight':total_index_candidates,
            'Excluded_By_Current_Source_Preflight':
                total_index_candidates-len(candidates),
            'Freshly_Preflighted_Ready_Candidates':len(candidates),
            'Planned_Candidate_Limit':limit,
            'Proposed_Next_Source_Symbol_Years':[
                {'Symbol':r['Symbol'],'Financial_Year':r['Financial_Year'],
                 'Research_Priority':candidate_priority(
                     r['Symbol'],r['Financial_Year'],
                     baseline_present,baseline_complete,verified_old_years),
                 'Existing_Other_Legacy_Year_Verified_In_History':
                     (r['Symbol'], '2023-24' if r['Financial_Year']=='2022-23'
                      else '2022-23') in verified_old_years}
                for r in preview
            ],
            'XML_Network_Requests':0,'Original_XBRL_Facts_Verified':0,
            'Strict_Fundamental_Passes_Added':0,'Orders':'DISABLED',
        }
    own_session = session is None
    session = session or requests.Session()
    if own_session:
        session.headers.update({'User-Agent':'NSE-fundamentals-research/1.0', 'Accept':'application/xml'})
    status = dict(Checked_At_UTC=stamp, Candidate_Symbol_Years=len(candidates),
        Original_Index_Candidates_Before_Preflight=total_index_candidates,
        Excluded_By_Current_Source_Preflight=total_index_candidates-len(candidates),
        Independent_Source_Preflight_Required=require_preflight,
        Attempted=0, Added_Verified=0, Added_Six_Core_Complete=0, Status='COMPLETED_BOUNDED_RUN',
        Remaining_Candidates=len(candidates), Last_Error='', Parser_Version=VERSION)
    indexes = {}
    added = []
    try:
        for candidate in candidates[:limit]:
            url = candidate['Source_URL']; name = candidate['Index_File']
            if name not in indexes:
                indexes[name] = legacy.csv_rows(root/name)
            matches = [r for r in indexes[name] if legacy.field(r,'XBRL URL') == url
                       and legacy.audited_annual_consolidated(r, candidate['Fiscal_Year_End'])[0]
                       and legacy.field(r,'Symbol').upper() == candidate['Symbol']]
            record = dict(symbol=candidate['Symbol'], smName=candidate['Company'],
                          qe_Date=candidate['Fiscal_Year_End'], consolidated='Consolidated', audited='Audited')
            published = {annual.parse_date(legacy.field(r, 'Broadcast Date')) for r in matches}
            entry = dict(Symbol=candidate['Symbol'], Financial_Year=candidate['Financial_Year'],
                         Source_URL=url, Index_SHA256=candidate['Index_SHA256'], Source_SHA256='',
                         Checked_At_UTC=stamp, Parser_Version=VERSION, Status='UNVERIFIED', Reason='')
            # Publication date must come from the matching source index; never use fiscal-end date.
            if not matches or len(published) != 1 or None in published:
                entry['Reason']='EXACT_INDEX_ROW_OR_PUBLICATION_DATE_NOT_VERIFIED'
                ledger[url]=entry
                continue
            record['broadcast_Date']=next(iter(published)).isoformat()
            if annual.parse_date(record['broadcast_Date']) < annual.end_date(record):
                entry['Reason']='PUBLICATION_BEFORE_FISCAL_END';ledger[url]=entry;continue
            status['Attempted'] += 1
            try:
                body=fetch(session,url)
                entry['Source_SHA256']=hashlib.sha256(body).hexdigest()
                row, reason=verify(record,url,body)
                if row is not None:
                    # Never overwrite another independently verified source/revision.
                    if any(r.get('Symbol') == row['Symbol'] and r.get('Fiscal_Year_End') == row['Fiscal_Year_End'] for r in history):
                        reason='EXISTING_HISTORY_ROW_REQUIRES_REVISION_REVIEW';row=None
                if row is not None:
                    row['Index_SHA256']=candidate['Index_SHA256']
                    row['Filing_Published_At']=record['broadcast_Date']
                    history.append(row);added.append(row)
                    status['Added_Verified']+=1
                    status['Added_Six_Core_Complete']+=int(row['Six_Core_Fields_Complete'])
                    entry['Status']=row['Status']
                entry['Reason']=reason
            except requests.HTTPError as exc:
                code=getattr(exc.response,'status_code',None)
                entry['Reason']='SOURCE_HTTP_'+str(code)
                if code in (403,429):
                    entry['Status']='ACCESS_BLOCKED'
                    status['Status']='STOPPED_ON_403_429'
                elif code is not None and code >= 500:
                    status['Status']='STOPPED_ON_SOURCE_SERVER_ERROR'
            except requests.RequestException:
                entry['Reason']='SOURCE_NETWORK_ERROR'
                status['Status']='STOPPED_ON_NETWORK_ERROR'
            except (ValueError, TypeError):
                entry['Reason']='XML_RESPONSE_OR_SCHEMA_INVALID'
            ledger[url]=entry
            status['Last_Error']=entry['Reason']
            print(candidate['Symbol'],candidate['Financial_Year'],entry['Status'],entry['Reason'],flush=True)
            if status['Status'].startswith('STOPPED'):
                break
            pause(.25)
    finally:
        if own_session: session.close()
        if added:
            write_rows(root/'annual_historical_results.csv',history)
        if ledger:
            write_rows(root/LEDGER,list(ledger.values()))
        status['Remaining_Candidates']=len(candidates)-status['Added_Verified']
        write_rows(root/RUN_STATUS,[status])
    return status


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit',type=int,default=100)
    parser.add_argument('--require-preflight',action='store_true',
        help='Fail closed unless original source index and last attempt are independently safe')
    parser.add_argument('--plan-only',action='store_true',
        help='No HTTP requests; preview source-safe four-year research priorities')
    args=parser.parse_args()
    print(collect(limit=args.limit,require_preflight=args.require_preflight,
                  plan_only=args.plan_only))


if __name__ == '__main__': main()
