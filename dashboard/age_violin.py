"""Weighted violin geometry for aggregate counts, without synthetic people."""
import numpy as np
import pandas as pd
import plotly.graph_objects as go

BANDS = {"0–17": (0, 17), "18–24": (18, 24), "25–54": (25, 54),
         "55–64": (55, 64), "65+": (65, 119)}
COLORS = ["#7BA8D0", "#91C9B3", "#36816B", "#66A89B", "#C09B42"]


def weighted_density(counts, low, high):
    """1.25-year Gaussian smoothing, renormalized within the age band."""
    ages = np.arange(low, high+1)
    grid = np.linspace(max(0, low-.5), high+.5, (high-low+1)*8+1)
    kernels = np.exp(-.5*((grid[:, None]-ages)/1.25)**2)
    kernels /= np.trapezoid(kernels, grid, axis=0)
    return grid, kernels @ counts[low:high+1]


def scale_for_paths(*paths):
    """One persons-per-year width scale for every band, year and scenario."""
    maximum = 0.0
    for path in paths:
        for state in path:
            counts = state.sum(axis=0)
            for low, high in BANDS.values():
                maximum = max(maximum, float(weighted_density(counts, low, high)[1].max()))
    return max(maximum, 1.0)


def band_summary(state):
    counts = state.sum(axis=0)
    rows = []
    for name, (low, high) in BANDS.items():
        weights = counts[low:high+1]
        total = weights.sum()
        median = low+np.searchsorted(weights.cumsum(), total*.5) if total > 0 else np.nan
        rows.append({"Age range": name, "Residents": total, "Median age": median})
    return pd.DataFrame(rows)


def age_violin_figure(state, year, density_scale):
    counts = np.asarray(state, dtype=float).sum(axis=0)
    summary = band_summary(state)
    fig = go.Figure()
    labels = []
    for index, ((name, (low, high)), color) in enumerate(zip(BANDS.items(), COLORS)):
        total = float(summary.iloc[index]["Residents"])
        labels.append(f"{name}<br>{total:,.0f}")
        if total <= 0:
            continue
        ages, density = weighted_density(counts, low, high)
        widths = .40*density/density_scale
        fig.add_trace(go.Scatter(x=np.r_[index-widths, (index+widths)[::-1]],
                                y=np.r_[ages, ages[::-1]], mode="lines", fill="toself",
                                line=dict(color=color, width=1.5), fillcolor=color,
                                name=f"Ages {name}", showlegend=False, hoverinfo="skip"))
        discrete = np.arange(low, high+1)
        fig.add_trace(go.Scatter(x=np.full(len(discrete), index), y=discrete,
                                customdata=counts[low:high+1], mode="markers",
                                marker=dict(size=10, opacity=0), showlegend=False,
                                hovertemplate="Age %{y:.0f}<br>%{customdata:,.0f} modeled residents<extra></extra>"))
        weights = counts[low:high+1]
        quartiles = low+np.searchsorted(weights.cumsum(), total*np.array([.25, .5, .75]))
        fig.add_trace(go.Scatter(x=[index, index], y=quartiles[[0, 2]], mode="lines",
                                line=dict(color="#E9EDEF", width=3), showlegend=False, hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=[index], y=[quartiles[1]], mode="markers",
                                marker=dict(size=7, color="#243A42", line=dict(color="#FFFFFF", width=1)),
                                showlegend=False, hovertemplate=f"Ages {name}<br>Median age: %{{y:.0f}}<br>{total:,.0f} residents<extra></extra>"))
    fig.update_layout(title=dict(text=f"Resident age distribution · {year}", font=dict(size=17)),
                      height=450, margin=dict(l=10, r=10, t=45, b=65),
                      xaxis=dict(tickmode="array", tickvals=list(range(len(BANDS))), ticktext=labels,
                                 range=[-.55, len(BANDS)-.45], title="Age range · resident count", fixedrange=True),
                      yaxis=dict(title="Age (years)", range=[0, 120], dtick=20, fixedrange=True),
                      hovermode="closest")
    return fig
