"""Observed annual LAUS history and an explicitly separate exploratory forecast."""
from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard.presentation import plot_chart

from labor_force_forecast import forecast_county, rolling_checks
from dashboard.pressure_charts import TEAL, GOLD, available, current, series


def labor_force_figure(history, forecast, monthly=None):
    annual_dates = pd.to_datetime(history.year.astype(str)+"-12-31")
    future_dates = pd.to_datetime(forecast.year.astype(str)+"-12-31")
    anchor_date, anchor_value = annual_dates.iloc[-1], history.labor_force.iloc[-1]
    band_dates = [anchor_date, *future_dates]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=band_dates, y=[anchor_value, *forecast.lower80],
                            mode="lines", line=dict(width=0), showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=band_dates, y=[anchor_value, *forecast.upper80],
                            mode="lines", line=dict(width=0), fill="tonexty",
                            fillcolor="rgba(54,129,107,0.18)", name="80% model prediction interval",
                            hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=annual_dates, y=history.labor_force, mode="lines+markers",
                            name="Observed annual average", line=dict(color=TEAL),
                            hovertemplate="%{x|%Y} annual average<br>%{y:,.0f} residents<extra></extra>"))
    fig.add_trace(go.Scatter(x=band_dates, y=[anchor_value, *forecast["median"]], mode="lines",
                            name="Median annual projection", line=dict(color=TEAL, dash="dot", width=3),
                            hovertemplate="%{x|%Y} annual projection<br>%{y:,.0f} residents<extra></extra>"))
    if available(monthly):
        fig.add_trace(go.Scatter(x=[monthly.period_end], y=[monthly.estimate], mode="markers",
                                marker=dict(color=GOLD, symbol="diamond", size=10),
                                name="Latest monthly observation",
                                hovertemplate="%{x|%b %Y} monthly estimate<br>%{y:,.0f} residents<extra></extra>"))
    fig.add_vline(x=anchor_date.timestamp()*1000, line_dash="dot", line_color="#7F8C8D")
    fig.update_layout(height=400, margin=dict(l=10, r=12, t=20, b=110),
                      xaxis=dict(title="Reference year", tickformat="%Y", dtick="M48"),
                      yaxis=dict(title="Resident labor force · age 16+", tickformat=",.0f"),
                      legend=dict(orientation="h", y=-.3, yanchor="top", font=dict(size=10)))
    return fig


def render_labor_force_forecast(annual_laus, frame, county, key="labor_force_projection_"):
    st.markdown("#### Residents in the labor force: ten-year outlook")
    as_of_year = date.today().year
    try:
        history, forecast = forecast_county(annual_laus, county, as_of_year, horizon=10)
    except ValueError as exc:
        st.info(str(exc))
        return None
    monthly = current(series(frame, "laus", "labor_force", county))
    st.caption(f"EXPLORATORY PROJECTION · Through {as_of_year+10}. Solid line: annual LAUS observations. "
               "Dotted line: model projection. Shading: nominal 80% pointwise prediction interval.")
    plot_chart(labor_force_figure(history, forecast, monthly), width="stretch", key=key+"chart")
    final = forecast.iloc[-1]
    st.caption(f"{int(final.year)} median: {final['median']:,.0f} residents · 80% model range: {final.lower80:,.0f}–{final.upper80:,.0f}.")
    st.caption(f"Uses annual averages through {int(history.year.iloc[-1])}; {as_of_year} is also an annual projection, "
               f"followed by ten future calendar years. The monthly diamond is separate and does not update the annual model. "
               "Annual averages are plotted at year-end. This age-16+ labor-force series differs from the approximate age-18–64 demographic forecast above.")
    with st.expander("Labor-force forecast assumptions, checks and values"):
        st.write("This dashboard-generated forecast extends the historical rate of annual labor-force change. It is not an official BLS projection. "
                 "The log random-walk model assumes independent annual percentage changes with constant drift and variance. "
                 "The interval includes future annual variability and uncertainty in the estimated drift; it does not account for future policy, structural breaks, "
                 "data revisions or uncertainty in the published baseline. It is an interval for each year, not an 80% probability that the entire path stays in the band.")
        st.write("Ten-year interval coverage has not been validated. The short history permits only a few retrospective checks; their results are diagnostic, not proof of long-range accuracy.")
        st.dataframe(rolling_checks(history), hide_index=True, width="stretch")
        st.dataframe(forecast.rename(columns={"year":"Year", "median":"Median annual projection",
                                             "lower80":"Lower 80% bound", "upper80":"Upper 80% bound"}),
                     hide_index=True, width="stretch")
        st.caption(f"Fit window: {int(history.year.min())}–{int(history.year.max())}; "
                   f"{forecast.attrs['increments']} annual changes. Source: local BLS LAUS annual-average series "
                   f"LAUCN{county}0000000006. Monthly data, where shown, retain their separately adopted source vintage.")
        st.markdown("[BLS resident labor-force definitions](https://www.bls.gov/opub/hom/lau/concepts.htm) · "
                    "[Forecast intervals and drift methods](https://otexts.com/fpp2/prediction-intervals.html)")
        st.download_button("Download labor-force projection", forecast.to_csv(index=False).encode(),
                           f"{county}_labor_force_projection_{as_of_year+10}.csv", "text/csv", key=key+"download")
    return forecast
