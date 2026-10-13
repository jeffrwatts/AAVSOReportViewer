"""Session model: combines a parsed AAVSO report with an optional General Report,
and exposes the summary-table rows and chart data increment 1's UI needs.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from aavso_viewer.models.jd import jd_to_date
from aavso_viewer.parsing.aavso_report import AavsoReport
from aavso_viewer.parsing.chart_photometry import ChartMag, fetch_chart_auids, fetch_chart_photometry
from aavso_viewer.parsing.general_report import GeneralReport
from aavso_viewer.parsing.vsx import fetch_vsx_range
from aavso_viewer.transform.coefficients import TransformCoefficients, parse_report_header
from aavso_viewer.transform.engine import (
    CoefficientSensitivity,
    TransformError,
    TransformResult,
    coefficient_sensitivity,
    comp_estimates,
    ensemble_membership_problem,
    transform_report,
    untransformable_reason,
)

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
    # -- increment 2: before/after transform comparison --
    average_after: float | None = None
    std_after: float | None = None
    """Populated (check stars only) when a Transformed ('after') Report is loaded."""
    noise_floor: float | None = None
    """Check stars only: per-filter median of the General Report's per-observation
    Err -- the photon-noise floor Std is judged against."""
    trend: float | None = None
    trend_after: float | None = None
    """Check stars only: predicted magnitude swing across the session's airmass
    range (extinction proxy) -- a slope-times-range, not a magnitude itself."""
    vsx_bright: float | None = None
    vsx_faint: float | None = None
    """Targets (V filter only): VSX's known (brightest, faintest) magnitude, i.e.
    (smallest, largest) mag number -- VSX's own field naming is the reverse of that."""
    range_after: tuple[float, float] | None = None
    """Targets only: (min, max) MAG from the after report, for a quick before/after
    amplitude comparison -- None when there's no after report."""

    @property
    def delta(self) -> float | None:
        if self.average is None or self.catalog_mag is None:
            return None
        return self.average - self.catalog_mag

    @property
    def delta_after(self) -> float | None:
        if self.average_after is None or self.catalog_mag is None:
            return None
        return self.average_after - self.catalog_mag

    @property
    def vsx_excess(self) -> float | None:
        return self._vsx_excess(self.minimum, self.maximum)

    @property
    def vsx_excess_after(self) -> float | None:
        if self.range_after is None:
            return None
        return self._vsx_excess(*self.range_after)

    def _vsx_excess(self, min_val: float | None, max_val: float | None) -> float | None:
        """How far outside VSX's [bright, faint] range the observed [min, max] falls,
        in mag (0 or negative if fully inside)."""
        if self.vsx_bright is None or min_val is None or max_val is None:
            return None
        return max(max(self.vsx_bright - min_val, 0.0), max(max_val - self.vsx_faint, 0.0))


# Color indices are formed from adjacent bands in wavelength order, so a B/V/R
# session gets B-V and V-R (B-R would just be their sum, adding nothing new).
_BAND_ORDER = ["U", "B", "V", "R", "I"]


@dataclass
class ColorRow:
    """One color index (e.g. B-V) for one non-variable star: measured as the
    difference of the star's per-filter session averages, judged against the same
    difference of its catalog magnitudes. Any error common to both filters (zero
    point, comp-star offset) cancels here, so what's left is the color-dependent
    error that a transform exists to remove."""

    section: str
    star_label: str
    index: str
    """e.g. 'B-V'."""
    catalog: float | None
    catalog_err: float | None
    """Quadrature sum of the two bands' catalog errors."""
    measured: float | None
    measured_after: float | None = None

    @property
    def delta(self) -> float | None:
        if self.measured is None or self.catalog is None:
            return None
        return self.measured - self.catalog

    @property
    def delta_after(self) -> float | None:
        if self.measured_after is None or self.catalog is None:
            return None
        return self.measured_after - self.catalog


def _color_pairs(filters: list[str]) -> list[tuple[str, str]]:
    ordered = [b for b in _BAND_ORDER if b in filters]
    return list(zip(ordered, ordered[1:]))


def _difference(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else a - b


def _quadrature(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else float(np.hypot(a, b))


class Session:
    def __init__(self, aavso: AavsoReport, general: GeneralReport | None = None):
        self.aavso = aavso
        self.general: GeneralReport | None = None
        self.after: AavsoReport | None = None
        self.after_source: str | None = None
        """Where the after report came from, for display -- a file name, or the
        coefficient file when the transform was applied in-app."""
        self.after_coeffs: TransformCoefficients | None = None
        """The coefficients behind `after` when it was transformed in-app (None for a
        loaded file) -- needed to transform per-comp estimates the same way."""
        self._merged: pd.DataFrame | None = None
        self._chart_photometry: dict[str, dict[str, ChartMag]] | None = None
        self._chart_fetch_attempted = False
        self._vsx_range: tuple[float, float] | None = None
        self._vsx_fetch_attempted = False
        if general is not None:
            self.attach_general_report(general)

    def attach_general_report(self, general: GeneralReport) -> None:
        self.general = general
        self._merged = None

    def attach_after_report(self, after: AavsoReport, source: str | None = None) -> None:
        """The TA-transformed counterpart of this session's (untransformed) report --
        matched by filter only, not star name, since a TransformApplier report renames
        stars from local designations (e.g. '117') to AAVSO AUIDs for the same
        physical star."""
        self.after = after
        self.after_source = source
        self.after_coeffs = None

    def apply_transform(self, coeffs: TransformCoefficients, lookup_auids: bool = True) -> TransformResult:
        """Transform this session's report in-app and attach the result as the after
        report. Raises transform.engine.TransformError if it can't be transformed."""
        auids = None
        if lookup_auids and self.chart_id:
            try:
                auids = fetch_chart_auids(self.chart_id)
            except Exception:  # noqa: BLE001 -- optional online enhancement; labels are valid too
                auids = None
        members = None
        if self.is_ensemble and self.general is not None:
            members = self.comp_star_names()
            problem = ensemble_membership_problem(self.general.df, members, self.filters)
            if problem:
                raise TransformError(problem)
        result = transform_report(self.aavso, coeffs, auids, ensemble_members=members)
        self.attach_after_report(result.report, source=f"transformed in app with {coeffs.source}")
        self.after_coeffs = coeffs
        return result

    def clear_transform(self) -> None:
        self.after = None
        self.after_source = None
        self.after_coeffs = None

    def comp_spread(self, transformed: bool) -> pd.DataFrame:
        """Per frame of an ensemble session: the target estimated against each comp
        separately (n, mean, std -- see transform.engine.comp_estimates), transformed
        the same way as `after` when `transformed`. Raises ValueError explaining what's
        missing -- the General Report, or the comps' catalog magnitudes (online)."""
        if not self.is_ensemble:
            raise ValueError("Only an ensemble session has a spread across comparison stars.")
        if self.general is None:
            raise ValueError(
                "This needs VPhot's General Report for the session (File → Open General Report…) -- "
                "only it has each comparison star's magnitude per frame."
            )
        if self._chart_photometry is None:
            self._chart_fetch_attempted = False  # retry: the last attempt may have been a transient failure
        catalog = self.chart_photometry()
        if not catalog:
            raise ValueError(
                "Couldn't get the comparison stars' catalog magnitudes from AAVSO's chart service "
                f"(chart {self.chart_id or 'unknown'}) -- check the network connection and try again."
            )
        coeffs = None
        if transformed:
            if self.after_coeffs is None:
                raise ValueError("The transformed report wasn't produced in this app, so its per-comp spread can't be computed.")
            coeffs = self.after_coeffs
        return comp_estimates(self.aavso, self.general.df, self.comp_star_names(), catalog, coeffs)

    def coefficient_sensitivity(self) -> dict[str, CoefficientSensitivity]:
        """Per filter, how much the after report's target magnitudes depend on the
        transform coefficients' errors (transform.engine.coefficient_sensitivity).
        Uses the in-app coefficients, or those a TransformApplier report echoes in its
        header. Raises ValueError saying why it can't be computed."""
        if self.after is None:
            raise ValueError("No transformed report is loaded.")
        reason = untransformable_reason(self.aavso)
        if reason:
            raise ValueError(reason)
        coeffs = self.after_coeffs
        if coeffs is None:
            try:
                coeffs = parse_report_header(self.after.source_path)
            except (TypeError, ValueError, OSError):
                raise ValueError("The transformed report doesn't list the coefficients it was made with.") from None
        result = coefficient_sensitivity(self.aavso, coeffs)
        if not result:
            raise ValueError(f"{coeffs.source or 'The coefficients'} has no coefficient errors.")
        return result

    @property
    def has_general_report(self) -> bool:
        return self.general is not None

    @property
    def has_after_report(self) -> bool:
        return self.after is not None

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

    def vsx_range(self) -> tuple[float, float] | None:
        """Lazily fetch and cache the target's known (bright, faint) V-band amplitude
        from AAVSO's VSX database (see parsing.vsx) -- returns None if unavailable
        (offline, no V-band amplitude on record, or the fetch failed) and never
        raises; only attempted once per Session instance."""
        if self._vsx_fetch_attempted:
            return self._vsx_range
        self._vsx_fetch_attempted = True
        try:
            self._vsx_range = fetch_vsx_range(self.star_name)
        except Exception:  # noqa: BLE001 -- optional online enhancement, never fatal
            self._vsx_range = None
        return self._vsx_range

    @staticmethod
    def _noise_floor(merged_df: pd.DataFrame, filt: str) -> float | None:
        """Photon-noise floor: per-filter median of the General Report's
        per-observation Err, already joined onto the AAVSO report's rows."""
        if "Err" not in merged_df.columns:
            return None
        sub = merged_df.loc[merged_df["FILT"] == filt, "Err"]
        return _nan_to_none(sub.median()) if len(sub) else None

    @staticmethod
    def _trend(df: pd.DataFrame, filt: str, column: str) -> float | None:
        """Predicted magnitude swing across the session's airmass range (extinction
        proxy): the slope of `column` vs. AMASS, times the airmass range actually
        observed in this filter. AMASS is optional in the AAVSO Extended Format --
        None here (rather than a KeyError) when a report omits it entirely."""
        if "AMASS" not in df.columns:
            return None
        sub = df.loc[df["FILT"] == filt, ["AMASS", column]].dropna()
        if len(sub) < 2 or sub["AMASS"].nunique() < 2:
            return None
        slope, _ = np.polyfit(sub["AMASS"], sub[column], 1)
        return float(slope * (sub["AMASS"].max() - sub["AMASS"].min()))

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

        # VSX only publishes one catalog band's amplitude (V) -- only V rows are
        # judged against it; B/R rows get no vsx_bright/vsx_faint, hence no coloring.
        vsx = self.vsx_range()
        for filt in self.filters:
            g = df.loc[df["FILT"] == filt, "MAG"]
            range_after = None
            if self.after is not None:
                ag = self.after.df.loc[self.after.df["FILT"] == filt, "MAG"]
                if len(ag):
                    range_after = (float(ag.min()), float(ag.max()))
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
                    vsx_bright=vsx[0] if vsx and filt == "V" else None,
                    vsx_faint=vsx[1] if vsx and filt == "V" else None,
                    range_after=range_after,
                )
            )

        merged = self._merged_df()
        for filt in self.filters:
            sub = df.loc[df["FILT"] == filt]
            g = sub["KEFF"]
            kname = str(sub["KNAME"].iloc[0]) if len(sub) else "?"
            krefmag = sub["KREFMAG"].iloc[0] if len(sub) else np.nan
            kreferr = sub["KREFERR"].iloc[0] if len(sub) else np.nan

            average_after = std_after = trend_after = None
            if self.after is not None:
                asub = self.after.df.loc[self.after.df["FILT"] == filt]
                ag = asub["KEFF"]
                if len(ag):
                    average_after = _nan_to_none(ag.mean())
                    std_after = _nan_to_none(ag.std())
                trend_after = self._trend(self.after.df, filt, "KEFF")

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
                    noise_floor=self._noise_floor(merged, filt),
                    trend=self._trend(df, filt, "KEFF"),
                    average_after=average_after,
                    std_after=std_after,
                    trend_after=trend_after,
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

    def build_color_rows(self, summary_rows: list[SummaryRow] | None = None) -> list[ColorRow]:
        """Color indices for check and comparison stars, derived from the summary
        table's per-filter rows (pass them in to avoid rebuilding). Targets are
        skipped -- a variable's color changes through the session and has no catalog
        value to judge against -- as are stars with no measured averages at all
        (single-comparison-star sessions' comp star)."""
        if summary_rows is None:
            summary_rows = self.build_summary_rows()
        by_star: dict[tuple[str, str], dict[str, SummaryRow]] = {}
        for row in summary_rows:
            if row.section == SECTION_TARGETS:
                continue
            by_star.setdefault((row.section, row.star_label), {})[row.filt] = row

        color_rows: list[ColorRow] = []
        for (section, star), filt_rows in by_star.items():
            for blue, red in _color_pairs(list(filt_rows)):
                b, r = filt_rows[blue], filt_rows[red]
                measured = _difference(b.average, r.average)
                measured_after = _difference(b.average_after, r.average_after)
                if measured is None and measured_after is None:
                    continue
                color_rows.append(
                    ColorRow(
                        section,
                        star,
                        f"{blue}-{red}",
                        catalog=_difference(b.catalog_mag, r.catalog_mag),
                        catalog_err=_quadrature(b.kreferr, r.kreferr),
                        measured=measured,
                        measured_after=measured_after,
                    )
                )
        return color_rows

    # -- Chart data --------------------------------------------------------

    def target_check_light_curve(self, filters: list[str] | None = None) -> pd.DataFrame:
        """Long-form frame: DATE, FILT, role ('target'/'check'), mag."""
        df = self.aavso.df
        filters = filters or self.filters
        sub = df.loc[df["FILT"].isin(filters)]
        target = pd.DataFrame({"DATE": sub["DATE"], "FILT": sub["FILT"], "role": "target", "mag": sub["MAG"]})
        check = pd.DataFrame({"DATE": sub["DATE"], "FILT": sub["FILT"], "role": "check", "mag": sub["KEFF"]})
        return pd.concat([target, check], ignore_index=True)

    def target_light_curve(self, filters: list[str], use_after: bool = False) -> pd.DataFrame:
        """Long-form frame: DATE (JD), FILT, MAG -- the target's own magnitude across
        one or more filters, from the as-reported report, or the transformed report
        when use_after is True and one is attached (falls back to as-reported otherwise)."""
        report = self.after if (use_after and self.after is not None) else self.aavso
        sub = report.df.loc[report.df["FILT"].isin(filters), ["DATE", "FILT", "MAG"]].dropna()
        return sub.sort_values("DATE").reset_index(drop=True)

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
        """Long-form frame: DATE, FILT, value -- from the AAVSO report's AMASS column,
        which is optional in the AAVSO Extended Format and so may be entirely absent."""
        df = self.aavso.df
        if "AMASS" not in df.columns:
            return pd.DataFrame(columns=["DATE", "FILT", "value"])
        filters = filters or self.filters
        sub = df.loc[df["FILT"].isin(filters)]
        return pd.DataFrame({"DATE": sub["DATE"], "FILT": sub["FILT"], "value": sub["AMASS"]})
