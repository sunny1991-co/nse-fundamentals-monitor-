"""Explain the fixed breakout-v1 thresholds without relaxing them."""

def evidence_failures(dev, hold, stress, first, second):
    checks = [
        ('DEVELOPMENT_COMPLETED_TRADES_LT_20', dev['Completed_Trades'] >= 20),
        ('DEVELOPMENT_MEAN_RETURN_NOT_POSITIVE', dev['Mean_Return'] > 0),
        ('HOLDOUT_COMPLETED_TRADES_LT_30', hold['Completed_Trades'] >= 30),
        ('HOLDOUT_MEAN_RETURN_NOT_POSITIVE', hold['Mean_Return'] > 0),
        ('HOLDOUT_PROFIT_FACTOR_MISSING_OR_LT_1_5', hold['Profit_Factor'] is not None and hold['Profit_Factor'] >= 1.5),
        ('HOLDOUT_DRAWDOWN_GT_20_PERCENT', hold['Max_Drawdown'] <= .20),
        ('STRESS_MEAN_RETURN_NOT_POSITIVE', stress['Mean_Return'] > 0),
        ('FIRST_HALF_COMPLETED_TRADES_LT_5', first['Completed_Trades'] >= 5),
        ('SECOND_HALF_COMPLETED_TRADES_LT_5', second['Completed_Trades'] >= 5),
        ('FIRST_HALF_MEAN_RETURN_NOT_POSITIVE', first['Mean_Return'] > 0),
        ('SECOND_HALF_MEAN_RETURN_NOT_POSITIVE', second['Mean_Return'] > 0),
    ]
    return [name for name, passed in checks if not passed]
