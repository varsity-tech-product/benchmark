#!/usr/bin/env python3
"""Inject backtest infrastructure into a LEAN C# strategy source file.

Applies up to 4 source-code transformations so that a strategy can run
in the benchmark backtest mode:

  1. GetSource() injection — ensures every BaseData subclass has a
     GetSource override pointing to the custom data path.
  2. SetAccountCurrency() — injects the correct quote currency at the
     top of Initialize() (e.g. "USDT" for BTCUSDT pairs).
  3. Fee model — injects an inline _BenchFeeModel class that reads
     maker/taker fee rates from algorithm parameters, and wires it up
     at the end of Initialize().
  4. TradingDaysPerYear — injects Settings.TradingDaysPerYear ??= 365
     so Lean uses 365 trading days for crypto annualization.

NOTE: No brokerage model is injected.  LEAN's default BacktestingBrokerage
is used (AccountType.Cash, effectively 1x leverage, shorting allowed).

Each injection is idempotent: if the target code already exists, it is
left untouched.

Portability notes:
  - A portable strategy should: use AddCrypto (not AddCryptoFuture),
    call SetAccountCurrency() and SetCash() itself, NOT call
    SetLeverage(), and NOT depend on harness-injected GetSource().
  - Class name: benchmark defaults to 'Algorithm'.  Pass --class-name
    to run_backtest.sh to use a different name.

Usage:
  python inject_strategy.py input.cs output.cs --symbol BTCUSDT \\
      --csv-path /data/custom/binance
"""

from __future__ import annotations

import argparse
import re
import sys


# ── Brace-aware class body parser ─────────────────────────────────────

def _find_body_end(source: str, start_pos: int) -> int:
    """Find the closing brace of a block starting from *start_pos*.

    Uses a brace-depth counter and skips string literals, character
    literals, single-line comments (//), and block comments (/* */)
    so that braces inside those constructs are not mis-counted.

    Returns the index of the closing ``}`` or ``-1`` if not found.
    """
    length = len(source)
    i = source.find("{", start_pos)
    if i == -1:
        return -1

    depth = 1
    i += 1

    while i < length and depth > 0:
        ch = source[i]

        # Skip string literals
        if ch == '"':
            i += 1
            while i < length and source[i] != '"':
                if source[i] == '\\':
                    i += 1
                i += 1
            i += 1
            continue

        # Skip character literals
        if ch == "'":
            i += 1
            while i < length and source[i] != "'":
                if source[i] == '\\':
                    i += 1
                i += 1
            i += 1
            continue

        # Skip single-line comments
        if ch == '/' and i + 1 < length and source[i + 1] == '/':
            i = source.find('\n', i)
            if i == -1:
                break
            i += 1
            continue

        # Skip block comments
        if ch == '/' and i + 1 < length and source[i + 1] == '*':
            end = source.find('*/', i + 2)
            if end == -1:
                break
            i = end + 2
            continue

        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return i

        i += 1

    return -1


# ── Shared regex patterns ─────────────────────────────────────────────

_BASEDATA_CLASS_RE = re.compile(
    r"class\s+(\w+)\s*:\s*BaseData\b(?!Collection)"
)
_QCALGO_CLASS_RE = re.compile(
    r"^(\s*(?:public\s+)?class\s+(\w+)\s*:\s*QCAlgorithm\b)", re.MULTILINE
)
_INITIALIZE_RE = re.compile(r"\bvoid\s+Initialize\s*\(")
_QUOTE_CURRENCIES = ("usdt", "usd", "busd", "usdc", "btc", "eth", "bnb")


# ── 0. 12-col BaseData class + AddCrypto→AddData transform ───────────

# The _Kline12Col class that gets injected before the strategy class.
# Reads 12-column zip-sliced data from custom-data-root parameter.
_KLINE12COL_CLASS = r'''
    internal class _Kline12Col : QuantConnect.Data.BaseData
    {
        public static string CsvPath = string.Empty;
        public static string CustomDataRoot = string.Empty;
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
        public override System.DateTime EndTime { get; set; }

        private static string _ResFolder(QuantConnect.Resolution res) =>
            res == QuantConnect.Resolution.Daily  ? "daily"  :
            res == QuantConnect.Resolution.Minute ? "minute" : "hour";

        private static System.TimeSpan _BarSpan(QuantConnect.Resolution res) =>
            res == QuantConnect.Resolution.Daily  ? System.TimeSpan.FromDays(1)    :
            res == QuantConnect.Resolution.Minute ? System.TimeSpan.FromMinutes(1) :
                                                    System.TimeSpan.FromHours(1);

        public override QuantConnect.Data.SubscriptionDataSource GetSource(
            QuantConnect.Data.SubscriptionDataConfig config, System.DateTime date, bool isLiveMode)
        {
            if (!string.IsNullOrWhiteSpace(CsvPath))
                return new QuantConnect.Data.SubscriptionDataSource(CsvPath,
                    QuantConnect.SubscriptionTransportMedium.LocalFile,
                    QuantConnect.Data.FileFormat.Csv);
            var resFolder = _ResFolder(config.Resolution);
            var symbolLower = config.Symbol.Value.ToLowerInvariant();
            var sliceKey = config.Resolution == QuantConnect.Resolution.Daily
                ? date.ToString("yyyyMM", System.Globalization.CultureInfo.InvariantCulture)
                : date.ToString("yyyyMMdd", System.Globalization.CultureInfo.InvariantCulture);
            var zipPath = System.IO.Path.Combine(CustomDataRoot, resFolder, symbolLower,
                $"{sliceKey}_trade.zip");
            var entryName = $"{sliceKey}_{symbolLower}_{resFolder}.csv";
            return new QuantConnect.Data.SubscriptionDataSource($"{zipPath}#{entryName}",
                QuantConnect.SubscriptionTransportMedium.LocalFile,
                QuantConnect.Data.FileFormat.Csv);
        }
        public override QuantConnect.Data.BaseData Reader(
            QuantConnect.Data.SubscriptionDataConfig config, string line,
            System.DateTime date, bool isLiveMode)
        {
            if (string.IsNullOrEmpty(line) || line.StartsWith("\"") || line.StartsWith("open"))
                return null;
            var csv = line.Split(',');
            if (csv.Length < 12) return null;
            try
            {
                var tsMs = long.Parse(csv[0], System.Globalization.CultureInfo.InvariantCulture);
                var time = new System.DateTime(1970, 1, 1, 0, 0, 0, System.DateTimeKind.Utc)
                    .AddMilliseconds(tsMs);
                var bar = new _Kline12Col
                {
                    Symbol = config.Symbol, Time = time, EndTime = time + _BarSpan(config.Resolution),
                    Open = decimal.Parse(csv[1], System.Globalization.CultureInfo.InvariantCulture),
                    High = decimal.Parse(csv[2], System.Globalization.CultureInfo.InvariantCulture),
                    Low = decimal.Parse(csv[3], System.Globalization.CultureInfo.InvariantCulture),
                    Close = decimal.Parse(csv[4], System.Globalization.CultureInfo.InvariantCulture),
                    Volume = decimal.Parse(csv[5], System.Globalization.CultureInfo.InvariantCulture),
                    TakerBuyVolume = decimal.Parse(csv[6], System.Globalization.CultureInfo.InvariantCulture),
                    TakerSellVolume = decimal.Parse(csv[7], System.Globalization.CultureInfo.InvariantCulture),
                    TakerBuyQuoteVolume = decimal.Parse(csv[8], System.Globalization.CultureInfo.InvariantCulture),
                    TakerSellQuoteVolume = decimal.Parse(csv[9], System.Globalization.CultureInfo.InvariantCulture),
                    TakerBuyTrades = decimal.Parse(csv[10], System.Globalization.CultureInfo.InvariantCulture),
                    TakerSellTrades = decimal.Parse(csv[11], System.Globalization.CultureInfo.InvariantCulture),
                };
                bar.Value = bar.Close;
                return bar;
            }
            catch { return null; }
        }
        public override bool RequiresMapping() => false;
        public override bool IsSparseData() => false;
        public override NodaTime.DateTimeZone DataTimeZone() => QuantConnect.TimeZones.Utc;
        public override QuantConnect.Resolution DefaultResolution() => QuantConnect.Resolution.Hour;
        public override System.Collections.Generic.List<QuantConnect.Resolution> SupportedResolutions() =>
            new System.Collections.Generic.List<QuantConnect.Resolution> {
                QuantConnect.Resolution.Minute, QuantConnect.Resolution.Hour, QuantConnect.Resolution.Daily };
    }
'''

# Shadow method bodies injected into the algorithm class.  Uses C# `new`
# to hide QCAlgorithm.AddCrypto / AddCryptoFuture so ALL call patterns
# (string literals, variables, loops) are intercepted at compile time.
_ADD_CRYPTO_SHADOW = r'''
        // [Injected] _bench_AddCrypto: shadow redirects all AddCrypto /
        // AddCryptoFuture calls to the 12-col custom data pipeline.
        // Uses C# method hiding (``new``) so loops, variables, and any
        // call pattern are intercepted — not just string-literal arguments.
        public new QuantConnect.Securities.Security AddCrypto(
            string ticker,
            Resolution? resolution = null,
            string market = null,
            bool fillForward = true,
            decimal leverage = 0m)
        {
            var res = resolution ?? Resolution.Minute;
            var sec = AddData<_Kline12Col>(ticker, res);
            typeof(QuantConnect.Securities.Security)
                .GetProperty("SymbolProperties",
                    System.Reflection.BindingFlags.Public
                    | System.Reflection.BindingFlags.Instance)
                ?.SetValue(sec,
                    new QuantConnect.Securities.SymbolProperties(
                        ticker, "USDT", 1m, 0.01m, 0.00001m, string.Empty));
            return sec;
        }

        public new QuantConnect.Securities.Security AddCryptoFuture(
            string ticker,
            Resolution? resolution = null,
            string market = null,
            bool fillForward = true,
            decimal leverage = 0m)
        {
            return AddCrypto(ticker, resolution, market, fillForward, leverage);
        }
'''


def inject_kline12col_class(source_code: str) -> str:
    """Inject the _Kline12Col BaseData class before the strategy class.

    Idempotent: skips if _Kline12Col already exists in source.
    """
    if "_Kline12Col" in source_code:
        print("  [skip] _Kline12Col already present")
        return source_code

    # Ensure required using directives are present
    required_usings = [
        "using System.Globalization;",
        "using System.IO;",
        "using System.Reflection;",
    ]
    for u in required_usings:
        if u not in source_code:
            # Insert after the last existing 'using' line
            last_using = max(
                (m.end() for m in re.finditer(r"^using\s+[^;]+;", source_code, re.MULTILINE)),
                default=0,
            )
            if last_using > 0:
                source_code = source_code[:last_using] + "\n" + u + source_code[last_using:]

    # Find the strategy class (inherits QCAlgorithm)
    class_match = _QCALGO_CLASS_RE.search(source_code)
    if class_match:
        source_code = (
            source_code[:class_match.start()]
            + _KLINE12COL_CLASS + "\n"
            + source_code[class_match.start():]
        )
        print("  [inject] _Kline12Col BaseData class")
    else:
        print("  [skip] No QCAlgorithm class found for _Kline12Col injection")

    return source_code


def inject_add_crypto_shadow(source_code: str) -> str:
    """Inject AddCrypto / AddCryptoFuture shadow methods into the algorithm class.

    Instead of regex-replacing individual ``AddCrypto("LITERAL", ...)``
    calls, this injects ``new`` methods that hide
    ``QCAlgorithm.AddCrypto`` at the C# level.  Any call — string
    literals, variables, loops — is intercepted and routed through
    ``AddData<_Kline12Col>``.

    Also injects the _Kline12Col static configuration at the top of
    Initialize() so ``CustomDataRoot`` and ``CsvPath`` are set before
    the first ``AddCrypto`` call.

    Idempotent: skips if shadow or legacy ``AddData<_Kline12Col>``
    already present.
    """
    # Structural sentinel: the injected shadow method signature
    if "new QuantConnect.Securities.Security AddCrypto(" in source_code:
        print("  [skip] AddCrypto shadow already present")
        return source_code
    if "AddData<_Kline12Col>" in source_code:
        print("  [skip] Legacy AddData<_Kline12Col> already present")
        return source_code

    # Count existing calls for logging (before we inject the shadow bodies)
    n_crypto = len(re.findall(r"\bAddCrypto\s*\(", source_code))
    n_future = len(re.findall(r"\bAddCryptoFuture\s*\(", source_code))

    # Find the algorithm class
    class_match = _QCALGO_CLASS_RE.search(source_code)
    if not class_match:
        print("  [skip] No QCAlgorithm class found for AddCrypto shadow")
        return source_code

    class_name = class_match.group(2)

    # Inject shadow methods at the end of the class body
    class_body_end = _find_body_end(source_code, class_match.start())
    if class_body_end == -1:
        print("  [skip] Could not find class closing brace")
        return source_code

    source_code = (
        source_code[:class_body_end]
        + _ADD_CRYPTO_SHADOW
        + source_code[class_body_end:]
    )

    # Inject _Kline12Col config at start of Initialize()
    init_m = _INITIALIZE_RE.search(source_code)
    if init_m:
        brace_pos = source_code.find("{", init_m.end())
        if brace_pos != -1:
            config_snippet = (
                '\n            // [Injected] 12-col custom data configuration'
                '\n            _Kline12Col.CsvPath = GetParameter("csv-path", string.Empty).Trim();'
                '\n            _Kline12Col.CustomDataRoot = GetParameter('
                '"custom-data-root", "/data/custom/binance").Trim();'
            )
            source_code = (
                source_code[:brace_pos + 1]
                + config_snippet
                + source_code[brace_pos + 1:]
            )

    print(f"  [inject] AddCrypto/AddCryptoFuture shadow into '{class_name}' "
          f"(intercepts {n_crypto} AddCrypto + {n_future} AddCryptoFuture calls)")
    return source_code


# ── 1. GetSource injection ────────────────────────────────────────────

def inject_get_source(source_code: str, csv_path: str) -> str:
    """Inject GetSource() into BaseData subclasses that lack one.

    The injected method returns a LocalFile SubscriptionDataSource
    pointing to *csv_path*.
    """
    matches = list(_BASEDATA_CLASS_RE.finditer(source_code))
    if not matches:
        return source_code

    get_source_template = (
        "\n"
        "        public override SubscriptionDataSource GetSource("
        "SubscriptionDataConfig config, DateTime date, bool isLiveMode)\n"
        "        {{\n"
        "            return new SubscriptionDataSource(\n"
        '                "{csv_path}",\n'
        "                SubscriptionTransportMedium.LocalFile,\n"
        "                FileFormat.Csv);\n"
        "        }}\n"
    ).format(csv_path=csv_path)

    for m in reversed(matches):
        class_name = m.group(1)
        class_body_end = _find_body_end(source_code, m.start())
        if class_body_end == -1:
            continue

        class_body = source_code[m.start():class_body_end]
        if re.search(r"GetSource\s*\(", class_body):
            continue

        print(f"  [inject] GetSource into BaseData subclass '{class_name}'")
        source_code = (
            source_code[:class_body_end]
            + get_source_template
            + source_code[class_body_end:]
        )

    return source_code


def rewrite_csv_paths(source_code: str, container_csv_path: str) -> str:
    """Replace local CSV file paths in string literals with a container path.

    Only rewrites paths that look like host/development paths.  Paths
    already under /data/ (the container mount) are left untouched so that
    tasks with supplementary data files (e.g. /data/BTC_UTC.csv) keep
    working.
    """
    def _replace(m: re.Match) -> str:
        path = m.group(1)
        if path.startswith("/data/"):
            return path  # already a container path — don't mangle
        return container_csv_path

    pattern = r'(?<=["\'])(/[^"\']*\.csv)(?=["\'])'
    return re.sub(pattern, _replace, source_code)


# ── 2. SetAccountCurrency injection ───────────────────────────────────

def inject_account_currency(source_code: str, symbol: str) -> str:
    """Inject SetAccountCurrency() as the first statement in Initialize().

    The quote currency is derived from the symbol suffix (e.g.
    "btcusdt" -> "USDT").
    """
    sym = symbol.lower()
    quote = None
    for q in _QUOTE_CURRENCIES:
        if sym.endswith(q) and len(sym) > len(q):
            quote = q.upper()
            break
    if quote is None:
        print(f"  [skip] Cannot determine quote currency from '{symbol}'")
        return source_code

    if re.search(r"SetAccountCurrency\s*\(", source_code):
        print("  [skip] SetAccountCurrency already present")
        return source_code

    m = _INITIALIZE_RE.search(source_code)
    if not m:
        print("  [skip] Initialize() not found for SetAccountCurrency")
        return source_code

    brace_pos = source_code.find("{", m.end())
    if brace_pos == -1:
        return source_code

    snippet = f'\n            SetAccountCurrency("{quote}");'
    source_code = (
        source_code[:brace_pos + 1]
        + snippet
        + source_code[brace_pos + 1:]
    )
    print(f'  [inject] SetAccountCurrency("{quote}")')
    return source_code


# ── 3. Fee model injection ────────────────────────────────────────────

_FEE_MODEL_CLASS = """\

    internal class _BenchFeeModel : QuantConnect.Orders.Fees.FeeModel
    {
        private readonly decimal _makerRate;
        private readonly decimal _takerRate;
        public _BenchFeeModel(decimal makerRate, decimal takerRate)
        { _makerRate = makerRate; _takerRate = takerRate; }
        public override QuantConnect.Orders.Fees.OrderFee GetOrderFee(
            QuantConnect.Orders.Fees.OrderFeeParameters p)
        {
            var rate = (p.Order.Type == QuantConnect.Orders.OrderType.Limit
                        && !p.Order.IsMarketable) ? _makerRate : _takerRate;
            var pv = p.Security.Holdings.GetQuantityValue(
                p.Order.AbsoluteQuantity, p.Security.Price);
            return new QuantConnect.Orders.Fees.OrderFee(
                new QuantConnect.Securities.CashAmount(pv.Amount * rate, pv.Cash.Symbol));
        }
    }
"""

_FEE_INIT_SNIPPET = """
            // [Injected by backtest harness]
            {
                var _mr = 0.0002m; var _tr = 0.0005m;
                decimal.TryParse(GetParameter("maker-fee-rate"),
                    System.Globalization.NumberStyles.Any,
                    System.Globalization.CultureInfo.InvariantCulture, out _mr);
                decimal.TryParse(GetParameter("taker-fee-rate"),
                    System.Globalization.NumberStyles.Any,
                    System.Globalization.CultureInfo.InvariantCulture, out _tr);
                if (_mr > 0 || _tr > 0)
                    foreach (var _s in Securities.Values)
                        _s.FeeModel = new _BenchFeeModel(_mr, _tr);
            }
"""


def inject_fee_model(source_code: str) -> str:
    """Inject an inline fee model class and initialization code.

    Two parts:
      1. _BenchFeeModel class — before the strategy class declaration.
      2. Fee-setup block — at the end of Initialize() body.
    """
    # Step 1: inject fee-setup snippet at end of Initialize()
    m = _INITIALIZE_RE.search(source_code)
    if not m:
        print("  [skip] Initialize() not found for fee model")
        return source_code

    init_body_end = _find_body_end(source_code, m.start())
    if init_body_end == -1:
        print("  [skip] Could not find Initialize() closing brace")
        return source_code

    source_code = (
        source_code[:init_body_end]
        + _FEE_INIT_SNIPPET
        + source_code[init_body_end:]
    )

    # Step 2: inject _BenchFeeModel class before the strategy class
    class_match = _QCALGO_CLASS_RE.search(source_code)
    if class_match:
        source_code = (
            source_code[:class_match.start()]
            + _FEE_MODEL_CLASS + "\n"
            + source_code[class_match.start():]
        )
    else:
        # Fallback: find the class declaration before Initialize()
        m2 = _INITIALIZE_RE.search(source_code)
        if m2:
            class_line = source_code.rfind("class ", 0, m2.start())
            if class_line != -1:
                source_code = (
                    source_code[:class_line]
                    + _FEE_MODEL_CLASS + "\n"
                    + source_code[class_line:]
                )

    print("  [inject] _BenchFeeModel + fee parameter wiring")
    return source_code


# ── 4. TradingDaysPerYear injection ───────────────────────────────────

_TRADING_DAYS_SNIPPET = """
            // [Injected] Crypto markets trade 365 days per year
            Settings.TradingDaysPerYear ??= 365;
"""


def inject_trading_days_per_year(source_code: str) -> str:
    """Inject TradingDaysPerYear = 365 at the end of Initialize().

    Uses ??= so an explicit user setting takes precedence.
    """
    if re.search(r"TradingDaysPerYear\s*=", source_code):
        print("  [skip] TradingDaysPerYear already set")
        return source_code

    m = _INITIALIZE_RE.search(source_code)
    if not m:
        print("  [skip] Initialize() not found for TradingDaysPerYear")
        return source_code

    init_body_end = _find_body_end(source_code, m.start())
    if init_body_end == -1:
        print("  [skip] Could not find Initialize() closing brace")
        return source_code

    source_code = (
        source_code[:init_body_end]
        + _TRADING_DAYS_SNIPPET
        + source_code[init_body_end:]
    )
    print("  [inject] TradingDaysPerYear ??= 365")
    return source_code


# ── Main entry point ──────────────────────────────────────────────────

def inject_all(
    source_code: str,
    *,
    symbol: str,
    csv_path: str = "",
    data_mode: str = "custom",
) -> str:
    """Apply all applicable injections to the strategy source code.

    Args:
        source_code: Raw C# source code.
        symbol: Trading symbol (e.g. "BTCUSDT") for currency detection.
        csv_path: Container path to custom data.
        data_mode: Must be "custom".

    Returns:
        Patched source code.
    """
    if data_mode != "custom":
        raise ValueError(
            "Legacy standard mode has been removed. Use 12-col custom mode."
        )

    # Step 0: Inject _Kline12Col class and AddCrypto/AddCryptoFuture shadows
    source_code = inject_kline12col_class(source_code)
    source_code = inject_add_crypto_shadow(source_code)

    if csv_path:
        # Legacy: inject GetSource into user-defined BaseData subclasses
        source_code = inject_get_source(source_code, csv_path)
        source_code = rewrite_csv_paths(source_code, csv_path)

    source_code = inject_account_currency(source_code, symbol)
    source_code = inject_fee_model(source_code)
    source_code = inject_trading_days_per_year(source_code)

    return source_code


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inject backtest infrastructure into a LEAN C# strategy file."
    )
    parser.add_argument("input", help="Input .cs file")
    parser.add_argument("output", help="Output .cs file (can be same as input)")
    parser.add_argument("--symbol", required=True, help="Trading symbol, e.g. BTCUSDT")
    parser.add_argument("--csv-path", default="", help="Container path to custom data")
    parser.add_argument(
        "--data-mode", choices=["custom"], default="custom",
        help="Data mode: custom (12-col benchmark runtime only)"
    )
    args = parser.parse_args()

    try:
        source = open(args.input, encoding="utf-8").read()
    except FileNotFoundError:
        print(f"Error: file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    print(f"Injecting into: {args.input}")
    print(f"  symbol={args.symbol}  data_mode={args.data_mode}")

    result = inject_all(
        source,
        symbol=args.symbol,
        csv_path=args.csv_path,
        data_mode=args.data_mode,
    )

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(result)

    print(f"Output written to: {args.output}")


if __name__ == "__main__":
    main()
