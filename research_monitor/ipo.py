"""RHP review queue only. IPOs have no pre-listing technical backtest."""
from datetime import date
from urllib.parse import urlparse
from .risk import finite

OFFICIAL_HOSTS={'www.nseindia.com','nsearchives.nseindia.com','www.sebi.gov.in','www.bseindia.com'}

def review_ipo(row, today=None):
    today=today or date.today()
    blockers=[]
    if row.get('board')!='MAINBOARD':
        blockers.append('NOT_VERIFIED_MAINBOARD')
    try:
        url=urlparse(row['rhp_url'])
        if url.scheme!='https' or url.hostname not in OFFICIAL_HOSTS:
            raise ValueError()
    except (KeyError,ValueError,TypeError):
        blockers.append('OFFICIAL_RHP_SOURCE_MISSING')
    for field in ['audited_financials_verified','use_of_proceeds_reviewed','promoter_and_litigation_reviewed','related_party_reviewed','peer_valuation_reviewed','financial_years_consecutive','latest_offer_terms_verified']:
        if row.get(field) is not True:
            blockers.append('REVIEW_PENDING_'+field.upper())
    try:
        opened=date.fromisoformat(row['open_date']);closed=date.fromisoformat(row['close_date'])
        if closed<opened or today>closed:
            blockers.append('ISSUE_CLOSED_OR_INVALID_DATES')
        if (today-date.fromisoformat(row['reviewed_on'])).days not in (0,1):
            blockers.append('REVIEW_STALE')
        band=finite(row['price_band_high']);lot=finite(row['lot_size'])
        if band<=0 or lot<1 or not lot.is_integer():
            raise ValueError()
        minimum=band*lot
        years=row['annual_financials']
        if len(years)!=3:
            raise ValueError()
        revenues=[finite(y['revenue']) for y in years]
        profits=[finite(y['net_profit']) for y in years]
        cash=[finite(y['operating_cashflow']) for y in years]
        if min(revenues+profits+cash)<=0:
            blockers.append('THREE_YEAR_POSITIVE_REVENUE_PROFIT_CASHFLOW_REQUIRED')
        if revenues[-1]<=revenues[0] or profits[-1]<=profits[0]:
            blockers.append('THREE_YEAR_GROWTH_NOT_PRESENT')
        if sum(cash)<.8*sum(profits):
            blockers.append('CASH_CONVERSION_WEAK')
        debt=finite(row['debt_equity']); pe=finite(row['post_issue_pe']); peer=finite(row['peer_median_pe'])
        if debt<0 or debt>.5 or pe<=0 or peer<=0 or pe>peer:
            blockers.append('LEVERAGE_OR_PEER_VALUATION_REVIEW_FAILED')
        if row.get('sector_specialist') is not False:
            blockers.append('SPECIALIST_SECTOR_REQUIRES_SEPARATE_METRICS')
    except (KeyError,TypeError,ValueError):
        minimum=None
        blockers.append('OFFER_OR_FINANCIAL_DATA_INCOMPLETE')
    return dict(company=row.get('company',''),status='BLOCKED' if blockers else 'RESEARCH_REVIEW_COMPLETE_NOT_BUY_SIGNAL',
        minimum_application_inr=minimum,reasons=sorted(set(blockers)),
        backtest='NOT_POSSIBLE_BEFORE_LISTING',entry_stoploss='NOT_APPLICABLE_TO_IPO_APPLICATION',
        post_listing_rule='No swing approval until required history and technical validation exist',
        gmp_used=False,application_status='NOT_SUBMITTED')
