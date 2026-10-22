"""Draw the app icon: a night-sky tile with a pulsating star's light curve.

Writes icon.png (1024 px) into the package, where the app loads it as its window icon,
and from it icon.icns (macOS, via iconutil) and icon.ico (Windows) next to this script
for the builds; icon.icns also goes into the source checkout's .app. Run it from any
folder:

    python packaging/make_icon.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import FancyBboxPatch
from PIL import Image

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PACKAGE_PNG = ROOT / "src/photometry_workbench/resources/icon.png"
SOURCE_APP_ICNS = ROOT / "Photometry Workbench.app/Contents/Resources/icon.icns"
SIZE = 1024
# macOS icons leave a margin around the tile; 100 px of 1024 matches Apple's template.
MARGIN = 100 / SIZE
CORNER = 185 / SIZE


def _pulsation(phase: np.ndarray) -> np.ndarray:
    """Brightness 0..1 over one cycle: rise over the first quarter, decline over the rest,
    with the sharp corners rounded off by smoothing around the cycle."""
    grid = np.linspace(0, 1, 1000, endpoint=False)
    rise = 0.25
    raw = np.where(grid < rise, grid / rise, 1 - (grid - rise) / (1 - rise))
    kernel = np.exp(-0.5 * (np.arange(-60, 61) / 30) ** 2)
    padded = np.concatenate([raw[-60:], raw, raw[:60]])
    smooth = np.convolve(padded, kernel / kernel.sum(), mode="valid")
    smooth = (smooth - smooth.min()) / (smooth.max() - smooth.min())
    return np.interp(phase, grid, smooth, period=1.0)


def _draw(path: Path) -> None:
    fig = plt.figure(figsize=(SIZE / 100, SIZE / 100), dpi=100)
    fig.patch.set_alpha(0)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    side = 1 - 2 * MARGIN
    tile = FancyBboxPatch((MARGIN, MARGIN), side, side,
                          boxstyle=f"round,pad=0,rounding_size={CORNER}",
                          transform=ax.transAxes, facecolor="none", edgecolor="none")
    ax.add_patch(tile)

    # Night-sky gradient, clipped to the rounded tile.
    gradient = np.linspace(0, 1, 256).reshape(-1, 1)
    sky = matplotlib.colors.LinearSegmentedColormap.from_list("sky", ["#0b1026", "#24306b"])
    image = ax.imshow(gradient, cmap=sky, extent=(MARGIN, 1 - MARGIN, MARGIN, 1 - MARGIN),
                      origin="lower", aspect="auto", zorder=0)
    image.set_clip_path(tile)

    # A few faint background stars.
    rng = np.random.default_rng(7)
    xs = rng.uniform(MARGIN + 0.05, 1 - MARGIN - 0.05, 28)
    ys = rng.uniform(0.62, 1 - MARGIN - 0.05, 28)
    stars = ax.scatter(xs, ys, s=rng.uniform(4, 30, 28), color="white",
                       alpha=0.55, linewidths=0, zorder=1)
    stars.set_clip_path(tile)

    # The light curve: a pulsator's quick rise to a rounded maximum, then a slower
    # decline, almost two cycles, as dots the way a light curve is plotted.
    t = np.linspace(0, 1, 46)
    x = MARGIN + 0.08 + t * (side - 0.16)
    y = 0.22 + 0.36 * _pulsation((t * 1.75 + 0.35) % 1.0) + rng.normal(0, 0.006, t.size)
    ax.scatter(x, y, s=170, color="#ffd36b", edgecolors="#fff3cf", linewidths=2, zorder=3)

    fig.savefig(path, dpi=100, transparent=True)
    plt.close(fig)


def _icns(png: Path, out: Path) -> None:
    if shutil.which("iconutil") is None:
        print("iconutil not found (macOS only); skipping icon.icns")
        return
    base = Image.open(png)
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "icon.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            base.resize((size, size), Image.LANCZOS).save(iconset / f"icon_{size}x{size}.png")
            base.resize((size * 2, size * 2), Image.LANCZOS).save(iconset / f"icon_{size}x{size}@2x.png")
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out)], check=True)


def main() -> int:
    PACKAGE_PNG.parent.mkdir(parents=True, exist_ok=True)
    _draw(PACKAGE_PNG)
    Image.open(PACKAGE_PNG).save(
        HERE / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    icns = HERE / "icon.icns"
    _icns(PACKAGE_PNG, icns)
    if icns.exists():
        SOURCE_APP_ICNS.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(icns, SOURCE_APP_ICNS)
    print(f"Wrote {PACKAGE_PNG.relative_to(ROOT)}, packaging/icon.ico and packaging/icon.icns")
    return 0


if __name__ == "__main__":
    sys.exit(main())
