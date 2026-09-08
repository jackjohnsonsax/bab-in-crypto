"""Betting Against Beta in crypto: the tradeable implementation.

Two things only:

  1. BASELINE -- Frazzini & Pedersen's construction, followed as closely as the
     asset class allows. No tuning.
  2. ROBUSTNESS -- how the baseline moves when the estimation windows and the
     portfolio construction change.

Every portfolio must hold at least MIN_NAMES per leg. Without that guard, any
restriction on the universe "improves" the Sharpe simply by concentrating the
book into a handful of coins, which is narrowing rather than signal.

Illiquidity is priced rather than screened out. Execution costs scale with the
size of a trade relative to the coin's daily volume, so the model needs a book
size: AUM below. Reported results are for $5m.

Metrics follow Johnny Tung's writeup (Sharpe, Return, Volatility, Max DD,
Holding Period, Turnover, Transaction Costs), annualised on 365 rather than 252
because crypto trades every day.
"""
import os
import numpy as np, pandas as pd, statsmodels.api as sm, warnings
warnings.filterwarnings('ignore')

DATA = 'data'
ANN = 365
# Execution costs scale with participation rather than being flat. Each trade
# pays a fixed component (commission plus half-spread) plus market impact under
# the square-root law: impact grows with the coin's volatility and with the
# square root of the trade as a share of that coin's daily volume. This replaces
# the hard liquidity screen. Thin coins become expensive rather than excluded,
# which also removes the turnover a cutoff manufactures as coins cross it.
AUM = 5e6               # book size the cost model is calibrated to
FIXED_BPS = 7           # commission plus half-spread
IMPACT_K = 1.0          # square-root impact coefficient
EXIT_BPS = 100          # forced exit of a delisting contract
MIN_NAMES = 8           # per leg; below this the portfolio is not diversified
START = '2021-01-01'    # perpetuals reach usable breadth here

# --- estimation conventions --------------------------------------------------
# FP use a 5-year correlation and 1-year volatility window. In crypto that
# removes most of the universe (a 1825-day window needs ~2.5 years of history,
# which fewer than half of coins have), so the whole project uses a 1-year
# correlation and 6-month volatility window instead. FP's windows are reported
# as a robustness row, not as the baseline.
CORR_WINDOW = 365       # 1 year, on 3-day overlapping log returns
VOL_WINDOW = 180        # 6 months, on daily log returns
AGG_DAYS = 3            # 3-day aggregation fixes non-synchronous trading
SHRINK = 0.6            # Vasicek weight toward the cross-sectional mean of 1

# --- tradeability ------------------------------------------------------------
SCREEN = 0              # no hard liquidity screen; costs price liquidity instead
VOL_WEIGHT_SQRT = True  # size the 'volume' scheme by sqrt(ADV) rather than ADV.
                        # Raw ADV is so skewed (BTC ~$2bn against ~$1m in the
                        # tail) that it collapses to an effective six names.


# ============================================================== data ==========
def load():
    px = {k: pd.read_pickle(f'{DATA}/{k}.pkl') for k in
          ['ret', 'volume', 'ret_perp', 'volume_perp', 'funding']}
    retSpot, volSpot = px['ret'], px['volume']

    # market index: 30-day ADV weights, lagged so it is investable
    adv = volSpot.reindex_like(retSpot).rolling(30, min_periods=15).mean()
    adv = adv.where(retSpot.notna())
    w = adv.div(adv.sum(axis=1), axis=0)
    mkt = (w.shift() * retSpot).sum(axis=1, min_count=1).dropna()
    return retSpot, volSpot, px['ret_perp'], px['volume_perp'], px['funding'], mkt


RET_S_FULL, VOL_S, RET_P, VOL_P, FUND, MKT_FULL = load()

# Every spot coin, NOT the spot-perp intersection. Intersecting would admit a
# coin to the 2021 panel on the strength of Binance listing a perpetual for it in
# 2025, which is end-of-sample information: coins that eventually get a perpetual
# launch with roughly three times the volume of those that never do. The short
# leg is filtered to live perpetuals at portfolio construction instead, where the
# test is point-in-time. Perp panels reindex to NaN for coins that have none.
SYMS = sorted(RET_S_FULL.columns)
IDX = RET_P.loc[START:RET_P.index.max()].index

# Beta is estimated on the FULL price history and only then restricted to the
# trading window. Reindexing first would leave a 365-day rolling window with no
# pre-2021 data, so no coin would have a beta until mid-2021.
RET_S_FULL = RET_S_FULL[SYMS]
RET_S, VOL_S, RET_P, VOL_P, FUND = [
    d.reindex(index=IDX, columns=SYMS) for d in
    (RET_S_FULL, VOL_S, RET_P, VOL_P, FUND)]
MKT = MKT_FULL.reindex(IDX)
LIQ_S = VOL_S.rolling(30, min_periods=15).median().shift()
LIQ_P = VOL_P.rolling(30, min_periods=15).median().shift()
ADV_P = VOL_P.rolling(30, min_periods=15).mean().shift()        # short leg trades here
ADV_S = VOL_S.rolling(30, min_periods=15).mean().shift()        # long leg trades here
ADV = ADV_P                                                     # compatibility alias
COIN_VOL = np.log(1 + RET_S).rolling(60, min_periods=30).std().shift()
def rebal_mask(freq='M'):
    """True on days the book is rebuilt. 'D' daily, 'W' weekly, '2W', 'M' monthly.

    Beta is a slow signal, so FP rebalance monthly. Whether that is right here is
    an empirical question: crypto betas decay faster than equity betas, and the
    funding credit on the short leg means turnover is cheaper than usual.
    """
    if freq == 'D':
        first = np.ones(len(IDX), dtype=bool)
    elif freq == 'W':
        first = ~IDX.to_period('W').duplicated()
    elif freq == '2W':
        w = ~IDX.to_period('W').duplicated()
        first = w & (np.cumsum(w) % 2 == 1)
    elif freq == 'M':
        first = ~IDX.to_period('M').duplicated()
    else:
        raise ValueError(freq)
    return pd.DataFrame(np.broadcast_to(first[:, None], (len(IDX), len(SYMS))),
                        index=IDX, columns=SYMS)


REBAL = 'M'
MONTH_START = rebal_mask(REBAL)


# ============================================================== beta ==========
def estimate_beta(corr_window=CORR_WINDOW, vol_window=VOL_WINDOW, agg=AGG_DAYS,
                  min_corr=None, min_vol=None, with_se=False):
    """FP eq. (14): beta = rho * sigma_i / sigma_m, on separate windows.

    Estimated on the full price history, then restricted to the trading window,
    so the rolling windows see everything available before each date.

    `min_corr` / `min_vol` fix the minimum observations a coin needs to get a
    beta. They default to half the window, which is the usual convention but
    confounds the window comparison: a longer window then also demands a longer
    price history, so it silently drops younger coins and any improvement mixes
    better estimation with selection on age. Passing a constant holds the
    universe fixed so the window is the only thing that varies.
    """
    mv = vol_window // 2 if min_vol is None else min_vol
    mc = corr_window // 2 if min_corr is None else min_corr
    r = np.log(1 + RET_S_FULL).join(np.log(1 + MKT_FULL).rename('mkt'), how='left')
    vol = r.rolling(vol_window, min_periods=mv).std()
    ra = r.rolling(agg).sum() if agg > 1 else r
    corr = ra.rolling(corr_window, min_periods=mc).corr(ra['mkt'])
    beta = (corr * vol).divide(vol['mkt'], axis=0).drop(columns=['mkt'])
    beta = beta.reindex(index=IDX, columns=SYMS)
    if not with_se:
        return beta
    # SE of an OLS slope: sigma_eps / (sigma_m sqrt(n)), with
    # sigma_eps = sigma_i sqrt(1 - rho^2). sigma_m is common across coins, so
    # only the sigma_i and rho terms move the cross-section.
    n = (ra.rolling(corr_window, min_periods=mc).count() / max(agg, 1))
    se = (vol.drop(columns=['mkt']).divide(vol['mkt'], axis=0)
          * np.sqrt(((1 - corr ** 2) / (n - 2)).clip(lower=1e-12)
                    ).drop(columns=['mkt']))
    return beta, se.reindex(index=IDX, columns=SYMS)


def shrink_beta(beta, w=SHRINK):
    """Vasicek shrinkage toward the cross-sectional mean of 1 (FP eq. 15)."""
    return w * beta + (1 - w)


# ========================================================= portfolio ==========
def bab_weights(beta, long_ok, short_ok, scheme='rank', scale_beta=None,
                rebal=None, rank_ok=None, size_by=None):
    """Long low signal, short high signal, each leg scaled to beta 1 (eq. 16-17).

    long_ok / short_ok are boolean masks: a coin can be held long if it is
    tradeable in spot, and short only if it has a live perpetual.

    `rank_ok` is the universe the cross-sectional sort runs over, defaulting to
    each leg's own mask. The two are separated because the legs draw on different
    universes here: a long position is held in spot and needs no derivative,
    while a short needs a perpetual. Ranking both legs against one distribution
    and then letting each hold only what it can implement keeps the sort coherent
    without forcing the long leg to give up the coins that have no perpetual.

    `beta` is the SORTING signal. `scale_beta` is what the legs are scaled by,
    and defaults to the sorting signal. They differ when sorting on something
    other than beta (e.g. volatility) while still wanting a beta-neutral book.
    """
    scale = beta if scale_beta is None else scale_beta
    when = MONTH_START if rebal is None else rebal_mask(rebal)

    def leg(mask, top):
        rank_uni = mask if rank_ok is None else rank_ok
        b = beta.where(rank_uni)
        sb = scale.where(rank_uni)
        if scheme == 'rank':                       # FP eq. (16)
            rank = b.rank(axis=1)
            dev = rank.subtract(rank.mean(axis=1), axis=0)
            k = 2 / dev.abs().sum(axis=1).replace(0, np.nan)
            w = (dev if top else -dev).clip(lower=0).multiply(k, axis=0)
        elif scheme == 'precision':
            # Size by how sharply each beta is identified rather than by how
            # extreme it is. `size_by` is 1 / SE(beta); non-members get 0.
            q = b.rank(axis=1, pct=True)
            member = (q > 0.5) if top else (q <= 0.5)
            w = size_by.where(member, 0.0)
        elif scheme == 'volume':
            # Same membership as the rank scheme -- each leg takes its half of
            # the beta distribution -- but sized by liquidity rather than by how
            # extreme the beta is. This is what a capacity-constrained investor
            # would do, and it is the opposite tilt to rank weighting, which puts
            # most weight on the extreme betas that tend to be the thinnest
            # coins. Each leg is sized on the venue it actually trades.
            q = b.rank(axis=1, pct=True)
            member = (q > 0.5) if top else (q <= 0.5)
            # non-members must be 0, not NaN: weights are formed monthly and
            # forward-filled, so a NaN would carry last month's weight forward
            # for a coin that has since left the leg.
            adv = (ADV_P if top else ADV_S)
            w = (np.sqrt(adv) if VOL_WEIGHT_SQRT else adv).where(member, 0.0)
        else:                                      # quintile
            q = b.rank(axis=1, pct=True)
            w = (q > 0.8) if top else (q <= 0.2)
            w = w.astype(float).divide(w.sum(axis=1).replace(0, np.nan), axis=0)
        # a leg holds only what it can implement, then is renormalised to be
        # fully invested. A no-op when mask is the ranking universe.
        w = w.where(mask)
        w = w.divide(w.sum(axis=1).replace(0, np.nan), axis=0)
        w = w.where(when).ffill().where(b.notna() & mask)
        bh = sb.where(when).ffill().where(b.notna() & mask)
        leg_beta = (w * bh).sum(axis=1, min_count=1)
        return w, leg_beta.where(leg_beta.abs() > 0.2)

    wL, betaL = leg(long_ok, top=False)
    wH, betaH = leg(short_ok, top=True)

    # diversification guard: drop days where either leg is too concentrated
    enough = ((wL > 0).sum(axis=1) >= MIN_NAMES) & ((wH > 0).sum(axis=1) >= MIN_NAMES)
    return wL.div(betaL, axis=0).where(enough), wH.div(betaH, axis=0).where(enough)


def trade_costs(wL, wH, aum=AUM):
    """Cost of a day's trading as a fraction of the book (square-root impact law).

    The long leg trades in spot and the short leg in perpetuals, so each is
    charged against the volume of the venue it actually trades on.

        rate_i = FIXED_BPS + IMPACT_K * sigma_i * sqrt(participation_i)

    where participation is the dollar trade in coin i over that coin's average
    daily volume. A small trade in a liquid coin pays close to the fixed
    component; a large trade in a thin one pays a multiple of it.
    """
    tradedL = (wL - wL.shift()).abs().fillna(0)
    tradedH = (wH - wH.shift()).abs().fillna(0)
    cost, part = 0, []
    for t, adv in [(tradedL, ADV_S), (tradedH, ADV_P)]:
        p = ((t * aum) / adv).replace([np.inf, -np.inf], np.nan)
        rate = FIXED_BPS * 1e-4 + IMPACT_K * COIN_VOL * np.sqrt(p.clip(lower=0))
        cost = cost + (t * rate).sum(axis=1)
        part.append(p.where(t > 0))
    return cost, (tradedL + tradedH).sum(axis=1), part[0].fillna(part[1])


def backtest(wL, wH, aum=AUM):
    """Long leg held in spot, short leg in perpetuals (which pay funding)."""
    gross = ((wL.shift() * RET_S).sum(axis=1, min_count=1)
             - (wH.shift() * RET_P).sum(axis=1, min_count=1))
    funding = (wH.shift() * FUND).sum(axis=1, min_count=1)     # shorts receive it
    cost, turnover, _ = trade_costs(wL, wH, aum)
    dead = (wL.shift().abs().where(RET_S.isna() & wL.shift().notna()).sum(axis=1)
            + wH.shift().abs().where(RET_P.isna() & wH.shift().notna()).sum(axis=1))
    net = gross + funding - cost.shift() - dead * EXIT_BPS * 1e-4
    return net, turnover, cost


# =========================================================== metrics ==========
def drawdown(rets):
    cum = (1 + rets).cumprod()
    return (cum - cum.cummax()) / cum.cummax()


def compute_stats(net, turnover, wL=None, wH=None, cost=None):
    net = net.dropna()
    if len(net) < 400 or net.std() == 0:
        return None
    to = turnover.reindex(net.index).fillna(0)
    d = pd.concat([net.rename('y'), MKT.reindex(net.index).rename('m')], axis=1).dropna()
    fit = sm.OLS(d['y'], sm.add_constant(d[['m']])).fit(
        cov_type='HAC', cov_kwds={'maxlags': 21})
    half = len(net) // 2
    sharpe = lambda s: s.mean() / s.std() * np.sqrt(ANN)
    out = {
        'Sharpe Ratio': sharpe(net),
        'Return': net.mean() * ANN,
        'Volatility': net.std() * np.sqrt(ANN),
        'Max DD': drawdown(net).min(),
        'Holding Period': 2 / to.mean() if to.mean() > 0 else np.nan,
        'Turnover': to.mean(),
        'Transaction Costs': (cost.reindex(net.index).mean() * ANN
                              if cost is not None else np.nan),
        'Market Beta': fit.params['m'],
        'Alpha t': fit.tvalues['const'],
        'Sharpe H1': sharpe(net[:half]),
        'Sharpe H2': sharpe(net[half:]),
    }
    if wL is not None:
        out['Names L'] = (wL > 0).sum(axis=1).replace(0, np.nan).mean()
        out['Names S'] = (wH > 0).sum(axis=1).replace(0, np.nan).mean()
    return out


def run(corr_window=CORR_WINDOW, vol_window=VOL_WINDOW, shrink=SHRINK,
        scheme='rank', screen=SCREEN, aum=AUM, rebal=REBAL,
        min_corr=None, min_vol=None):
    """One universe, one ranking.

    The sort runs over every coin with a beta and a spot price. The long leg
    holds any of them, because a long position is held in spot and needs no
    derivative. The short leg is restricted to coins with a live perpetual, which
    is the only place a short can actually be put on. Both legs are ranked
    against the same distribution so the sort stays coherent.

    `screen` is off by default: illiquidity is priced through the cost model
    rather than screened out. It is kept as a robustness lever.
    """
    size_by = None
    if scheme == 'precision':
        raw, se = estimate_beta(corr_window, vol_window, min_corr=min_corr,
                                min_vol=min_vol, with_se=True)
        size_by = (1 / se).replace([np.inf, -np.inf], np.nan)
        beta = shrink_beta(raw, shrink)
    else:
        beta = shrink_beta(estimate_beta(corr_window, vol_window,
                                         min_corr=min_corr, min_vol=min_vol), shrink)
    rankable = (beta.notna() & RET_S.notna() & ADV_S.notna() & (LIQ_S >= screen))
    shortable = rankable & RET_P.notna() & ADV_P.notna() & (LIQ_P >= screen)
    wL, wH = bab_weights(beta, rankable, shortable, scheme, rebal=rebal,
                         rank_ok=rankable, size_by=size_by)
    net, turnover, cost = backtest(wL, wH, aum)
    return net, turnover, wL, wH, cost


def fmt(df):
    d = df.copy()
    for c in ['Return', 'Volatility', 'Max DD', 'Transaction Costs']:
        if c in d: d[c] = d[c].map('{:.2%}'.format)
    for c in ['Sharpe Ratio', 'Holding Period', 'Alpha t', 'Sharpe H1', 'Sharpe H2',
              'Names L', 'Names S']:
        if c in d: d[c] = d[c].map('{:.2f}'.format)
    if 'Market Beta' in d: d['Market Beta'] = d['Market Beta'].map('{:+.3f}'.format)
    if 'Turnover' in d: d['Turnover'] = d['Turnover'].map('{:.4f}'.format)
    if 'Turnover per Year' in d:
        d['Turnover per Year'] = d['Turnover per Year'].map('{:.1f}'.format)
    return d


if __name__ == '__main__':
    os.makedirs('results', exist_ok=True)
    pd.set_option('display.width', 250)
    NL = '\n\n' + '=' * 100

    print('=' * 100)
    print('BASELINE: Frazzini-Pedersen construction, followed as closely as crypto allows')
    print(f'  beta      {CORR_WINDOW}d correlation on {AGG_DAYS}-day returns, '
          f'{VOL_WINDOW}d volatility, shrinkage w={SHRINK}')
    print(f'  portfolio rank-weighted legs (eq. 16), each scaled to beta 1 (eq. 17), monthly')
    print(f'  universe  every coin with a beta and a live perpetual '
          f'(min {MIN_NAMES} names per leg)')
    print(f'  costs     {FIXED_BPS}bps + {IMPACT_K} * sigma * sqrt(participation) at '
          f'${AUM:,.0f} of capital, {EXIT_BPS}bps delisting exit, realised funding')
    print('=' * 100)
    net, to, wL, wH, cost = run()
    base = pd.DataFrame([compute_stats(net, to, wL, wH, cost)], index=['Baseline (FP)'])
    print(fmt(base).T.to_string())
    net.dropna().to_pickle('results/baseline_returns.pkl')

    print(NL)
    print('ROBUSTNESS 1: estimation windows')
    print('=' * 100)
    rows = {}
    # Every cell uses the SAME minimum observation count, so the universe is held
    # fixed at ~147 names and 2,006 days and the window is the only thing that
    # varies. With the usual window/2 default a longer window also demands a
    # longer price history, which drops younger coins and confounds the
    # comparison: the 1825d row scored 0.33/0.76/0.58 on 85 names that way, and
    # 1.00/1.21/1.03 once the universe is held constant.
    for cw in [365, 730, 1095, 1460, 1825]:
        for vw in [90, 180, 365]:
            n, t, a, b, c = run(corr_window=cw, vol_window=vw,
                                min_corr=182, min_vol=45)
            s = compute_stats(n, t, a, b, c)
            if s: rows[f'corr {cw}d / vol {vw}d'] = s
    win = pd.DataFrame(rows).T
    print(fmt(win[['Sharpe Ratio', 'Return', 'Volatility', 'Max DD', 'Turnover',
                   'Market Beta', 'Alpha t', 'Names L']]).to_string())
    win.to_csv('results/robustness_windows.csv')

    print(NL)
    print('ROBUSTNESS 2: portfolio construction')
    print('=' * 100)
    rows = {}
    for scheme in ['rank', 'quintile']:
        for w in [0.4, 0.6, 1.0]:
            n, t, a, b, c = run(scheme=scheme, shrink=w)
            s = compute_stats(n, t, a, b, c)
            if s: rows[f'{scheme}-weighted, shrink w={w}'] = s
    con = pd.DataFrame(rows).T
    print(fmt(con[['Sharpe Ratio', 'Return', 'Volatility', 'Max DD', 'Turnover',
                   'Transaction Costs', 'Alpha t', 'Names L', 'Names S']]).to_string())
    con.to_csv('results/robustness_construction.csv')

    print(NL)
    print('ROBUSTNESS 3: capacity')
    print('=' * 100)
    cap = {}
    for aum, lab in [(1e6, '$1m'), (5e6, '$5m'), (2e7, '$20m'), (5e7, '$50m'),
                     (1e8, '$100m'), (2.5e8, '$250m'), (5e8, '$500m')]:
        n, t, a, b, c = run(aum=aum)
        st = compute_stats(n, t, a, b, c)
        if st: cap[lab] = st
    capdf = pd.DataFrame(cap).T
    print(fmt(capdf[['Sharpe Ratio', 'Return', 'Volatility', 'Max DD',
                     'Transaction Costs', 'Alpha t']]).to_string())
    capdf.to_csv('results/capacity.csv')

    print('\nsaved baseline_returns.pkl, robustness_windows.csv, '
          'robustness_construction.csv, capacity.csv')
