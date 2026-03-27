/*
 * I09 — Risk Management Models
 *
 * Strategy:
 *   - Universe: full tier2 crypto-futures universe from universe.json
 *   - Timeframe: Hourly bars
 *   - Signal: EMA(24) / EMA(72) crossover via AlphaModel
 *   - Portfolio: EqualWeightingPortfolioConstructionModel
 *   - Execution: ImmediateExecutionModel
 *   - Risk: Parameterized via GetParameter("risk_config"):
 *       "none"    → no risk management
 *       "builtin" → MaximumDrawdownPerSecurity(5%) + TrailingStop(3%)
 *       "custom"  → MaxGroupExposureRiskManagementModel(40%) + TrailingStop(3%)
 *
 * LEAN API: Algorithm Framework (AlphaModel, RiskManagementModel,
 *           AddRiskManagement, SetRiskManagement, GetParameter)
 */

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json;
using QuantConnect;
using QuantConnect.Algorithm;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Algorithm.Framework.Execution;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Algorithm.Framework.Risk;
using QuantConnect.Data;
using QuantConnect.Data.Market;
using QuantConnect.Indicators;
using QuantConnect.Data.UniverseSelection;
using QuantConnect.Orders;

namespace QuantTutorBench
{
    public class I09RiskManagement : QCAlgorithm
    {
        // ── Configuration ──
        private const int MaxSymbols = 20;
        private const decimal InitialCash = 100_000m;

        public override void Initialize()
        {
            SetStartDate(2022, 1, 1);
            SetEndDate(2025, 12, 31);
            SetAccountCurrency("USDT");
            SetCash(InitialCash);

            var tickers = LoadUniverse();
            var subset = tickers.Take(MaxSymbols).ToList();

            foreach (var ticker in subset)
            {
                AddCryptoFuture(ticker, Resolution.Hour, Market.Binance);
            }

            // Framework wiring
            SetAlpha(new HourlyEmaCrossoverAlpha());
            SetPortfolioConstruction(new EqualWeightingPortfolioConstructionModel());
            SetExecution(new ImmediateExecutionModel());

            // Risk management based on parameter
            var riskConfig = GetParameter("risk_config", "none");
            if (riskConfig == "builtin")
            {
                AddRiskManagement(new MaximumDrawdownPercentPerSecurity(0.05m));
                AddRiskManagement(new TrailingStopRiskManagementModel(0.03m));
                Log("Risk config: builtin (MaxDD 5% + TrailingStop 3%)");
            }
            else if (riskConfig == "custom")
            {
                AddRiskManagement(new MaxGroupExposureRiskManagementModel(0.40m));
                AddRiskManagement(new TrailingStopRiskManagementModel(0.03m));
                Log("Risk config: custom (MaxGroupExposure 40% + TrailingStop 3%)");
            }
            else
            {
                Log("Risk config: none (no risk management)");
            }

            SetWarmUp(72, Resolution.Hour);

            Log($"I09 initialized with {subset.Count} symbols, " +
                $"EMA(24/72) hourly, risk_config={riskConfig}");
        }

        public override void OnOrderEvent(OrderEvent orderEvent)
        {
            if (orderEvent.Status == OrderStatus.Filled)
            {
                Log($"TRADE: {orderEvent.Symbol} | " +
                    $"Direction={orderEvent.Direction} | " +
                    $"Qty={orderEvent.FillQuantity} | " +
                    $"Price={orderEvent.FillPrice} | " +
                    $"Tag={orderEvent.Message}");
            }
        }

        private List<string> LoadUniverse()
        {
            var paths = new[]
            {
                Path.Combine(Globals.DataFolder, "universe.json"),
                "/data/universe.json",
                "/lean/Data/universe.json"
            };

            foreach (var path in paths)
            {
                if (File.Exists(path))
                {
                    var json = File.ReadAllText(path);
                    var symbols = JsonConvert.DeserializeObject<List<string>>(json);
                    Log($"Loaded {symbols.Count} symbols from {path}");
                    return symbols;
                }
            }

            Log("WARNING: universe.json not found, using default 10-symbol universe");
            return new List<string>
            {
                "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
                "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "MATICUSDT"
            };
        }
    }

    /// <summary>
    /// EMA(24) / EMA(72) crossover alpha on hourly bars.
    /// </summary>
    public class HourlyEmaCrossoverAlpha : AlphaModel
    {
        private const int FastPeriod = 24;
        private const int SlowPeriod = 72;

        private class SymbolData
        {
            public Symbol Symbol;
            public ExponentialMovingAverage EmaFast;
            public ExponentialMovingAverage EmaSlow;
            public InsightDirection LastDirection = InsightDirection.Flat;
        }

        private readonly Dictionary<Symbol, SymbolData> _data = new();

        public override IEnumerable<Insight> Update(QCAlgorithm algorithm, Slice data)
        {
            var insights = new List<Insight>();

            foreach (var kvp in _data)
            {
                var sd = kvp.Value;

                if (!sd.EmaFast.IsReady || !sd.EmaSlow.IsReady)
                    continue;

                if (!data.ContainsKey(sd.Symbol))
                    continue;

                var fast = sd.EmaFast.Current.Value;
                var slow = sd.EmaSlow.Current.Value;
                var spread = (fast - slow) / slow;

                InsightDirection direction;
                if (fast > slow)
                    direction = InsightDirection.Up;
                else
                    direction = InsightDirection.Down;

                if (direction != InsightDirection.Flat)
                {
                    var magnitude = (double)Math.Abs(spread);
                    var confidence = direction == sd.LastDirection ? 0.6 : 0.8;

                    insights.Add(Insight.Price(sd.Symbol, TimeSpan.FromDays(2),
                        direction, magnitude, confidence));
                }

                sd.LastDirection = direction;
            }

            return insights;
        }

        public override void OnSecuritiesChanged(QCAlgorithm algorithm, SecurityChanges changes)
        {
            foreach (var added in changes.AddedSecurities)
            {
                if (!_data.ContainsKey(added.Symbol))
                {
                    var sd = new SymbolData
                    {
                        Symbol = added.Symbol,
                        EmaFast = algorithm.EMA(added.Symbol, FastPeriod, Resolution.Hour),
                        EmaSlow = algorithm.EMA(added.Symbol, SlowPeriod, Resolution.Hour),
                    };
                    _data[added.Symbol] = sd;
                }
            }

            foreach (var removed in changes.RemovedSecurities)
            {
                _data.Remove(removed.Symbol);
            }
        }
    }

    /// <summary>
    /// Custom risk model: limits total portfolio exposure per group.
    /// If total exposure exceeds the threshold, scales all positions down.
    /// </summary>
    public class MaxGroupExposureRiskManagementModel : RiskManagementModel
    {
        private readonly decimal _maxExposure;

        public MaxGroupExposureRiskManagementModel(decimal maxExposure)
        {
            _maxExposure = maxExposure;
        }

        public override IEnumerable<IPortfolioTarget> ManageRisk(
            QCAlgorithm algorithm, IPortfolioTarget[] targets)
        {
            var totalPortfolioValue = algorithm.Portfolio.TotalPortfolioValue;
            if (totalPortfolioValue <= 0) return targets;

            var totalExposure = algorithm.Portfolio.TotalHoldingsValue / totalPortfolioValue;

            if (totalExposure > _maxExposure)
            {
                var scale = _maxExposure / totalExposure;
                algorithm.Log($"RISK: Group exposure {totalExposure:P2} exceeds {_maxExposure:P2}, " +
                              $"scaling positions by {scale:F3}");

                var adjusted = new List<IPortfolioTarget>();
                foreach (var target in targets)
                {
                    adjusted.Add(new PortfolioTarget(target.Symbol,
                        (int)(target.Quantity * scale)));
                }
                return adjusted;
            }

            return targets;
        }
    }
}
