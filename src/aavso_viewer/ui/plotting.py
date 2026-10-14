"""Shared matplotlib plotting helpers for thumbnails and chart detail dialogs."""

from __future__ import annotations

import datetime

import matplotlib.dates as mdates
import pandas as pd
from matplotlib.ticker import MaxNLocator

from aavso_viewer.models.jd import jd_to_datetime

ROLE_COLORS = {"target": "#1565c0", "check": "#c62828"}
# VPhot's own filter-color convention -- B/V/R map onto the intuitive blue/green/red.
FILTER_COLORS = {"B": "#1565c0", "V": "#2e7d32", "R": "#c62828"}
_FALLBACK_PALETTE = [
    "#8c564b", "#9467bd", "#ff7f0e", "#17becf", "#e377c2", "#bcbd22", "#7f7f7f",
]
AIRMASS_COLOR = "#1565c0"
# Matches the notebook's plot_before_after palette (tab:orange / tab:blue).
BEFORE_COLOR = "#ff7f0e"
AFTER_COLOR = "#1f77b4"
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
        # One line per filter: a single line through all of a star's frames would zig-zag
        # between its B and V magnitudes (an OSC frames them at the same JD).
        for i, (_, g) in enumerate(df.loc[df["star"] == star].groupby("Filter")):
            g = g.sort_values("JD")
            _draw(ax, g["JD"] - base, g["mag"], color_for(star, stars), thumbnail, label=star if i == 0 else None)
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


def _plot_before_after(ax, session, column: str, thumbnail: bool, filters: list[str] | None = None) -> None:
    filters = filters or session.filters
    df_before = session.aavso.df
    df_after = session.after.df if session.after is not None else None
    if df_before.empty:
        return
    base = int(df_before["DATE"].min())

    for i, filt in enumerate(filters):
        gb = df_before.loc[df_before["FILT"] == filt].sort_values("DATE")
        _draw(ax, gb["DATE"] - base, gb[column], BEFORE_COLOR, thumbnail, label="Before" if i == 0 else None)
        if df_after is not None:
            ga = df_after.loc[df_after["FILT"] == filt].sort_values("DATE")
            _draw(ax, ga["DATE"] - base, ga[column], AFTER_COLOR, thumbnail, label="After" if i == 0 else None)

    ax.invert_yaxis()
    _style_axes(ax, thumbnail, xlabel=f"JD+{base}", ylabel="Magnitude")


def plot_check_before_after(ax, session, thumbnail: bool, filters: list[str] | None = None) -> None:
    """Check star's magnitude, before vs. after the transform. A catalog-reference
    line is only drawn when a single filter is shown (each filter has its own
    catalog magnitude, so a combined multi-filter view has no single line to draw)."""
    _plot_before_after(ax, session, "KEFF", thumbnail, filters)

    filters = filters or session.filters
    if not thumbnail and len(filters) == 1:
        sub = session.aavso.df.loc[session.aavso.df["FILT"] == filters[0]]
        if len(sub):
            refmag = sub["KREFMAG"].iloc[0]
            if pd.notna(refmag):
                ax.axhline(refmag, color="gray", linestyle="--", linewidth=1, label=f"Catalog ref {refmag:.3f}")

    if not thumbnail:
        ax.legend(fontsize=8)


def plot_target_before_after(ax, session, thumbnail: bool, filters: list[str] | None = None) -> None:
    """Target's magnitude, before vs. after the transform -- a gut-check that the
    transform didn't distort the light curve's shape or amplitude."""
    _plot_before_after(ax, session, "MAG", thumbnail, filters)
    if not thumbnail:
        ax.legend(fontsize=8)


# Hawaii-Aleutian Standard Time -- fixed UTC-10:00, no DST observed.
_HST = datetime.timezone(datetime.timedelta(hours=-10))


def plot_share_light_curve(ax, df: pd.DataFrame, time_mode: str, filters: list[str]) -> None:
    """A light curve styled for sharing outside the app: larger points, a plain
    (unrotated-offset) time axis in the caller's chosen units, and one color-coded
    series per selected filter (a legend is shown only when more than one filter is
    plotted). df is long-form with DATE, FILT, MAG columns. time_mode is one of
    'jd', 'utc', 'hst'."""
    if df.empty:
        ax.text(
            0.5, 0.5, "No data for this selection", ha="center", va="center",
            transform=ax.transAxes, color="#999999", fontsize=11,
        )
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(False)
        return

    # matplotlib's date machinery round-trips every datetime through a UTC-based
    # numeric representation -- the tzinfo on each point only affects *which*
    # absolute instant it is, not how ticks are labeled. The label timezone has to
    # be passed explicitly to the locator/formatter, or both axes silently render
    # in UTC regardless of which mode was requested.
    tz = _HST if time_mode == "hst" else datetime.timezone.utc

    for filt in filters:
        sub = df.loc[df["FILT"] == filt]
        if sub.empty:
            continue
        color = filter_color(filt)
        label = filt if len(filters) > 1 else None
        if time_mode == "jd":
            ax.plot(sub["DATE"], sub["MAG"], marker="o", linestyle="none", markersize=5, color=color, alpha=0.85, label=label)
        else:
            times = [jd_to_datetime(jd) for jd in sub["DATE"]]
            ax.plot(times, sub["MAG"], marker="o", linestyle="none", markersize=5, color=color, alpha=0.85, label=label)

    if time_mode == "jd":
        # Julian Dates are large (~2.46e6) -- without this, matplotlib defaults to a
        # scientific-notation offset label that's confusing out of context.
        ax.ticklabel_format(useOffset=False, style="plain", axis="x")
        ax.set_xlabel("Julian Date (JD)", fontsize=10)
        for label in ax.get_xticklabels():
            label.set_rotation(30)
            label.set_ha("right")
    else:
        locator = mdates.AutoDateLocator(tz=tz)
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator, tz=tz))
        ax.set_xlabel("UTC" if time_mode == "utc" else "HST", fontsize=10)

    ax.invert_yaxis()
    ax.set_ylabel("Magnitude", fontsize=10)
    ax.tick_params(labelsize=9)
    ax.grid(True, linestyle="--", linewidth=0.4, alpha=0.3)
    ax.margins(y=0.25)
    if len(filters) > 1:
        ax.legend(fontsize=9)


def plot_airmass(ax, df: pd.DataFrame, thumbnail: bool) -> None:
    """Airmass is a single physical quantity independent of filter -- plotted as one
    series (matching VPhot's own display), not split/colored by filter."""
    if df.empty:
        return
    base = int(df["DATE"].min())
    g = df.sort_values("DATE")
    _draw(ax, g["DATE"] - base, g["value"], AIRMASS_COLOR, thumbnail)
    _style_axes(ax, thumbnail, xlabel=f"JD+{base}", ylabel="Airmass")
