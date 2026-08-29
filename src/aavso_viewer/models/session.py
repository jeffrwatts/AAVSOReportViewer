"""Session model: combines a parsed AAVSO report with an optional General Report,
and exposes the summary-table rows and chart data increment 1's UI needs.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from aavso_viewer.models.jd import jd_to_date
from aavso_viewer.parsing.aavso_report import AavsoReport
from aavso_viewer.parsing.chart_photometry import ChartMag, fetch_chart_photometry
from aavso_viewer.parsing.general_report import GeneralReport

# General Report <-> AAVSO report timestamps are computed independently and can differ
# by a few 1e-5 days even for the same exposure -- nearest-within-tolerance join is
# robust to that where exact-equality would silently drop rows. 0.0001d (~8.6s) is well
# under a typical per-filter cadence.
_JOIN_TOLERANCE_DAYS = 0.0001

SECTION_TARGETS = "Targets"
SECTION_CHECK = "Check stars"
SECTION_COMPARISON = "Comparison stars"


def _nan_to_none(value: float) -> float | None:
    return None if pd.isna(value) else float(value)


@dataclass
class SummaryRow:
    section: str
    star_label: str
    """Star identifier, e.g. 'CY Aqr' or '117' -- the catalog magnitude, when known
    (check/comparison stars via KREFMAG/CREFMAG), is exposed separately as catalog_mag
    rather than baked into this string, so the UI can format '117 (12.217)' itself."""
    filt: str
    average: float | None
    minimum: float | None
    maximum: float | None
    std: float | None
    avg_snr: float | None
    """None with avg_snr_available=True means 'not loaded yet' (render as a clickable
    '*'); None with avg_snr_available=False means 'not derivable at all' (render '-')."""
    avg_snr_available: bool
    catalog_mag: float | None = None
    """KREFMAG (check stars) or CREFMAG (comparison stars) -- None for Targets, where
    no catalog magnitude is known."""
    kreferr: float | None = None
    """Catalog uncertainty of catalog_mag -- the yardstick for whether Delta is real."""
    needs_general_report: bool = False
    """True when this row's blank numeric fields are blank only because no General
    Report is loaded (render '*', clickable) rather than genuinely unavailable
    (render '-')."""

    @property
    def delta(self) -> float | None:
        if self.average is None or self.catalog_mag is None:
            return None
        return self.average - self.catalog_mag


class Session:
    def __init__(self, aavso: AavsoReport, general: GeneralReport | None = None):
        self.aavso = aavso
        self.general: GeneralReport | None = None
        self._merged: pd.DataFrame | None = None
        self._chart_photometry: dict[str, dict[str, ChartMag]] | None = None
        self._chart_fetch_attempted = False
        if general is not None:
            self.attach_general_report(general)

    def attach_general_report(self, general: GeneralReport) -> None:
        self.general = general
        self._merged = None

    @property
    def has_general_report(self) -> bool:
        return self.general is not None

    @property
    def star_name(self) -> str:
        return self.aavso.star_name

    @property
    def filters(self) -> list[str]:
        return self.aavso.filters

    @property
    def is_ensemble(self) -> bool:
        return self.aavso.is_ensemble

    @property
    def is_transformed(self) -> bool:
        return self.aavso.is_transformed

    @property
    def session_date(self):
        dates = self.aavso.df["DATE"].dropna()
        if dates.empty:
            return None
        return jd_to_date(dates.min())

    @property
    def chart_id(self) -> str | None:
        df = self.aavso.df
        if "CHART" not in df.columns or df.empty:
            return None
        chart = df["CHART"].iloc[0]
        return str(chart) if chart else None

    def chart_photometry(self) -> dict[str, dict[str, ChartMag]] | None:
        """Lazily fetch and cache each named comparison star's catalog magnitude from
        AAVSO's chart API (see parsing.chart_photometry) for an ensemble session --
        returns None if unavailable (offline, no chart id, or the fetch failed) and
        never raises; only attempted once per Session instance."""
        if self._chart_fetch_attempted:
            return self._chart_photometry
        self._chart_fetch_attempted = True
        chart_id = self.chart_id
        if chart_id:
            try:
                self._chart_photometry = fetch_chart_photometry(chart_id)
            except Exception:  # noqa: BLE001 -- optional online enhancement, never fatal
                self._chart_photometry = None
        return self._chart_photometry

    # -- General Report join -------------------------------------------------

    def _merged_df(self) -> pd.DataFrame:
        """AAVSO report rows with the General Report's diagnostic columns joined on
        nearest JD, matched per filter."""
        if self._merged is not None:
            return self._merged
        df = self.aavso.df
        if self.general is None:
            self._merged = df
            return df

        gdf = self.general.df
        diag_cols = self.general.diagnostic_columns
        cols = ["JD", "Filter", *diag_cols]
        parts = []
        for filt, group in df.groupby("FILT", sort=False):
            gen_f = gdf.loc[gdf["Filter"] == filt, cols].sort_values("JD")
            merged = pd.merge_asof(
                group.sort_values("DATE"),
                gen_f,
                left_on="DATE",
                right_on="JD",
                direction="nearest",
                tolerance=_JOIN_TOLERANCE_DAYS,
            )
            parts.append(merged)
        self._merged = (
            pd.concat(parts).sort_index().drop(columns=["JD"], errors="ignore") if parts else df
        )
        return self._merged

    def _avg_snr_for_target(self, filt: str) -> float | None:
        if self.general is None:
            return None
        merged = self._merged_df()
        sub = merged.loc[merged["FILT"] == filt]
        if "SNR" in sub.columns and sub["SNR"].notna().any():
            return float(sub["SNR"].mean())
        if "Err" in sub.columns and sub["Err"].notna().any():
            return float((1.0857 / sub["Err"]).mean())
        return None

    # -- Summary table ---------------------------------------------------------

    def build_summary_rows(self) -> list[SummaryRow]:
        df = self.aavso.df
        rows: list[SummaryRow] = []

        for filt in self.filters:
            g = df.loc[df["FILT"] == filt, "MAG"]
            rows.append(
                SummaryRow(
                    SECTION_TARGETS,
                    self.star_name,
                    filt,
                    g.mean(),
                    g.min(),
                    g.max(),
                    g.std(),
                    self._avg_snr_for_target(filt),
                    avg_snr_available=True,
                )
            )

        for filt in self.filters:
            sub = df.loc[df["FILT"] == filt]
            g = sub["KEFF"]
            kname = str(sub["KNAME"].iloc[0]) if len(sub) else "?"
            krefmag = sub["KREFMAG"].iloc[0] if len(sub) else np.nan
            kreferr = sub["KREFERR"].iloc[0] if len(sub) else np.nan
            rows.append(
                SummaryRow(
                    SECTION_CHECK,
                    kname,
                    filt,
                    g.mean(),
                    g.min(),
                    g.max(),
                    g.std(),
                    None,
                    avg_snr_available=False,
                    catalog_mag=krefmag if pd.notna(krefmag) else None,
                    kreferr=kreferr if pd.notna(kreferr) else None,
                )
            )

        # Comparison-star stats are only derivable from the General Report's own
        # per-star standardized-magnitude columns -- but VPhot only actually populates
        # those for an ENSEMBLE export; a single-comparison-star export's sole comp
        # column is a literal 'na' placeholder in every row (it's the calibration
        # anchor, not something VPhot tracks per-frame), so loading a General Report
        # never unlocks real stats there. Only break the section out into one row per
        # named comparison star -- matching the Comp Stars Light Curve chart's source
        # data -- for ensemble sessions; single-comp sessions keep the single
        # reference-only row per filter, permanently "-".
        comp_names = (
            self.comp_star_names() if (self.is_ensemble and self.has_general_report) else []
        )
        if comp_names:
            # The AAVSO report's own NOTES carry only a single aggregate CREFMAG for
            # the whole ensemble, never a per-star breakdown -- so each named comp
            # star's catalog magnitude has to come from AAVSO's chart database instead
            # (see chart_photometry()), keyed by the same numeric label VPhot uses in
            # both the AAVSO report's CHART sequence and the General Report's column
            # names.
            chart_phot = self.chart_photometry() or {}
            gdf = self.general.df
            for star in comp_names:
                if star not in gdf.columns or gdf[star].notna().sum() == 0:
                    continue
                star_mags = chart_phot.get(star, {})
                for filt in self.filters:
                    g = gdf.loc[gdf["Filter"] == filt, star]
                    chart_mag = star_mags.get(filt)
                    rows.append(
                        SummaryRow(
                            SECTION_COMPARISON,
                            star,
                            filt,
                            _nan_to_none(g.mean()),
                            _nan_to_none(g.min()),
                            _nan_to_none(g.max()),
                            _nan_to_none(g.std()),
                            None,
                            False,
                            catalog_mag=chart_mag.mag if chart_mag else None,
                            kreferr=chart_mag.error if chart_mag else None,
                        )
                    )
        else:
            for filt in self.filters:
                sub = df.loc[df["FILT"] == filt]
                cname = str(sub["CNAME"].iloc[0]) if len(sub) else "?"
                crefmag = sub["CREFMAG"].iloc[0] if len(sub) else np.nan
                rows.append(
                    SummaryRow(
                        SECTION_COMPARISON,
                        cname,
                        filt,
                        None,
                        None,
                        None,
                        None,
                        None,
                        False,
                        catalog_mag=crefmag if pd.notna(crefmag) else None,
                        needs_general_report=self.is_ensemble and not self.has_general_report,
                    )
                )

        return rows

    # -- Chart data --------------------------------------------------------

    def target_check_light_curve(self, filters: list[str] | None = None) -> pd.DataFrame:
        """Long-form frame: DATE, FILT, role ('target'/'check'), mag."""
        df = self.aavso.df
        filters = filters or self.filters
        sub = df.loc[df["FILT"].isin(filters)]
        target = pd.DataFrame({"DATE": sub["DATE"], "FILT": sub["FILT"], "role": "target", "mag": sub["MAG"]})
        check = pd.DataFrame({"DATE": sub["DATE"], "FILT": sub["FILT"], "role": "check", "mag": sub["KEFF"]})
        return pd.concat([target, check], ignore_index=True)

    def comp_star_names(self) -> list[str]:
        """Named comparison-star columns from the General Report, for an ensemble
        session (i.e. every star column that isn't the target or the check star)."""
        if self.general is None:
            return []
        exclude = {self.star_name}
        knames = self.aavso.df["KNAME"].astype(str).unique()
        exclude.update(knames)
        return [c for c in self.general.star_columns if c not in exclude]

    def comp_stars_light_curve(
        self, filters: list[str] | None = None, stars: list[str] | None = None
    ) -> pd.DataFrame:
        """Long-form frame: JD, Filter, star, mag -- for ensemble sessions only,
        sourced from the General Report's per-comparison-star columns."""
        if self.general is None:
            return pd.DataFrame(columns=["JD", "Filter", "star", "mag"])
        stars = stars if stars is not None else self.comp_star_names()
        filters = filters or self.filters
        gdf = self.general.df
        sub = gdf.loc[gdf["Filter"].isin(filters)]
        parts = []
        for star in stars:
            if star not in sub.columns:
                continue
            parts.append(
                pd.DataFrame(
                    {"JD": sub["JD"], "Filter": sub["Filter"], "star": star, "mag": sub[star]}
                )
            )
        if not parts:
            return pd.DataFrame(columns=["JD", "Filter", "star", "mag"])
        return pd.concat(parts, ignore_index=True)

    def diagnostic_series(self, column: str, filters: list[str] | None = None) -> pd.DataFrame:
        """Long-form frame: JD, Filter, value -- for one of the General Report's
        single-series diagnostic columns (SNR/FWHM/Skyglow/'Max ADU'/'I.M.')."""
        if self.general is None or column not in self.general.df.columns:
            return pd.DataFrame(columns=["JD", "Filter", "value"])
        filters = filters or self.filters
        gdf = self.general.df
        sub = gdf.loc[gdf["Filter"].isin(filters)]
        return pd.DataFrame({"JD": sub["JD"], "Filter": sub["Filter"], "value": sub[column]})

    def airmass_series(self, filters: list[str] | None = None) -> pd.DataFrame:
        """Long-form frame: DATE, FILT, value -- always available from the AAVSO report."""
        df = self.aavso.df
        filters = filters or self.filters
        sub = df.loc[df["FILT"].isin(filters)]
        return pd.DataFrame({"DATE": sub["DATE"], "FILT": sub["FILT"], "value": sub["AMASS"]})
