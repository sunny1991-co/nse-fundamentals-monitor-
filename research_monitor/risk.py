"""Reject incomplete plans; sizes are research estimates, never broker orders."""
import math
from datetime import datetime, timezone

def finite(value):
    if isinstance(value,bool):
        raise ValueError('Boolean is not a number')
    n=float(value)
    if not math.isfinite(n):
        raise ValueError('Non-finite number')
    return n

def quote_errors(quote, cfg, now=None):
    errors=[]
    now=now or datetime.now(timezone.utc)
    try:
        stamp=datetime.fromisoformat(quote['feed_time'])
        if stamp.tzinfo is None:
            raise ValueError('Timezone missing')
        age=(now-stamp).total_seconds()
        if age<0 or age>finite(cfg['max_quote_age_seconds']):
            errors.append('STALE_OR_FUTURE_QUOTE')
        bid,ask,ltp=(finite(quote[k]) for k in ['bid','ask','ltp'])
        if not 0<bid<=ask or ltp<=0:
            errors.append('INVALID_BID_ASK')
        elif (ask-bid)/((ask+bid)/2)>finite(cfg['max_spread_fraction']):
            errors.append('SPREAD_TOO_WIDE')
    except (KeyError,TypeError,ValueError):
        errors.append('QUOTE_INCOMPLETE')
    return errors

def size_plan(plan, quote, cfg, portfolio, now=None):
    errors=quote_errors(quote,cfg,now)
    if cfg.get('kill_switch') is not False:
        errors.append('KILL_SWITCH_OR_MISSING_CONFIGURATION')
    if portfolio.get('session_open_verified') is not True:
        errors.append('EXCHANGE_SESSION_NOT_VERIFIED')
    if plan.get('symbol')!=quote.get('symbol') or str(plan.get('token'))!=str(quote.get('token')) or plan.get('exchange')!=quote.get('exchange'):
        errors.append('INSTRUMENT_MISMATCH')
    kind=plan.get('kind')
    if kind not in {'equity','long_option'}:
        errors.append('INSTRUMENT_NOT_IMPLEMENTED')
    # Stock backtests must never be reused as evidence for an option contract.
    required=['holdout_pass','paper_validation_pass','strategy_version_match','entry_rules_match','exit_rules_match']
    if kind=='equity':
        required+=['fundamentals_pass','market_regime_pass','relative_strength_pass','event_risk_clear','portfolio_concentration_pass']
    else:
        required+=['actual_option_contract_backtest','lot_and_expiry_verified','greeks_and_iv_verified']
        try:
            expiry=datetime.fromisoformat(plan['expiry'])
            if expiry.tzinfo is None or expiry<=(now or datetime.now(timezone.utc)):
                errors.append('EXPIRED_OR_INVALID_CONTRACT')
        except (KeyError,TypeError,ValueError):
            errors.append('EXPIRY_MISSING')
    errors += ['MISSING_EVIDENCE_'+k.upper() for k in required if plan.get('evidence',{}).get(k) is not True]
    quantity=0
    try:
        capital=finite(cfg['capital_inr'])
        entry=finite(quote['ask'])*(1+finite(cfg['slippage_fraction_per_side']))
        stop=finite(plan['stop_loss']); low=finite(plan['entry_min']); high=finite(plan['entry_max'])
        lot=finite(plan['lot_size'])
        if capital<=0 or not 0<stop<entry or not low<=entry<=high or lot<1 or not lot.is_integer():
            raise ValueError('Invalid price/capital/lot')
        if kind=='equity' and lot!=1:
            raise ValueError('Equity lot must be one')
        risk_fraction=finite(cfg['risk_per_trade_fraction'])
        open_fraction=finite(cfg['max_total_open_risk_fraction'])
        position_fraction=finite(cfg['max_position_fraction'])
        daily_fraction=finite(cfg['daily_loss_limit_fraction'])
        if not (0<risk_fraction<=.01 and risk_fraction<=open_fraction<=.05 and 0<position_fraction<=.25 and 0<daily_fraction<=.05):
            raise ValueError('Risk configuration outside supported conservative bounds')
        used_risk=finite(portfolio['open_risk_inr']); free_cash=finite(portfolio['free_cash_inr'])
        pnl=finite(portfolio['daily_marked_pnl_inr']); cost=finite(cfg['cost_fraction_per_side'])
        if used_risk<0 or free_cash<0 or not 0<=cost<.05:
            raise ValueError('Invalid account/cost state')
        if pnl<=-capital*daily_fraction:
            errors.append('DAILY_LOSS_LIMIT')
        budget=min(capital*risk_fraction,capital*open_fraction-used_risk)
        # Long options can lose the ENTIRE premium; use that, not the stop,
        # to size their risk. Stocks can gap too; reported stock risk is planned.
        unit_risk=entry*(1+cost) if kind=='long_option' else entry-stop+cost*(entry+stop)
        lots=math.floor(min(max(0,budget)/unit_risk,min(free_cash,capital*position_fraction)/(entry*(1+cost)))/lot)
        quantity=max(0,lots)*int(lot)
        if quantity==0:
            errors.append('INSUFFICIENT_RISK_BUDGET_OR_CAPITAL')
    except (KeyError,ValueError,TypeError,OverflowError):
        errors.append('CAPITAL_PRICES_OR_ACCOUNT_STATE_INCOMPLETE')
    if errors:
        return dict(status='BLOCKED',quantity=0,reasons=sorted(set(errors)))
    r=entry-stop
    return dict(status='PAPER_PLAN_ONLY',symbol=plan['symbol'],quantity=quantity,
        entry=round(entry,2),stop_loss=stop,target_1=round(entry+2*r,2),
        target_2=round(entry+3*r,2),planned_risk_inr=round(quantity*unit_risk,2),
        maximum_loss_note='Entire premium at risk' if kind=='long_option' else 'Gap losses can exceed planned risk',
        real_order_status='NOT_SENT_MANUAL_BROKER_CONFIRMATION_REQUIRED')
