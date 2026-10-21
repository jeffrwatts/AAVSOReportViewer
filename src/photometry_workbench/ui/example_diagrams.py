"""Small illustrative diagrams for the submission wizard's choices. They use made-up
example data, not the session's, so they look the same every time and show only the
idea: several frames averaged into one point or kept as a light curve, and a target
that holds steady over the frames or changes during them."""

from __future__ import annotations

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

_POINT = "#1f77b4"
_FRAME = "#9e9e9e"


def _canvas(draw, height_px: int = 130) -> FigureCanvasQTAgg:
    figure = Figure(figsize=(3.2, height_px / 100), dpi=100, facecolor="none")
    canvas = FigureCanvasQTAgg(figure)
    canvas.setStyleSheet("background: transparent;")
    canvas.setFixedHeight(height_px)
    ax = figure.add_axes([0.04, 0.2, 0.92, 0.75])
    ax.set_facecolor("none")
    draw(ax)
    return canvas


def _draw_combine(ax) -> None:
    rng = np.random.default_rng(3)
    x = np.arange(5)
    y = rng.normal(0, 0.03, 5)
    ax.errorbar(x, y, yerr=0.025, fmt="o", color=_FRAME, ms=6, capsize=2, lw=1)
    ax.hlines(y.mean(), -0.3, 4.3, colors=_FRAME, linestyles="dashed", lw=1)
    ax.annotate("", xy=(6.1, y.mean()), xytext=(4.7, y.mean()),
                arrowprops=dict(arrowstyle="->", color="#555555", lw=1.5))
    ax.errorbar([6.9], [y.mean()], yerr=y.std(ddof=1), fmt="o", color=_POINT, ms=10, capsize=4, lw=1.5)
    ax.text(2, -0.16, "5 frames", ha="center", va="top", fontsize=9, color="#555555")
    ax.text(6.9, -0.16, "1 point", ha="center", va="top", fontsize=9, color="#555555")
    ax.set_xlim(-0.8, 7.8)
    ax.set_ylim(-0.2, 0.15)
    ax.axis("off")


def _draw_light_curve(ax) -> None:
    ax.set_position([0.1, 0.18, 0.86, 0.78])  # room for the axis labels
    rng = np.random.default_rng(5)
    t = np.linspace(0, 2.2, 48)
    phase = t % 1.0
    # A pulsator's shape: a quick rise to maximum, then a slower decline.
    brightness = np.where(phase < 0.2, phase / 0.2, 1 - (phase - 0.2) / 0.8) + rng.normal(0, 0.03, t.size)
    ax.plot(t, brightness, "o", color=_POINT, ms=4)
    ax.set_xticks([])
    ax.set_yticks([])
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.set_xlabel("time", fontsize=9, color="#555555", labelpad=2)
    ax.set_ylabel("brightness", fontsize=9, color="#555555", labelpad=2)
    ax.set_ylim(-0.25, 1.2)


def _labeled_frames(ax, y, color, label, label_y) -> None:
    x = np.arange(len(y))
    ax.plot(x, y, "o", color=color, ms=7)
    ax.text(len(y) - 0.4, label_y, label, ha="left", va="center", fontsize=9, color="#555555")


def _draw_steady(ax) -> None:
    rng = np.random.default_rng(3)
    y = rng.normal(0, 0.03, 5)
    ax.hlines(y.mean(), -0.3, 4.3, colors=_POINT, linestyles="dashed", lw=1)
    _labeled_frames(ax, y, _POINT, "target", y.mean())
    ax.annotate("", xy=(-0.6, y.max() + 0.01), xytext=(-0.6, y.min() - 0.01),
                arrowprops=dict(arrowstyle="<->", color="#555555", lw=1))
    ax.text(-0.75, y.mean(), "scatter", ha="right", va="center", fontsize=9, color="#555555")
    ax.set_xlim(-2.6, 6.2)
    ax.set_ylim(-0.12, 0.1)
    ax.axis("off")


def _draw_varies(ax) -> None:
    rng = np.random.default_rng(4)
    target = np.linspace(-0.02, 0.16, 5) + rng.normal(0, 0.01, 5)
    check = -0.17 + rng.normal(0, 0.012, 5)
    _labeled_frames(ax, target, _POINT, "target", target[-1])
    _labeled_frames(ax, check, _FRAME, "check star", check.mean())
    ax.set_xlim(-2.6, 6.2)
    ax.set_ylim(-0.24, 0.22)
    ax.axis("off")


def steady_diagram() -> FigureCanvasQTAgg:
    return _canvas(_draw_steady)


def varies_diagram() -> FigureCanvasQTAgg:
    return _canvas(_draw_varies)


def combine_diagram() -> FigureCanvasQTAgg:
    return _canvas(_draw_combine)


def light_curve_diagram() -> FigureCanvasQTAgg:
    return _canvas(_draw_light_curve)
