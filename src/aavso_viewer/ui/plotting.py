"""Shared matplotlib plotting helpers for thumbnails and chart detail dialogs."""

from __future__ import annotations

import pandas as pd
from matplotlib.ticker import MaxNLocator

ROLE_COLORS = {"target": "#1565c0", "check": "#c62828"}
# VPhot's own filter-color convention -- B/V/R map onto the intuitive blue/green/red.
FILTER_COLORS = {"B": "#1565c0", "V": "#2e7d32", "R": "#c62828"}
_FALLBACK_PALETTE = [
    "#8c564b", "#9467bd", "#ff7f0e", "#17becf", "#e377c2", "#bcbd22", "#7f7f7f",
]
AIRMASS_COLOR = "#1565c0"
_STAR_PALETTE = [
    "#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e",
    "#17becf", "#8c564b", "#e377c2", "#bcbd22", "#7f7f7f",
]


def filter_color(filt: str) -> str:
    if filt in FILTER_COLORS:
        return FILTER_COLORS[filt]
    return _FALLBACK_PALETTE[abs(hash(filt)) % len(_FALLBACK_PALETTE)]


def color_for(name: str, ordered_names: list[str]) -> str:
    idx = ordered_names.index(name) if name in ordered_names else 0
    return _STAR_PALETTE[idx % len(_STAR_PALETTE)]


def _draw(ax, x, y, color: str, thumbnail: bool, label: str | None = None) -> None:
    """Thumbnails draw a connected line (readable at a glance, matching VPhot's own
    small-chart style); detail views plot discrete points instead -- real photometry
    is unevenly sampled, and a connected line at full size implies interpolation
    between measurements that were never actually taken."""
    if thumbnail:
        ax.plot(x, y, "-", color=color, linewidth=1.2, alpha=0.9, label=label)
    else:
        ax.plot(x, y, marker="o", linestyle="none", markersize=2.5, color=color, alpha=0.7, label=label)


def _style_axes(ax, thumbnail: bool, xlabel: str, ylabel: str) -> None:
    ax.grid(True, linestyle="--", linewidth=0.4, alpha=0.4)
    label_size = 6 if thumbnail else 9
    tick_size = 5 if thumbnail else 8
    ax.set_xlabel(xlabel, fontsize=label_size)
    ax.set_ylabel(ylabel, fontsize=label_size)
    ax.tick_params(labelsize=tick_size)
    if thumbnail:
        ax.xaxis.set_major_locator(MaxNLocator(nbins=3))
        ax.yaxis.set_major_locator(MaxNLocator(nbins=4))
    else:
        # Discrete points autoscale tight to the data's exact min/max, with zero
        # headroom above/below -- that reads as far noisier than it is. Matplotlib's
        # own default margin (0.05) turned out too subtle to notice on a narrow-range
        # chart; a clearly visible margin gives real scatter some perspective without
        # flattening the pattern into an unreadable band.
        ax.margins(y=0.25)


def plot_light_curve(ax, df: pd.DataFrame, roles: tuple[str, ...], thumbnail: bool) -> None:
    if df.empty:
        return
    base = int(df["DATE"].min())
    labeled: set[str] = set()
    for role in roles:
        sub = df.loc[df["role"] == role]
        for _, g in sub.groupby("FILT"):
            g = g.sort_values("DATE")
            label = None if role in labeled else role
            labeled.add(role)
            _draw(ax, g["DATE"] - base, g["mag"], ROLE_COLORS[role], thumbnail, label=label)
    ax.invert_yaxis()
    _style_axes(ax, thumbnail, xlabel=f"JD+{base}", ylabel="Magnitude")
    if not thumbnail:
        ax.legend(fontsize=8)


def plot_comp_stars(ax, df: pd.DataFrame, thumbnail: bool) -> None:
    if df.empty:
        return
    stars = sorted(df["star"].unique())
    base = int(df["JD"].min())
    for star in stars:
        g = df.loc[df["star"] == star].sort_values("JD")
        _draw(ax, g["JD"] - base, g["mag"], color_for(star, stars), thumbnail, label=star)
    ax.invert_yaxis()
    _style_axes(ax, thumbnail, xlabel=f"JD+{base}", ylabel="Magnitude")
    if not thumbnail:
        ax.legend(fontsize=7, ncol=2)


def plot_diagnostic(ax, df: pd.DataFrame, thumbnail: bool, ylabel: str) -> None:
    if df.empty:
        return
    base = int(df["JD"].min())
    filters = sorted(df["Filter"].unique())
    for filt in filters:
        g = df.loc[df["Filter"] == filt].sort_values("JD")
        _draw(ax, g["JD"] - base, g["value"], filter_color(filt), thumbnail, label=filt)
    _style_axes(ax, thumbnail, xlabel=f"JD+{base}", ylabel=ylabel)
    if not thumbnail:
        ax.legend(fontsize=8)


def plot_airmass(ax, df: pd.DataFrame, thumbnail: bool) -> None:
    """Airmass is a single physical quantity independent of filter -- plotted as one
    series (matching VPhot's own display), not split/colored by filter."""
    if df.empty:
        return
    base = int(df["DATE"].min())
    g = df.sort_values("DATE")
    _draw(ax, g["DATE"] - base, g["value"], AIRMASS_COLOR, thumbnail)
    _style_axes(ax, thumbnail, xlabel=f"JD+{base}", ylabel="Airmass")
