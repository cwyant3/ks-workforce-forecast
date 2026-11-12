"""Readable chart headings and occupation context at any content width."""
import html
from hashlib import sha256
import re

import plotly.graph_objects as go
import streamlit as st


def _plain_title(text):
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"</?(?:b|i|em|strong|sup|sub)>", "", text, flags=re.IGNORECASE)
    return html.unescape(text)


def _markdown_text(text):
    return re.sub(r"([\\`*_{}\[\]<>])", r"\\\1", str(text))


def plot_chart(figure, **kwargs):
    """Let page text wrap the full heading instead of clipping it in an SVG."""
    title = figure.layout.title.text
    if title:
        st.markdown(f"**{_markdown_text(_plain_title(title))}**")
        # Titles distinguished otherwise identical charts in different tabs.
        kwargs.setdefault("key", "plot-" + sha256(figure.to_json().encode()).hexdigest()[:16])
        # Leave the caller's figure intact.
        figure = go.Figure(figure)
        figure.update_layout(title_text=None)
    return st.plotly_chart(figure, **kwargs)


def occupation_context(title, soc):
    """Expose the complete selected name and SOC without hover or a wide table."""
    st.markdown(f"**Selected occupation:** {_markdown_text(title)}  \n**SOC:** `{soc}`")
