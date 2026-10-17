"""Bounded legacy source recovery; index metadata alone never promotes facts."""
from research_monitor.paths import data_root
import argparse
import csv
import hashlib
import time
from datetime import datetime, timezone
from pathlib import Path
from collections import Counter
import json
import requests
import nse_annual_history as annual
import nse_legacy_annual_recovery as legacy
from research_monitor.legacy_xbrl import verify, VERSION, MAX_XML_BYTES
from nse_official_annual_pdf_auto_index import baseline_coverage

ROOT = data_root()
LEDGER = 'legacy_xbrl_verification.csv'
RUN_STATUS = 'legacy_xbrl_run_status.csv'
YIELD_WINDOW = 50
YIELD_MIN_SOURCE_DOWNLOADS = 30
YIELD_MIN_STRUCTURAL_RATIO = 0.90
STRUCTURAL_REASONS = frozenset({
    'EXACT_APRIL_MARCH_CONTEXT_NOT_VERIFIED',
    'XML_SYMBOL_MISMATCH_OR_MISSING',
    'YEAR_END_BALANCE_SHEET_CONTEXT_NOT_VERIFIED',
    'XML_BASIS_MISSING_OR_CONFLICTING',
})



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


def original_xml_source_yield(ledger_rows):
    """Recent *actual source XML responses* only; not CSV-only candidates.

    A chronic near-100% structural failure rate should halt unattended
    source collection. This does not deem missing years absent or mark
    old PDF comparatives as independent audited XBRL.
    """
    source_reads=[
        r for r in ledger_rows
        if r.get('Parser_Version') == VERSION
        and len(r.get('Source_SHA256','')) == 64
        and r.get('Checked_At_UTC')
    ]
    source_reads.sort(key=lambda r:r.get('Checked_At_UTC',''))
    recent=source_reads[-YIELD_WINDOW:]
    rejected=sum(r.get('Reason') in STRUCTURAL_REASONS for r in recent)
    successful=sum(
        r.get('Status') in ('VERIFIED','VERIFIED_COMPLETE',
                             'VERIFIED_INCOMPLETE')
        and not r.get('Reason') for r in recent
    )
    pause=(
        len(recent)>=YIELD_MIN_SOURCE_DOWNLOADS
        and rejected/len(recent)>=YIELD_MIN_STRUCTURAL_RATIO
        and successful==0
    )
    return {
        'Source_Yield_Recent_XML_Responses':len(recent),
        'Source_Yield_Recent_Structural_Rejects':rejected,
        'Source_Yield_Recent_Verified':successful,
        'Source_Yield_Unattended_Pause_Recommended':pause,
        'Source_Yield_Pause_Reason':(
            'RECENT_ORIGINAL_NSE_XML_STRUCTURAL_REJECT_RATE_TOO_HIGH'
            if pause else ''
        ),
    }


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
            require_preflight=False, plan_only=False, respect_yield_guard=False):
    root = Path(root)
    if not 1 <= limit <= 500:
        raise ValueError('Limit must be 1..500')
    if plan_only and not require_preflight:
        raise ValueError('OFFLINE_PLAN_REQUIRES_FRESH_OFFICIAL_SOURCE_PREFLIGHT')
    if respect_yield_guard and not require_preflight:
        raise ValueError('UNATTENDED_SOURCE_YIELD_GUARD_REQUIRES_OFFICIAL_PREFLIGHT')
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
    original_attempt_rows=legacy.csv_rows(root/LEDGER)
    ledger = {r['Source_URL']: r for r in original_attempt_rows}
    yield_health=original_xml_source_yield(original_attempt_rows)
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
            **yield_health,
        }
    if respect_yield_guard and yield_health[
            'Source_Yield_Unattended_Pause_Recommended']:
        # Deliberately NO HTTP session, XML download, ledger mutation
        # or source financial promotion in an unattended low-yield run.
        status=dict(
            Checked_At_UTC=stamp,
            Candidate_Symbol_Years=len(candidates),
            Original_Index_Candidates_Before_Preflight=total_index_candidates,
            Excluded_By_Current_Source_Preflight=
                total_index_candidates-len(candidates),
            Independent_Source_Preflight_Required=True,
            Attempted=0,Added_Verified=0,Added_Six_Core_Complete=0,
            Status='UNATTENDED_ORIGINAL_XML_SOURCE_ZERO_YIELD_PAUSED',
            Remaining_Candidates=len(candidates),Last_Error='',
            Parser_Version=VERSION,
            Attempted_Unverified_This_Run=0,
            Context_Diagnostic_Reason_Counts_JSON='{}',
            Next_Action='REVIEW_ALTERNATE_OFFICIAL_AUDITED_ANNUAL_SOURCES',
            **yield_health,
        )
        write_rows(root/RUN_STATUS,[status])
        return status
    own_session = session is None
    session = session or requests.Session()
    if own_session:
        session.headers.update({'User-Agent':'NSE-fundamentals-research/1.0', 'Accept':'application/xml'})
    status = dict(Checked_At_UTC=stamp, Candidate_Symbol_Years=len(candidates),
        Original_Index_Candidates_Before_Preflight=total_index_candidates,
        Excluded_By_Current_Source_Preflight=total_index_candidates-len(candidates),
        Independent_Source_Preflight_Required=require_preflight,
        Attempted=0, Added_Verified=0, Added_Six_Core_Complete=0, Status='COMPLETED_BOUNDED_RUN',
        Remaining_Candidates=len(candidates), Last_Error='', Parser_Version=VERSION,
        **yield_health)
    indexes = {}
    added = []
    context_rejection_reasons = Counter()
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
                if row is None and reason in (
                    'EXACT_APRIL_MARCH_CONTEXT_NOT_VERIFIED',
                    'YEAR_END_BALANCE_SHEET_CONTEXT_NOT_VERIFIED',
                ):
                    # Reuse EXACTLY the downloaded XML bytes; never send a
                    # second NSE request just to diagnose a rejected context.
                    # No source numeric values, issuer identifiers, arbitrary
                    # XML strings, or URLs from XML enter this diagnosis.
                    try:
                        from nse_legacy_context_diagnostic import inspect
                        result=inspect(
                            body,symbol=candidate['Symbol'],
                            fiscal_end=candidate['Fiscal_Year_End'])
                        diagnosis=result['reason']
                        count=result['counters']
                        entry['Context_Diagnostic_Reason']=diagnosis
                        entry['Context_Exact_Annual_Duration_Count']=count[
                            'exact_april_march_duration_contexts']
                        entry['Context_Strict_Annual_Duration_Count']=count[
                            'strict_duration_eligible_contexts']
                        entry['Context_Year_End_Instant_Count']=count[
                            'year_end_instant_contexts']
                        entry['Context_Quarter_Length_Ending_Fiscal_End_Count']=sum(
                            p['contexts'] for p in result[
                                'reported_duration_windows_ending_at_fiscal_end']
                            if p['classification']=='QUARTER_LENGTH_NOT_ANNUAL')
                    except (ValueError,TypeError,KeyError,IndexError):
                        # The original audited-fact rejection is authoritative.
                        diagnosis='CONTEXT_DIAGNOSTIC_UNAVAILABLE'
                        entry['Context_Diagnostic_Reason']=diagnosis
                    context_rejection_reasons[diagnosis]+=1
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
        # "Remaining_Candidates" includes previously attempted but
        # unsuccessful evidence gaps. Distinguish never-attempted count:
        # a rejected original XML must NOT be mistaken for verified.
        status['Remaining_Candidates']=len(candidates)-status['Added_Verified']
        status['Attempted_Unverified_This_Run']=status['Attempted']-status['Added_Verified']
        status['Context_Diagnostic_Reason_Counts_JSON']=json.dumps(
            dict(sorted(context_rejection_reasons.items())),sort_keys=True)
        write_rows(root/RUN_STATUS,[status])
    return status


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit',type=int,default=100)
    parser.add_argument('--require-preflight',action='store_true',
        help='Fail closed unless original source index and last attempt are independently safe')
    parser.add_argument('--plan-only',action='store_true',
        help='No HTTP requests; preview source-safe four-year research priorities')
    parser.add_argument('--respect-yield-guard',action='store_true',
        help='Pause unattended original XML download when recent sources overwhelmingly fail strict annual checks')
    args=parser.parse_args()
    print(collect(limit=args.limit,require_preflight=args.require_preflight,
                  plan_only=args.plan_only,
                  respect_yield_guard=args.respect_yield_guard))


if __name__ == '__main__': main()
