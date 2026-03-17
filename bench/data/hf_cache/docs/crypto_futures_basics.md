# Crypto Futures: A Practical Guide

Crypto futures research uses market structure that differs from ordinary cash equities. This guide covers the concepts most relevant to perpetual futures and Binance-style kline data.

---

## 1. What Are Perpetual Futures?

Perpetual futures are derivative contracts with no expiry date.

Key properties:

- They track an underlying asset such as BTC or ETH
- Traders can take long or short exposure
- Leverage is commonly available
- A funding-rate mechanism helps keep the contract near spot

Because there is no expiry, pricing behavior differs from traditional dated futures.

---

## 2. USDT-M vs Coin-M Contracts

Two common contract types:

- **USDT-M**: margined and settled in USDT, with a linear payoff structure
- **Coin-M**: margined and settled in the base asset, often with inverse mechanics

For research and teaching, USDT-M is usually simpler because:

- PnL is easier to interpret in quote currency
- Position sizing is more intuitive
- Data sources are widely available

---

## 3. Funding Rates

Funding is a periodic cash flow between longs and shorts.

- Positive funding: longs pay shorts
- Negative funding: shorts pay longs
- Exchanges often settle funding every 8 hours

Approximate funding cash flow:

$$
\mathrm{funding\ payment} = \mathrm{position\ notional} \times \mathrm{funding\ rate}
$$

Funding matters in two ways:

- **As a cost** for holding positions
- **As a signal** that reflects crowding or carry

Extremely positive funding can indicate crowded long positioning. Extremely negative funding can indicate crowded short positioning.

---

## 4. Fee Structure

Perpetual futures usually distinguish between:

- **Maker fees**: for passive liquidity provision
- **Taker fees**: for aggressive liquidity removal

Approximate formula:

$$
\mathrm{fee} = \mathrm{notional\ traded} \times \mathrm{fee\ rate}
$$

In research:

- A low-turnover signal is less fee-sensitive
- A microstructure signal can disappear once taker fees and slippage are considered

---

## 5. Kline Data Fields

Binance-style kline files usually contain:

- `open`, `high`, `low`, `close`
- `volume`
- `quote_volume`
- `trade_count`
- `taker_buy_vol`
- `taker_buy_quote_vol`

Field intuition:

- **volume**: traded base asset amount
- **quote_volume**: traded quote-currency amount
- **trade_count**: number of trades in the interval
- **taker_buy_vol**: base volume executed by aggressive buyers
- **taker_buy_quote_vol**: quote volume executed by aggressive buyers

These non-price fields are often useful for flow and microstructure research.

---

## 6. Slippage and Market Impact

Slippage is the difference between the observed price and the actual fill price.

Common causes:

- Thin order books
- Volatile conditions
- Large order size relative to available liquidity
- Aggressive order types

Simple modeling approaches:

- Fixed basis-point slippage
- Spread-based slippage
- Volume-aware impact models

For early alpha research, slippage may be ignored or approximated. For realistic validation, it must be modeled explicitly.

---

## 7. Python: Loading Binance Kline CSVs

Typical workflow:

```python
import pandas as pd

df = pd.read_csv("BTCUSDT_1d_2021_2024.csv")
df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
df = df.sort_values("timestamp").reset_index(drop=True)
```

Checks to perform:

- Timestamps are monotonic
- Numeric columns are parsed as numbers
- Timezone handling is explicit
- No missing intervals exist unexpectedly

---

## 8. Common Pitfalls

- Treating funding as irrelevant for longer holding periods
- Confusing base volume with quote volume
- Ignoring that taker-buy fields describe aggressive flow, not total buying
- Mixing spot and futures data without checking basis effects
- Forgetting that 24/7 markets do not have equity-style session boundaries

Crypto futures research is easiest when market structure assumptions are written down explicitly rather than left implicit.
