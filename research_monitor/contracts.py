"""Validate verified instrument-master records; never hard-code lots/expiries."""
from datetime import datetime,timezone
from .risk import finite

def validate_contract(row,now=None):
    now=now or datetime.now(timezone.utc)
    errors=[]
    kinds={'OPTIDX':('INDEX','OPTION'),'FUTIDX':('INDEX','FUTURE'),
           'OPTSTK':('STOCK','OPTION'),'FUTSTK':('STOCK','FUTURE')}
    kind=row.get('instrument_type')
    if kind not in kinds:
        errors.append('UNSUPPORTED_INSTRUMENT')
    if row.get('exchange')!='NFO' or not row.get('token') or not row.get('underlying'):
        errors.append('VERIFIED_NFO_IDENTITY_REQUIRED')
    if row.get('official_master_verified') is not True:
        errors.append('OFFICIAL_INSTRUMENT_MASTER_NOT_VERIFIED')
    try:
        checked=datetime.fromisoformat(row['master_checked_at'])
        expiry=datetime.fromisoformat(row['expiry'])
        if checked.tzinfo is None or expiry.tzinfo is None:
            raise ValueError()
        if not 0<=(now-checked).total_seconds()<=86400:
            errors.append('INSTRUMENT_MASTER_STALE')
        if expiry<=now:
            errors.append('EXPIRED_CONTRACT')
        lot=finite(row['lot_size']);tick=finite(row['tick_size'])
        if lot<1 or not lot.is_integer() or tick<=0:
            raise ValueError()
        if kind in {'OPTIDX','OPTSTK'}:
            if row.get('option_type') not in {'CE','PE'} or finite(row['strike'])<=0:
                raise ValueError()
    except (KeyError,ValueError,TypeError):
        errors.append('CONTRACT_TERMS_INCOMPLETE')
    if kind in {'OPTSTK','FUTSTK'} and (row.get('settlement_reviewed') is not True or row.get('fno_ban_checked') is not True):
        errors.append('STOCK_SETTLEMENT_OR_BAN_REVIEW_PENDING')
    if kind in {'FUTIDX','FUTSTK'} and row.get('margin_stress_reviewed') is not True:
        errors.append('FUTURES_MARGIN_STRESS_REVIEW_PENDING')
    return dict(status='BLOCKED' if errors else 'RESEARCH_ELIGIBLE_NOT_TRADE_APPROVAL',
        underlying=row.get('underlying'),scope=kinds.get(kind,('UNKNOWN','UNKNOWN')),
        reasons=errors,execution='PAPER_ONLY_NOT_CONNECTED')
