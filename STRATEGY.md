# Team155-alpha SMU Strategy

## Macro Thesis

The current competition environment is range-bound rather than trend-driven. BTC is oscillating in the 70,000-73,000 USD zone with a bearish macro bias after a hawkish Fed tone, ETH remains weak near the low 2,000s, BNB is the relative strength leader, and SOL is the weakest major in the basket. Fear has remained elevated, which favors short-horizon rebounds and mean-reversion bounces over persistent trend continuation.

Given that backdrop, the bot is intentionally configured to overweight mean reversion and underweight momentum. The objective is not to maximize gross exposure, but to harvest repeated small reversions with tight downside control and limit-order fee efficiency. This is aligned with hackathon judging criteria that emphasize Sharpe Ratio and Sortino Ratio more than raw return.

## Signal Architecture

The bot combines two signals and uses a weighted vote.

- `MomentumStrategy` weight: `0.25`
- `MeanReversionStrategy` weight: `0.75`

This weighting reflects the view that range trading is the dominant opportunity set during the March 21-31, 2026 competition window.

### Momentum Strategy

Momentum is now deliberately suppressed unless trend strength is real.

- Fast EMA: `12`
- Slow EMA: `26`
- Trend strength check: compare `EMA9` vs `EMA21`
- If `abs(EMA9 - EMA21) / EMA21 < 0.003`, momentum returns `HOLD`
- RSI period: `14`
- RSI overbought: `65`
- RSI oversold: `35`

Momentum only contributes when trend structure is strong enough to justify it. This prevents the trend leg from repeatedly fighting the range-reversion thesis.

### Mean Reversion Strategy

Mean reversion is the primary edge capture engine.

- Bollinger period: `20`
- Bollinger std dev: `2.0`
- Z-score entry: `1.5`
- Z-score exit: `0.3`

The strategy also requires location inside the recent range:

- BUY only if price is in the lower 30 percent of the last 20-bar range
- SELL only if price is in the upper 30 percent of the last 20-bar range

This prevents fading mid-range noise. The bot only acts when the market is both statistically stretched and physically close to a range extreme.

## Entry And Exit Rules

### Entry

The final signal is a weighted vote:

- Momentum: `0.25`
- Mean reversion: `0.75`

Default thresholds:

- Base signal threshold: `0.25`
- Relaxed threshold: `0.15`

The bot prefers limit orders for entries to minimize fees:

- BUY limit price: slightly below market
- SELL limit price: slightly above market

If a limit order does not fill quickly enough, the bot can fall back to a market order, but the preferred mode is maker-style execution.

### Exit

Range trades use tight profit-taking and fast failure recognition.

- Stop loss: `0.8%`
- Take profit: `0.6%`

On every BUY fill, the bot records:

- `entry_price`
- `stop_price = entry_price * 0.992`
- `take_profit_price = entry_price * 1.006`

During each tick:

- Take profit is checked first
- Stop loss is checked second
- Triggered exits execute immediately at market

This is designed for short-horizon bounce capture, not long trend holding.

## Position Sizing And Pair Tilt

The bot sizes from live USD balance rather than a hardcoded portfolio value.

Base sizing:

- `MAX_POSITION_PCT = 0.04`

Pair-specific weights:

- `BNB/USD = 1.5`
- `BTC/USD = 1.0`
- `ETH/USD = 0.8`
- `SOL/USD = 0.5`

Rationale:

- BNB is the strongest pair and gets the largest allocation
- BTC is neutral and remains the benchmark range asset
- ETH is weaker and sized down modestly
- SOL is the weakest and receives the smallest allocation

The pair weight multiplies the calculated quantity after standard position sizing.

## Risk Controls

The current range-mode risk profile is intentionally conservative.

- Cooldown: `90 seconds`
- Max open positions: `2`
- Dust positions are ignored for exposure counting
- Per-pair active cap: `1`
- BTC position scale: `0.5`
- Pair precision, amount precision, and `MiniOrder` rules are pulled from Roostoo `exchangeInfo`

The practical result is low deployed capital, frequent re-entry capacity, and minimal capital lockup. With a 50,000 USD mock portfolio this keeps most cash uncommitted while still allowing repeated range trades.

## Expected Edge

Target trade geometry:

- Take profit: `+0.6%`
- Stop loss: `-0.8%`

If the strategy can maintain a 60 percent win rate in a stable range regime:

- Expected value per trade
- `= 0.60 * 0.6% - 0.40 * 0.8%`
- `= 0.36% - 0.32%`
- `= +0.04% per trade`

At roughly 12 trades per day:

- Daily expected return: about `+0.48%`
- 8-day expected return: about `+3.84%`

This is below the stretch target of `+5%`, but it is much more aligned with strong Sharpe and Sortino than an aggressive directional strategy.

## Commission Impact

Roostoo fee structure:

- Market orders: `0.10%`
- Limit orders: `0.05%`

Because the strategy targets relatively small reversions, fee control matters materially.

- A full market-in and market-out round trip costs about `0.20%`
- A full limit-in and limit-out round trip costs about `0.10%`

On a `0.6%` take-profit target, saving `0.10%` in round-trip fees preserves a large portion of the edge. That is why normal entries favor limit orders and only urgent exits use market orders.

## Current Operating Mode

The repository is currently configured for a range-bound macro regime with:

- Mean reversion as the dominant signal
- Momentum filtered to only activate in real trend conditions
- Tight stop and take-profit bands
- BNB overweight and SOL underweight
- Low simultaneous exposure

This configuration is intended to produce cleaner, higher-quality trades for the March 28 repository review and the live competition window that follows.
