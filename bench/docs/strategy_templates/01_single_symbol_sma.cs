// Template 01: Single-Symbol SMA Crossover
//
// The simplest possible strategy on the 12-col backtest system.
// Buys BTCUSDT when fast SMA crosses above slow SMA, sells on cross below.
//
// Key patterns demonstrated:
//   - AddCrypto with a single symbol
//   - LEAN built-in indicators (SimpleMovingAverage)
//   - SetWarmUp to pre-fill indicators before trading
//   - IsWarmingUp guard to skip early bars
//   - data[symbol] for accessing bar data (NOT data.Bars)
//   - SetHoldings / Liquidate for position management

using QuantConnect;
using QuantConnect.Algorithm;
using QuantConnect.Data;
using QuantConnect.Indicators;

namespace QuantConnect.Algorithm.CSharp
{
    public class Algorithm : QCAlgorithm
    {
        private Symbol _btc;
        private SimpleMovingAverage _smaFast;
        private SimpleMovingAverage _smaSlow;

        public override void Initialize()
        {
            SetStartDate(2022, 1, 1);
            SetEndDate(2025, 12, 31);
            SetCash(100000);

            // Subscribe to daily data.
            // The harness intercepts this and routes to 12-col data.
            _btc = AddCrypto("BTCUSDT", Resolution.Daily).Symbol;

            // LEAN's built-in indicators work normally.
            _smaFast = SMA(_btc, 10, Resolution.Daily);
            _smaSlow = SMA(_btc, 30, Resolution.Daily);

            // Warm up so indicators are ready on day 1.
            SetWarmUp(30, Resolution.Daily);
        }

        public override void OnData(Slice data)
        {
            // Always guard against warm-up period.
            if (IsWarmingUp) return;

            // Check data arrived for our symbol.
            // Use data.ContainsKey or data.Keys -- NOT data.Bars.
            if (!data.ContainsKey(_btc)) return;

            // Indicators are fed automatically by LEAN.
            if (!_smaFast.IsReady || !_smaSlow.IsReady) return;

            if (_smaFast > _smaSlow && !Portfolio[_btc].IsLong)
            {
                SetHoldings(_btc, 1.0m);
                Log($"BUY  @ {Securities[_btc].Price}  SMA10={_smaFast:F2}  SMA30={_smaSlow:F2}");
            }
            else if (_smaFast < _smaSlow && Portfolio[_btc].IsLong)
            {
                Liquidate(_btc);
                Log($"SELL @ {Securities[_btc].Price}  SMA10={_smaFast:F2}  SMA30={_smaSlow:F2}");
            }
        }
    }
}
