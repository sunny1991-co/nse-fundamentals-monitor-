"""Additional research gates. Inputs must be independently timestamp-verified.
These gates do not estimate future probability and are not yet backtest-validated.
"""
from datetime import datetime,timezone
import math

def market_strength(stock_closes,index_closes,*,aligned_sessions_verified=False):
    # Require the exact same completed dates; caller must verify alignment.
    if not aligned_sessions_verified or len(stock_closes)!=len(index_closes) or len(index_closes)<200:
        return {'status':'BLOCKED','reason':'ALIGNED_200_SESSION_HISTORY_REQUIRED'}
    try:
        stock=[float(x) for x in stock_closes];index=[float(x) for x in index_closes]
        if any(not math.isfinite(x) or x<=0 for x in stock+index):
            raise ValueError()
        relative=stock[-1]/stock[-64]-index[-1]/index[-64]
        regime=index[-1]>sum(index[-200:])/200 and sum(index[-50:])/50>sum(index[-200:])/200
        return {'status':'RESEARCH_CHECK_ONLY','market_regime_pass':regime,
                'relative_strength_pass':relative>0,'relative_63_session_return':relative,
                'full_strategy_backtest_required':True}
    except (ValueError,TypeError):
        return {'status':'BLOCKED','reason':'INVALID_PRICES'}

def event_and_concentration(*,checked_at,event_calendar_verified,next_event_at,
                            capital,sector_exposure_after,position_exposure_after,
                            now=None):
    now=now or datetime.now(timezone.utc)
    blockers=[]
    try:
        check=datetime.fromisoformat(checked_at)
        if check.tzinfo is None or not 0<=(now-check).total_seconds()<=86400 or event_calendar_verified is not True:
            blockers.append('EVENT_CALENDAR_UNVERIFIED_OR_STALE')
        if next_event_at:
            event=datetime.fromisoformat(next_event_at)
            if event.tzinfo is None or event<now:
                blockers.append('EVENT_TIMESTAMP_INVALID')
            elif (event-now).total_seconds()<=3*86400:
                blockers.append('MATERIAL_EVENT_WITHIN_72_HOURS')
        values=[float(v) for v in [capital,sector_exposure_after,position_exposure_after]]
        if not all(math.isfinite(v) for v in values) or values[0]<=0 or min(values[1:])<0:
            raise ValueError()
        if values[1]/values[0]>.25 or values[2]/values[0]>.10:
            blockers.append('SECTOR_OR_POSITION_CONCENTRATION_LIMIT')
    except (ValueError,TypeError):
        blockers.append('EVENT_OR_EXPOSURE_DATA_INCOMPLETE')
    return {'status':'BLOCKED' if blockers else 'RESEARCH_CHECK_ONLY','reasons':blockers,
        'full_strategy_backtest_required':True}
