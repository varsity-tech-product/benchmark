using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Reflection;
using QuantConnect;
using QuantConnect.Algorithm;
using QuantConnect.Data;
using QuantConnect.Data.Market;
using QuantConnect.Indicators;
using QuantConnect.Orders;
using QuantConnect.Securities;
using NodaTime;

namespace QuantConnect.Algorithm.CSharp
{
    /// <summary>
    /// 12-column custom BaseData bar for MatchX/benchmark parity testing.
    /// Reads: open_time_ms, O, H, L, C, vol, tbv, tsv, tbqv, tsqv, tbt, tst
    /// </summary>
    public class Parity12ColBar : BaseData
    {
        public static string CsvPath = string.Empty;
        public static string CustomDataRoot = string.Empty;
        public static string ResolutionFolder = "hour";
        public static TimeSpan BarSpan = TimeSpan.FromHours(1);

        public decimal Open { get; set; }
        public decimal High { get; set; }
        public decimal Low { get; set; }
        public decimal Close { get; set; }
        public decimal Volume { get; set; }
        public decimal TakerBuyVolume { get; set; }
        public decimal TakerSellVolume { get; set; }
        public decimal TakerBuyQuoteVolume { get; set; }
        public decimal TakerSellQuoteVolume { get; set; }
        public decimal TakerBuyTrades { get; set; }
        public decimal TakerSellTrades { get; set; }

        public override DateTime EndTime { get; set; }

        public override SubscriptionDataSource GetSource(
            SubscriptionDataConfig config, DateTime date, bool isLiveMode)
        {
            // Path 1: flat CSV file (MatchX MV mode injects csv-path parameter)
            if (!string.IsNullOrWhiteSpace(CsvPath))
            {
                return new SubscriptionDataSource(
                    CsvPath,
                    SubscriptionTransportMedium.LocalFile,
                    FileFormat.Csv);
            }

            // Path 2: zip-sliced files (benchmark custom data)
            var symbolLower = config.Symbol.Value.ToLowerInvariant();
            var sliceKey = config.Resolution == Resolution.Daily
                ? date.ToString("yyyyMM", CultureInfo.InvariantCulture)
                : date.ToString("yyyyMMdd", CultureInfo.InvariantCulture);
            var zipPath = Path.Combine(
                CustomDataRoot, ResolutionFolder, symbolLower,
                $"{sliceKey}_trade.zip");
            var entryName = $"{sliceKey}_{symbolLower}_{ResolutionFolder}.csv";
            return new SubscriptionDataSource(
                $"{zipPath}#{entryName}",
                SubscriptionTransportMedium.LocalFile,
                FileFormat.Csv);
        }

        public override BaseData Reader(
            SubscriptionDataConfig config, string line, DateTime date, bool isLiveMode)
        {
            if (string.IsNullOrEmpty(line) || line.StartsWith("\"") || line.StartsWith("open"))
                return null;

            var csv = line.Split(',');
            if (csv.Length < 12)
                return null;

            try
            {
                var tsMs = long.Parse(csv[0], CultureInfo.InvariantCulture);
                var time = new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc)
                    .AddMilliseconds(tsMs);

                var bar = new Parity12ColBar
                {
                    Symbol = config.Symbol,
                    Time = time,
                    EndTime = time + BarSpan,
                    Open = decimal.Parse(csv[1], CultureInfo.InvariantCulture),
                    High = decimal.Parse(csv[2], CultureInfo.InvariantCulture),
                    Low = decimal.Parse(csv[3], CultureInfo.InvariantCulture),
                    Close = decimal.Parse(csv[4], CultureInfo.InvariantCulture),
                    Volume = decimal.Parse(csv[5], CultureInfo.InvariantCulture),
                    TakerBuyVolume = decimal.Parse(csv[6], CultureInfo.InvariantCulture),
                    TakerSellVolume = decimal.Parse(csv[7], CultureInfo.InvariantCulture),
                    TakerBuyQuoteVolume = decimal.Parse(csv[8], CultureInfo.InvariantCulture),
                    TakerSellQuoteVolume = decimal.Parse(csv[9], CultureInfo.InvariantCulture),
                    TakerBuyTrades = decimal.Parse(csv[10], CultureInfo.InvariantCulture),
                    TakerSellTrades = decimal.Parse(csv[11], CultureInfo.InvariantCulture),
                };
                bar.Value = bar.Close;
                return bar;
            }
            catch
            {
                return null;
            }
        }

        public override bool RequiresMapping() => false;
        public override bool IsSparseData() => false;
        public override DateTimeZone DataTimeZone() => TimeZones.Utc;
        public override Resolution DefaultResolution() => Resolution.Hour;
        public override List<Resolution> SupportedResolutions() =>
            new List<Resolution> { Resolution.Minute, Resolution.Hour, Resolution.Daily };
    }

    /// <summary>
    /// Portable EMA crossover strategy for benchmark/MatchX parity validation.
    ///
    /// Requirements for portability:
    ///   - Explicit SetAccountCurrency + SetCash (not harness-injected)
    ///   - Own GetSource (not harness-injected)
    ///   - No SetLeverage
    ///   - AddData (not AddCrypto/AddCryptoFuture)
    /// </summary>
    public class ParityValidationStrategy : QCAlgorithm
    {
        private Symbol _symbol;
        private ExponentialMovingAverage _fastEma;
        private ExponentialMovingAverage _slowEma;
        private bool? _prevCross;

        private const int FastPeriod = 10;
        private const int SlowPeriod = 30;
        private const decimal PositionSize = 0.01m;

        public override void Initialize()
        {
            SetAccountCurrency("USDT");
            SetCash(100000);

            SetStartDate(2024, 1, 1);
            SetEndDate(2024, 1, 7);

            // Read data path from algorithm parameters.
            // MatchX MV mode sets csv-path; benchmark sets custom-data-root.
            Parity12ColBar.CsvPath = GetParameter("csv-path", string.Empty).Trim();
            Parity12ColBar.CustomDataRoot = GetParameter(
                "custom-data-root", "/data/custom/binance").Trim();
            Parity12ColBar.ResolutionFolder = "hour";
            Parity12ColBar.BarSpan = TimeSpan.FromHours(1);

            _symbol = AddData<Parity12ColBar>("BTCUSDT", Resolution.Hour).Symbol;

            // Set SymbolProperties via reflection (same as MatchX strategies)
            typeof(Security)
                .GetProperty("SymbolProperties", BindingFlags.Public | BindingFlags.Instance)
                ?.SetValue(
                    Securities[_symbol],
                    new SymbolProperties("BTCUSDT", "USDT", 1m, 0.01m, 0.00001m, string.Empty));

            _fastEma = new ExponentialMovingAverage(FastPeriod);
            _slowEma = new ExponentialMovingAverage(SlowPeriod);

            SetWarmUp(SlowPeriod);
            Settings.TradingDaysPerYear = 365;
        }

        public override void OnData(Slice data)
        {
            if (!data.ContainsKey(_symbol)) return;

            var bar = data.Get<Parity12ColBar>()[_symbol];
            var price = bar.Close;
            if (price <= 0) return;

            _fastEma.Update(bar.Time, price);
            _slowEma.Update(bar.Time, price);

            if (IsWarmingUp || !_fastEma.IsReady || !_slowEma.IsReady) return;

            bool cross = _fastEma.Current.Value > _slowEma.Current.Value;

            if (_prevCross.HasValue && cross != _prevCross.Value)
            {
                var holdings = Portfolio[_symbol];
                if (cross) // bullish
                {
                    if (holdings.IsShort)
                        MarketOrder(_symbol, Math.Abs(holdings.Quantity));
                    if (!holdings.IsLong)
                        MarketOrder(_symbol, PositionSize);
                }
                else // bearish
                {
                    if (holdings.IsLong)
                        MarketOrder(_symbol, -holdings.Quantity);
                    if (!holdings.IsShort)
                        MarketOrder(_symbol, -PositionSize);
                }
            }

            _prevCross = cross;
        }

        public override void OnOrderEvent(OrderEvent orderEvent)
        {
            if (orderEvent.Status == OrderStatus.Filled)
            {
                Log($"FILL: {orderEvent.Direction} {Math.Abs(orderEvent.FillQuantity)} " +
                    $"@ {orderEvent.FillPrice} | Holdings: {Portfolio[_symbol].Quantity}");
            }
        }
    }
}
