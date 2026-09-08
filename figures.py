"""Figures for the writeup. Writes PNGs to results/figures/.

Each figure is self-contained and captioned in the outline. Run:
    python figures.py
"""
import os
import numpy as np, pandas as pd, statsmodels.api as sm, warnings
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
warnings.filterwarnings('ignore')

import strategy as S

OUT = 'results/figures'
os.makedirs(OUT, exist_ok=True)
plt.rcParams.update({'figure.dpi': 130, 'font.size': 10,
                     'axes.spines.top': False, 'axes.spines.right': False})
PCT = FuncFormatter(lambda v, p: f'{v:.0%}')
BLUE, RED, GREEN, GREY = '#2E6E9E', '#B4483C', '#3E8E63', '#8A8A8A'

DATA = 'data'
retSpot = pd.read_pickle(f'{DATA}/ret.pkl')
volSpot = pd.read_pickle(f'{DATA}/volume.pkl')
mktRet = pd.read_pickle(f'{DATA}/retMkt.pkl')
beta365 = pd.read_pickle(f'{DATA}/betaShrunk.pkl')
babFac = pd.read_pickle(f'{DATA}/retBAB.pkl')
funding = pd.read_pickle(f'{DATA}/funding.pkl')


def save(fig, name):
    fig.tight_layout()
    fig.savefig(f'{OUT}/{name}.png', bbox_inches='tight')
    plt.close(fig)
    print(f'  {OUT}/{name}.png')


# ---------------------------------------------- 2. universe over time --------
def fig_universe():
    fig, ax = plt.subplots(figsize=(9, 4.2))
    tradeable = retSpot.notna().sum(axis=1)
    withbeta = (retSpot.notna() & beta365.reindex_like(retSpot).notna()).sum(axis=1)
    liq = volSpot.reindex_like(retSpot).rolling(30, min_periods=15).median().shift()
    liquid = (retSpot.notna() & beta365.reindex_like(retSpot).notna() & (liq >= 5e6)).sum(axis=1)

    ax.fill_between(tradeable.index, tradeable, color=GREY, alpha=.30, label='Listed and Trading')
    ax.fill_between(withbeta.index, withbeta, color=BLUE, alpha=.55, label='+ Beta Estimable')
    ax.fill_between(liquid.index, liquid, color=RED, alpha=.80, label='+ Volume $\\geq$ \\$5m')
    ax.set_ylabel('Number of Coins')
    ax.legend(frameon=False, loc='upper left', fontsize=9)
    ax.grid(alpha=.25)
    save(fig, 'fig2_universe_over_time')


# ------------------------------- 3. daily vs 3-day correlation ---------------
def fig_correlation_attenuation():
    """Per-coin full-sample correlations, grouped by the coin's median volume.

    Measured per coin rather than on rolling windows: a rolling window needs 180
    observations, which already excludes the shortest-lived and least liquid
    coins and so understates the attenuation.
    """
    r = np.log(1 + retSpot)
    m = np.log(1 + mktRet)
    mv = volSpot.reindex_like(retSpot).median()
    usable = [c for c in retSpot.columns
              if retSpot[c].notna().sum() >= 400 and pd.notna(mv[c])]
    grp = pd.qcut(mv[usable].rank(method='first'), 5,
                  labels=['Q1', 'Q2', 'Q3', 'Q4', 'Q5'])

    rows = []
    for c in usable:
        x = pd.concat([r[c].rename('r'), m.rename('m')], axis=1).dropna()
        x3 = pd.concat([r[c].rolling(3).sum().rename('r'),
                        m.rolling(3).sum().rename('m')], axis=1).dropna()
        if len(x) < 300:
            continue
        rows.append({'q': grp[c], 'daily': x['r'].corr(x['m']),
                     'three': x3['r'].corr(x3['m'])})
    t = (pd.DataFrame(rows).groupby('q', observed=True)
         .agg(daily=('daily', 'median'), three=('three', 'median')))

    fig, ax = plt.subplots(figsize=(7.6, 4.3))
    x = np.arange(len(t))
    ax.bar(x - .19, t['daily'], .38, label='Daily Returns', color=GREY)
    ax.bar(x + .19, t['three'], .38, label='3-Day Overlapping Returns', color=BLUE)
    ax.set_xticks(x)
    ax.set_xticklabels(['Q1\nLeast Liquid', 'Q2', 'Q3', 'Q4', 'Q5\nMost Liquid'])
    ax.set_ylabel('Median Correlation with the Market')
    ax.set_ylim(0, .85)
    ax.set_title('Illiquid coins are genuinely less correlated,\n'
                 'and 3-day aggregation barely changes that', fontsize=11)
    ax.legend(frameon=False, fontsize=9, loc='upper left')
    ax.grid(alpha=.25, axis='y')
    for xi, (a, b) in enumerate(zip(t['daily'], t['three'])):
        ax.annotate(f'{a:.2f}', (xi - .19, a), ha='center', va='bottom', fontsize=8)
        ax.annotate(f'{b:.2f}', (xi + .19, b), ha='center', va='bottom', fontsize=8)
    ax.annotate(f'3-Day Aggregation Lifts Q1 by Only {t.loc["Q1","three"]-t.loc["Q1","daily"]:+.3f}',
                xy=(.30, .06), xycoords='axes fraction', fontsize=9, color=RED)
    save(fig, 'fig3_correlation_attenuation')


# ------------------------------------- 4. factor cumulative + beta -----------
def fig_factor():
    f = babFac.dropna()
    roll = pd.concat([f.rename('y'), mktRet.rename('m')], axis=1).dropna()
    rb = (roll['y'].rolling(180).cov(roll['m']) / roll['m'].rolling(180).var())

    fig, ax = plt.subplots(2, 1, figsize=(9, 5.6), sharex=True,
                           gridspec_kw={'height_ratios': [2.4, 1]})
    btc = retSpot['BTCUSDT'].reindex(f.index).fillna(0)
    ax[0].plot(f.index, (1 + f).cumprod(), color=BLUE, lw=1.6, label='BAB Factor')
    ax[0].plot(f.index, (1 + btc).cumprod(), color=GREEN, lw=1.3, label='Bitcoin')
    ax[0].plot(mktRet.reindex(f.index).index,
               (1 + mktRet.reindex(f.index).fillna(0)).cumprod(),
               color=GREY, lw=1.2, label='Market Index')
    ax[0].set_yscale('log'); ax[0].set_ylabel('Growth of \\$1 (Log Scale)')
    ax[0].set_title('The BAB factor, gross of costs')
    ax[0].legend(frameon=False, fontsize=9); ax[0].grid(alpha=.25)

    ax[1].plot(rb.index, rb, color=RED, lw=1.2)
    ax[1].axhline(0, color='k', lw=.8)
    ax[1].set_ylabel('Rolling 180-Day\nMarket Beta')
    ax[1].set_ylim(-.6, .6); ax[1].grid(alpha=.25)
    save(fig, 'fig4_factor_cumulative')


# -------------------------------- 6. SML slope by funding quartile -----------
def fig_sml_by_funding():
    idx = beta365.index.intersection(funding.index)
    agg = (funding.median(axis=1) * 365).reindex(idx)
    bLag = beta365.reindex(idx).shift()
    r = retSpot.reindex(idx)
    sl = {}
    for dt in idx:
        x, y = bLag.loc[dt], r.loc[dt]
        ok = x.notna() & y.notna()
        if ok.sum() >= 30:
            sl[dt] = sm.OLS(y[ok].values, sm.add_constant(x[ok].values)).fit().params[1]
    slope = pd.Series(sl)
    d = pd.concat([slope.rename('s'), agg.shift(21).rename('f')], axis=1).dropna()
    d['q'] = pd.qcut(d['f'].rank(method='first'), 4,
                     labels=['Cheapest', 'Q2', 'Q3', 'Dearest'])
    g = d.groupby('q', observed=True)
    mean = g['s'].mean() * 365
    se = g['s'].apply(lambda s: s.std() / np.sqrt(len(s))) * 365
    med = g['f'].median()

    fig, ax = plt.subplots(figsize=(7.5, 4.4))
    cols = [GREEN if v > 0 else RED for v in mean]
    ax.bar(range(4), mean, yerr=1.96 * se, color=cols, capsize=4)
    ax.axhline(0, color='k', lw=1)
    ax.set_xticks(range(4))
    ax.set_xticklabels([f'{l}\n({v:.0%} Funding)' for l, v in zip(mean.index, med)])
    ax.set_ylabel('Realized SML Slope, Annualized')
    ax.grid(alpha=.25, axis='y')
    save(fig, 'fig6_sml_by_funding')


# ------------------------- 8. universe cost of the estimation window ---------
def fig_window_universe():
    rows = {}
    for cw in [365, 730, 1095, 1460, 1825]:
        b = S.estimate_beta(corr_window=cw, vol_window=365)
        rows[cw] = {'beta estimable': (S.RET_S.notna() & b.notna()).sum(1).mean(),
                    'and volume $\\geq$ \\$5m':
                        (S.RET_S.notna() & b.notna() & (S.LIQ_S >= 5e6)).sum(1).mean()}
    t = pd.DataFrame(rows).T

    fig, ax = plt.subplots(figsize=(7.5, 4.2))
    x = np.arange(len(t))
    ax.bar(x - .19, t['beta estimable'], .38, color=BLUE, label='Beta Estimable')
    ax.bar(x + .19, t['and volume $\\geq$ \\$5m'], .38, color=RED,
           label='and Volume $\\geq$ \\$5m')
    ax.set_xticks(x); ax.set_xticklabels([f'{c}d' for c in t.index])
    ax.set_xlabel('Correlation Estimation Window')
    ax.set_ylabel('Coins Available per Day')
    ax.set_title("FP's five-year window costs most of the crypto universe")
    ax.legend(frameon=False, fontsize=9); ax.grid(alpha=.25, axis='y')
    for xi, (a, b) in enumerate(zip(t.iloc[:, 0], t.iloc[:, 1])):
        ax.annotate(f'{a:.0f}', (xi - .19, a), ha='center', va='bottom', fontsize=8)
        ax.annotate(f'{b:.0f}', (xi + .19, b), ha='center', va='bottom', fontsize=8)
    save(fig, 'fig8_window_universe')


# --------------------------------- 9. Sharpe heatmap over the windows --------
def fig_window_heatmap():
    corr_ws = [365, 730, 1095, 1460, 1825]
    vol_ws = [90, 180, 365]
    sh = np.full((len(corr_ws), len(vol_ws)), np.nan)
    nm = np.full((len(corr_ws), len(vol_ws)), np.nan)
    for i, cw in enumerate(corr_ws):
        for j, vw in enumerate(vol_ws):
            net, to, wL, wH, cost = S.run(corr_window=cw, vol_window=vw,
                                          min_corr=182, min_vol=45)
            st = S.compute_stats(net, to, wL, wH, cost)
            if st:
                sh[i, j] = st['Sharpe Ratio']; nm[i, j] = st['Names L']

    fig, ax = plt.subplots(figsize=(6.6, 4.6))
    im = ax.imshow(sh, cmap='RdYlGn', vmin=0.3, vmax=1.3, aspect='auto')
    ax.set_xticks(range(len(vol_ws))); ax.set_xticklabels([f'{v}d' for v in vol_ws])
    ax.set_yticks(range(len(corr_ws))); ax.set_yticklabels([f'{c}d' for c in corr_ws])
    ax.set_xlabel('Volatility Window'); ax.set_ylabel('Correlation Window')
    ax.set_title('Net Sharpe, with names per leg')
    for i in range(len(corr_ws)):
        for j in range(len(vol_ws)):
            if not np.isnan(sh[i, j]):
                ax.text(j, i, f'{sh[i,j]:.2f}\n({nm[i,j]:.0f} names)',
                        ha='center', va='center', fontsize=9)
    fig.colorbar(im, ax=ax, label='Sharpe Ratio', shrink=.85)
    save(fig, 'fig9_window_heatmap')


# ------------------------------------- 1. the security market line ----------
QS = [(0, .2), (.2, .4), (.4, .6), (.6, .8), (.8, 1.01)]


def _beta_quintiles():
    """Quintile portfolios sorted on lagged beta, rebalanced monthly.

    Monthly to match the BAB factor's own rebalancing. Rebalancing daily churns
    membership on estimation noise and makes the alpha pattern non-monotonic.
    """
    q = beta365.rank(axis=1, pct=True)
    firstOfMonth = ~beta365.index.to_period('M').duplicated()
    monthMask = pd.DataFrame(
        np.broadcast_to(firstOfMonth[:, None], beta365.shape),
        index=beta365.index, columns=beta365.columns)

    rows = {}
    for i, (lo, hi) in enumerate(QS):
        m = ((q > lo) & (q <= hi)).astype(float)
        m = m.divide(m.sum(axis=1).replace(0, np.nan), axis=0)
        m = m.where(monthMask).ffill().where(beta365.notna())   # hold for the month
        m = m.divide(m.sum(axis=1).replace(0, np.nan), axis=0)  # renormalise
        r = (m.shift() * retSpot).sum(axis=1, min_count=1)
        d = pd.concat([r.rename('y'), mktRet.rename('m')], axis=1).dropna()
        f = sm.OLS(d['y'], sm.add_constant(d[['m']])).fit()
        rows[f'Q{i+1}'] = {'beta': f.params['m'], 'ann_ret': r.mean() * 365,
                           'alpha': f.params['const'] * 365,
                           'sharpe': r.mean() / r.std() * np.sqrt(365)}
    return pd.DataFrame(rows).T


def fig_sml():
    t = _beta_quintiles()
    b, y = t['beta'].values, t['ann_ret'].values

    fig, ax = plt.subplots(figsize=(7.4, 4.8))
    # fit only over the range the data actually spans
    slope = np.polyfit(b, y, 1)
    pad = (max(b) - min(b)) * 0.18
    xf = np.linspace(min(b) - pad, max(b) + pad, 20)
    ax.plot(xf, np.polyval(slope, xf), color=RED, lw=2,
            label=f'Fitted, Slope {slope[0]:+.2f}')
    ax.scatter(b, y, s=95, zorder=3, color=BLUE, edgecolor='k', lw=.5)
    for lab, bi, yi in zip(t.index, b, y):
        ax.annotate(lab, (bi, yi), textcoords='offset points',
                    xytext=(8, 5), fontsize=9)
    ax.set_xlabel('Realized Beta')
    ax.set_ylabel('Annualized Return')
    ax.yaxis.set_major_formatter(PCT)
    ax.set_xlim(min(b) - pad, max(b) + pad)
    yr = max(y) - min(y)
    ax.set_ylim(min(y) - yr * .45, max(y) + yr * .35)
    ax.set_title('Security Market Line')
    ax.legend(frameon=False, fontsize=8.5, loc='lower left')
    ax.grid(alpha=.25)
    save(fig, 'fig1_security_market_line')


# ------------------------------- 5. funding across the beta cross-section ----
def fig_funding_by_beta():
    common = beta365.columns.intersection(funding.columns)
    q = beta365[common].rank(axis=1, pct=True)
    med = [funding[common].where((q > lo) & (q <= hi)).stack().median() * 365
           for lo, hi in QS]
    fig, ax = plt.subplots(figsize=(7, 4.2))
    ax.bar([f'Q{i+1}' for i in range(5)], med, color=BLUE)
    ax.set_xlabel('Beta Quintile')
    ax.set_ylabel('Median Funding Rate, Annualized')
    ax.yaxis.set_major_formatter(PCT)
    ax.set_ylim(0, max(med) * 1.25)
    ax.set_title('Leverage costs the same across the beta cross-section')
    ax.grid(alpha=.25, axis='y')
    for i, v in enumerate(med):
        ax.annotate(f'{v:.1%}', (i, v), ha='center', va='bottom', fontsize=9,
                    xytext=(0, 3), textcoords='offset points')
    save(fig, 'fig5_funding_by_beta')


# ------------------------------ 7. the price of leverage, observed ----------
def fig_funding_over_time():
    """Volume-weighted aggregate funding: what the average dollar of perpetual
    notional actually paid. Weighted rather than median because the question is
    the cost faced by the marginal levered dollar, not by the typical coin."""
    volPerp = pd.read_pickle(f'{DATA}/volume_perp.pkl')
    adv = volPerp.rolling(30, min_periods=15).mean().where(funding.notna())
    w = adv.div(adv.sum(axis=1), axis=0).shift()
    agg = ((w * funding).sum(axis=1, min_count=1) * 365).dropna()
    # smoothed over TIME, not across coins; both series are volume-weighted
    smooth = agg.rolling(30, min_periods=15).median()
    rf = pd.read_pickle(f'{DATA}/riskfree.pkl')['DGS1MO'].reindex(agg.index).ffill()

    fig, ax = plt.subplots(figsize=(9.5, 4.3))
    ax.axhline(0, color='k', lw=.8)
    ax.plot(agg.index, agg.clip(-0.6, 1.6), color=GREY, lw=.6, alpha=.55,
            label='Volume-Weighted, Daily')
    ax.plot(smooth.index, smooth, color=BLUE, lw=1.8, label='Volume-Weighted, 30-Day Trailing Median')
    ax.plot(rf.index, rf, color=RED, ls='--', lw=1.3,
            label='One-Month Treasury Bill')
    ax.set_ylim(-0.6, 1.2)
    ax.yaxis.set_major_formatter(PCT)
    ax.set_ylabel('Annualized Funding Rate')
    ax.legend(frameon=False, fontsize=8.5, loc='upper right')
    ax.grid(alpha=.25)
    save(fig, 'fig7_funding_over_time')


# ------------------------------ 10. how the cost model works ----------------
def fig_cost_model():
    """The square-root impact law against the flat assumption it replaces."""
    fig, ax = plt.subplots(figsize=(7.2, 4.4))

    part = np.linspace(0, 0.05, 400)
    for sig, col, lab in [(0.03, GREEN, 'Low Volatility Coin (3% Daily)'),
                          (0.05, BLUE,  'Typical Coin (5% Daily)'),
                          (0.09, RED,   'High Volatility Coin (9% Daily)')]:
        ax.plot(part * 100, 7 + 1.0 * sig * np.sqrt(part) * 1e4,
                color=col, lw=1.8, label=lab)
    for x, lab in [(0.022, 'Median Trade'), (3.566, '99th Percentile Trade')]:
        ax.axvline(x, color=GREY, ls=':', lw=1.1)
        ax.annotate(lab, xy=(x + 0.02, 2), rotation=90, fontsize=8, color=GREY,
                    ha='left', va='bottom')
    ax.set_xlabel("Participation: Trade Size as a % of the Coin's Daily Volume")
    ax.set_ylabel('Cost, Basis Points')
    ax.set_ylim(0, 90)
    ax.legend(frameon=False, fontsize=9, loc='upper left')
    ax.grid(alpha=.25)
    save(fig, 'fig10_cost_model')


# ------------------------------------------------ 11. capacity --------------
def fig_capacity():
    """Net Sharpe and annual cost as the book grows, under the impact model."""
    aums = [1e6, 2e6, 5e6, 1e7, 2e7, 5e7, 1e8, 2e8, 3.5e8, 5e8]
    sh, cst = [], []
    for a in aums:
        net, to, wL, wH, cost = S.run(aum=a)
        st = S.compute_stats(net, to, wL, wH, cost)
        sh.append(st['Sharpe Ratio']); cst.append(st['Transaction Costs'] * 100)

    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    ax.plot(aums, sh, color=BLUE, lw=2, marker='o', ms=4, label='Net Sharpe Ratio')
    ax.axhline(0, color='k', lw=.8)
    ax.set_xscale('log')
    ax.set_xlabel('Capital Deployed')
    ax.set_ylabel('Net Sharpe Ratio', color=BLUE)
    ax.tick_params(axis='y', labelcolor=BLUE)
    ax.set_xticks([1e6, 1e7, 1e8, 5e8])
    ax.set_xticklabels(['$1m', '$10m', '$100m', '$500m'])
    ax.grid(alpha=.25)

    ax2 = ax.twinx()
    ax2.plot(aums, cst, color=RED, lw=1.6, ls='--', marker='s', ms=3.5,
             label='Execution Cost')
    ax2.set_ylabel('Execution Cost, % per Year', color=RED)
    ax2.tick_params(axis='y', labelcolor=RED)

    h1, l1 = ax.get_legend_handles_labels(); h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, frameon=False, fontsize=9, loc='lower left')
    save(fig, 'fig11_capacity')


# ------------------------------------ 12. tradeable strategy vs benchmarks ---
def fig_strategy_cumulative():
    """Growth of $1 in the baseline strategy against Bitcoin and the market.

    All three are excess of the one-month bill, matching the baseline table: the
    strategy is self-financing so its raw return is already an excess return.
    """
    net = S.run()[0].dropna()
    idx = net.index
    rf = (pd.read_pickle(f'{DATA}/riskfree.pkl')['DGS1MO']
            .reindex(idx).ffill().bfill() / 100 / 365)
    btc = (S.RET_S['BTCUSDT'].reindex(idx) - rf).fillna(0)
    mkt = (S.MKT.reindex(idx) - rf).fillna(0)

    fig, ax = plt.subplots(figsize=(9, 4.8))
    for r, col, lw, lab in [(net, BLUE, 1.8, 'BAB Strategy'),
                            (btc, GREEN, 1.3, 'Bitcoin'),
                            (mkt, GREY, 1.2, 'Market Index')]:
        ax.plot(idx, (1 + r).cumprod(), color=col, lw=lw, label=lab)

    ax.set_yscale('log')
    ax.set_ylabel('Growth of \\$1, Excess of Cash (Log Scale)')
    ax.set_xlabel('Date')
    ax.axhline(1, color='k', lw=.8)
    ax.legend(frameon=False, fontsize=9, loc='upper left')
    ax.grid(alpha=.25, which='both')
    ax.set_yticks([0.25, 0.5, 1, 2, 3, 4])
    ax.get_yaxis().set_minor_formatter(plt.NullFormatter())
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, p: f'{v:g}x'))
    save(fig, 'fig12_strategy_cumulative')


# ------------------------------- 13. best specification vs the benchmarks ----
def fig_best_vs_benchmarks():
    """Growth of $1 in the best specification against the baseline and buy-and-hold.

    All series are excess of the one-month bill, matching the tables: the BAB
    portfolios are self-financing, so their raw returns are already excess.
    """
    best = S.run(corr_window=1460, vol_window=180, scheme='precision',
                 min_corr=182, min_vol=45)[0].dropna()
    base = S.run(corr_window=365, vol_window=180, scheme='rank',
                 min_corr=182, min_vol=45)[0].dropna()
    idx = best.index.intersection(base.index)
    rf = (pd.read_pickle(f'{DATA}/riskfree.pkl')['DGS1MO']
            .reindex(idx).ffill().bfill() / 100 / 365)
    btc = (S.RET_S['BTCUSDT'].reindex(idx) - rf).fillna(0)
    mkt = (S.MKT.reindex(idx) - rf).fillna(0)

    # Sharpe-optimal mix of the best specification with Bitcoin
    X = pd.concat([best.reindex(idx).rename('a'), btc.rename('b')], axis=1).dropna()
    w = np.linalg.solve(X.cov().values * 365, X.mean().values * 365)
    w = w / w.sum()
    blend = (X * w).sum(axis=1).reindex(idx)

    fig, ax = plt.subplots(figsize=(9, 4.8))
    series = [(blend, BLUE, 2.0,
               f'Precision-weighted 1460d + {w[1]:.0%} Bitcoin (Sharpe-optimal)'),
              (base.reindex(idx), RED, 1.5, 'Baseline BAB (rank, 365d)'),
              (btc, GREEN, 1.3, 'Bitcoin'),
              (mkt, GREY, 1.2, 'Market Index')]
    for r, col, lw, lab in series:
        ax.plot(idx, (1 + r).cumprod(), color=col, lw=lw, label=lab)

    ax.set_yscale('log')
    ax.set_ylabel('Growth of \\$1, Excess of Cash (Log Scale)')
    ax.set_xlabel('Date')
    ax.axhline(1, color='k', lw=.8)
    ax.set_yticks([0.25, 0.5, 1, 2, 4, 8])
    ax.get_yaxis().set_minor_formatter(plt.NullFormatter())
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, p: f'{v:g}x'))
    ax.legend(frameon=False, fontsize=9, loc='upper left')
    ax.grid(alpha=.25, which='both')
    save(fig, 'fig13_best_vs_benchmarks')


if __name__ == '__main__':
    print('building figures...')
    fig_sml()
    fig_universe()
    fig_correlation_attenuation()
    fig_factor()
    fig_funding_by_beta()
    fig_sml_by_funding()
    fig_funding_over_time()
    fig_window_universe()
    fig_window_heatmap()
    fig_cost_model()
    fig_strategy_cumulative()
    fig_best_vs_benchmarks()
    fig_capacity()
    print('done')
