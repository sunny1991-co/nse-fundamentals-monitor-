"""Indicative NSE equity *delivery* charges, not a broker contract note.

Source: https://groww.in/pricing and https://groww.in/help/stocks%2C-f%26o%2C-ipo-%26-mtf/discoverable/what-are-the-different-charges-mentioned-in-the-report

All monetary amounts are INR. Rates checked 2026-10-16; future tariffs,
rounding, promotional balance types, depository demographics and capital-gains
tax can differ. Source prices and per-fill NSE VWAP for STT are approximated
by the executable fill; this is a conservative research scenario, not an invoice.

This module does not place orders and has no network calls.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

STT_DELIVERY = 0.001       # 0.1% buy AND sell
STAMP_BUY = 0.00015        # 0.015% on buy only
NSE_EXCHANGE = 0.0000297   # 0.00297% each side
SEBI_FEE = 0.000001        # 0.0001% each side
IPFT = 0.000001            # 0.0001% each side
GST = 0.18
DP_GROWW_SELL = 16.5
DP_CDSL_CONSERVATIVE = 3.5
DP_MIN_DEBIT_VALUE = 100.0
LAST_REVIEWED = "2026-10-16"


@dataclass(frozen=True)
class Breakdown:
    side: str
    turnover: float
    brokerage: float
    stt: float
    stamp: float
    exchange: float
    sebi: float
    ipft: float
    dp: float
    gst: float
    total: float


def groww_delivery_breakdown(turnover: float, side: str, *,
                             stress_multiplier: float = 1.0) -> Breakdown:
    """Compute research-estimated delivery order costs including DP/GST.

    Sell DP assumed per sell execution (conservative for split exits).
    Fees are never invented as negative rebates; multiples >1 are fee stress.
    STT, stamp and market levies are also stressed by scenario multiplier,
    not asserted to change statutory tariffs.
    """
    if side not in {"buy", "sell"}:
        raise ValueError("INVALID_SIDE")
    if (not math.isfinite(turnover) or turnover <= 0
            or not math.isfinite(stress_multiplier)
            or stress_multiplier < 1):
        raise ValueError("INVALID_TURNOVER_OR_STRESS")
    brokerage = max(min(20.0, turnover * 0.001),
                    min(5.0, turnover * 0.025))
    stt = turnover * STT_DELIVERY
    stamp = turnover * STAMP_BUY if side == "buy" else 0.0
    exchange = turnover * NSE_EXCHANGE
    sebi = turnover * SEBI_FEE
    ipft = turnover * IPFT
    dp = (DP_GROWW_SELL + DP_CDSL_CONSERVATIVE
          if side == "sell" and turnover >= DP_MIN_DEBIT_VALUE else 0.0)
    taxable = brokerage + exchange + sebi + ipft + dp
    gst = taxable * GST
    # Scenario stress multiplies the full fees, NOT the fill price.
    fees = (brokerage, stt, stamp, exchange, sebi, ipft, dp, gst)
    return Breakdown(
        side, turnover, *(v * stress_multiplier for v in fees),
        sum(fees) * stress_multiplier
    )


def simulate_delivery(d, start: int, end: int, *, capital: float = 100_000.0,
                      slip: float = 0.001, stress_multiplier: float = 1.0,
                      max_hold: int = 100, liquidate_at_end: bool = True):
    """Single-stock whole-share strategy execution with per-order fees.

    Same prior-bar signal/next-open 1% band and same conservative stop-first
    partial targets as swing_backtest.simulate; separate implementation avoids
    changing previous research reports. Returns per-position NET realisation.
    Both slippage and costs are embedded in the returned net trade returns.
    """
    if not (capital > 0 and math.isfinite(capital)
            and 0 <= slip < .1 and math.isfinite(slip)
            and 1 <= stress_multiplier <= 4 and 1 <= max_hold <= 500):
        raise ValueError("INVALID_SIMULATION_PARAMETERS")
    if not (0 <= start <= end <= len(d)):
        raise ValueError("INVALID_DATE_RANGE")
    cash = float(capital)
    position = None
    trades = []
    curve = [cash]
    total_fees = 0.0

    def sale(p, qty, price):
        nonlocal cash, total_fees
        if qty <= 0:
            return
        turnover = qty * price
        fee = groww_delivery_breakdown(turnover, "sell",
                                       stress_multiplier=stress_multiplier).total
        proceeds = turnover - fee
        cash += proceeds
        p["sell_receipts"] += proceeds
        p["sell_turnover"] += turnover
        p["sell_fees"] += fee
        p["qty"] -= qty
        total_fees += fee

    for i in range(start, end):
        b = d.iloc[i]
        if position is None and i >= 220 and bool(d.iloc[i-1].Signal):
            sig = d.iloc[i-1]
            fill = float(b.Open) * (1 + slip)
            if sig.Close <= fill <= sig.Close*1.01 and (
                0 < fill-float(sig.Stop) <= .12*fill
            ):
                qty = int(cash // fill)
                fee = None
                while qty > 0:
                    turnover = qty*fill
                    fee = groww_delivery_breakdown(
                        turnover, "buy",
                        stress_multiplier=stress_multiplier
                    ).total
                    if turnover+fee <= cash + 1e-9:
                        break
                    qty -= 1
                if qty > 0:
                    before = cash
                    buy_total = qty*fill + fee
                    cash -= buy_total
                    total_fees += fee
                    risk = fill-float(sig.Stop)
                    position = {
                        "qty": qty, "original_qty": qty, "entry": fill,
                        "stop": float(sig.Stop), "t1": fill+2*risk,
                        "t2": fill+3*risk, "i": i,
                        "entry_date": str(b.Date.date()), "before": before,
                        "buy_total": buy_total, "buy_turnover": qty*fill,
                        "buy_fees": fee, "sell_receipts": 0.0,
                        "initial_stop": float(sig.Stop),
                        "signal_date": str(sig.Date.date()),
                        "sell_turnover": 0.0, "sell_fees": 0.0,
                        "partial": False,
                    }
        if position is not None:
            p = position
            reason = None
            exit_price = None
            # Exit forensics are observations only. Never affect fills, fees,
            # stop-first priority or historical profit calculations.
            stop_at_bar_start = float(p["stop"])
            had_partial_at_bar_start = bool(p["partial"])
            entry_session = (i == p["i"])
            if b.Low <= p["stop"]:
                exit_price = min(float(b.Open), p["stop"])*(1-slip)
                reason = "stop"
            else:
                if not p["partial"] and p["original_qty"] > 1 and b.High >= p["t1"]:
                    take = p["original_qty"]//2
                    sale(p, take, p["t1"]*(1-slip))
                    p["partial"] = True
                if b.High >= p["t2"]:
                    exit_price = p["t2"]*(1-slip)
                    reason = "target2"
                elif i-p["i"]+1 >= max_hold:
                    exit_price = float(b.Close)*(1-slip)
                    reason = "time"
                elif i == end-1 and liquidate_at_end:
                    exit_price = float(b.Close)*(1-slip)
                    reason = "sample_end"
            if reason is not None:
                sale(p, p["qty"], exit_price)
                pnl = p["sell_receipts"]-p["buy_total"]
                trades.append({
                    "Entry_Date": p["entry_date"],
                    "Exit_Date": str(b.Date.date()),
                    "Reason": reason,
                    "Entry": p["entry"], "Exit": exit_price,
                    "Quantity": p["original_qty"],
                    "Buy_Turnover_Rs": p["buy_turnover"],
                    "Sell_Turnover_Rs": p["sell_turnover"],
                    "All_Trading_Charges_Rs": p["buy_fees"]+p["sell_fees"],
                    "Net_Profit_Rs": pnl,
                    "Return": pnl/p["buy_total"],
                    "Held_Sessions": i-p["i"]+1,
                    # Extra fields for independent *loss attribution*.
                    # These are never consumed to choose the exit.
                    "Signal_Date": p["signal_date"],
                    "Initial_Stop_Rs": p["initial_stop"],
                    "Stop_At_Exit_Rs": stop_at_bar_start,
                    "Exit_Open_Rs": float(b.Open),
                    "Exit_Low_Rs": float(b.Low),
                    "Exit_High_Rs": float(b.High),
                    "Exit_On_Entry_Session": entry_session,
                    "Partial_Profit_Taken": bool(p["partial"]),
                    "Partial_Previously_Taken": had_partial_at_bar_start,
                    "Gap_Below_Stop": bool(
                        reason == "stop" and float(b.Open) < stop_at_bar_start
                    ),
                    "Stop_And_Target_Both_Touched": bool(
                        reason == "stop" and float(b.High) >= p["t1"]
                    ),
                    "Trailing_Stop_Active": bool(
                        reason == "stop"
                        and stop_at_bar_start > p["initial_stop"] + 1e-9
                    ),
                    "Gross_Filled_Return": (
                        p["sell_turnover"]/p["buy_turnover"]-1
                    ),
                })
                position = None
            elif p["partial"]:
                p["stop"] = max(
                    p["stop"], p["entry"], float(b.Close-2*b.ATR)
                )
        mark = cash
        if position is not None:
            # Conservative liquidation estimate, to avoid overstating the curve.
            est = max(0, position["qty"]*float(b.Close)*(1-slip))
            fee = (groww_delivery_breakdown(
                est, "sell", stress_multiplier=stress_multiplier
            ).total if est > 0 else 0.0)
            mark += est-fee
        curve.append(mark)

    completed = [float(t["Return"]) for t in trades
                 if t["Reason"] != "sample_end"]
    losses = -sum(x for x in completed if x < 0)
    peak = curve[0]
    drawdown = 0.0
    for value in curve[1:]:
        peak = max(peak, value)
        if peak > 0:
            drawdown = max(drawdown, 1-value/peak)
    return {
        "Trades": len(trades),
        "Completed_Trades": len(completed),
        "Completed_Mean_Net_Return": (
            sum(completed)/len(completed) if completed else None
        ),
        "Completed_Profit_Factor": (
            sum(x for x in completed if x > 0)/losses
            if losses > 0 else None
        ),
        "Max_Drawdown": drawdown,
        "Estimated_All_Trading_Charges_Rs": total_fees,
        "Final_Marked_Capital_Rs": curve[-1],
        "Open_Position": position is not None,
    }, trades
