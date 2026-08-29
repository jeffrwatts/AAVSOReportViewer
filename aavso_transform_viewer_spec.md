# AAVSO Transform/Session Viewer — Spec (v1 draft)

Standalone desktop application, successor to `aavso_report_check.ipynb`. Not part of the
AstroPlannerData repo's own pipeline — this spec documents the design for a separate
project (own folder, own repo) that this repo's planning session produced. Intended
eventually as a contribution to AAVSO, since (per Jeff's AAVSO mentor) no existing AAVSO
tool judges transform quality this way, and this project has been generalized from a
before/after-only tool into a general single-session viewer with the before/after
comparison as an additive feature. See `CLAUDE.md`'s `aavso_report_check.ipynb` section
in this repo for the notebook this supersedes and the domain reasoning (noise floor,
trend thresholds, VSX tolerance) carried forward from it.

## Goals

- Read VPhot/AAVSO session exports (AAVSO Extended-format report, optionally its
  TA-transformed counterpart, optionally VPhot's General Export) and present the same
  kind of summary table + diagnostic charts VPhot's own "Time Series" web page shows —
  as a standalone tool an observer can run without VPhot access.
- No saved-project/session-persistence model for v1 — the user picks files at the start
  of a session; there's no "save state and resume later."
- Framework: **PySide6** (native desktop, LGPL license fits open distribution). Packaged
  via PyInstaller (or `briefcase`) into per-OS executables for AAVSO-facing distribution,
  in addition to a PyPI package for Python-literate users.

## Increment plan

- **Increment 1 (this spec's primary scope): single-report view.** Given one AAVSO
  report (transformed or untransformed, single-comparison or ensemble), render the
  summary table and diagnostic charts for that one session.
- **Increment 2 (future, not detailed here): before/after transform comparison.**
  Builds on two single-report views (untransformed + TA-transformed) side by side, per
  the logic already implemented in `aavso_report_check.ipynb` (Std vs. noise floor,
  Trend vs. airmass, VSX range tolerance, etc.). The startup dialog's "Transformed
  Report" field (see below) is wired up for this increment but inert until it's built.

## Startup inputs

A three-field dialog, all file pickers, with a light one-line description under each:

1. **AAVSO Report** *(required)* — "The AAVSO Extended-format report to analyze —
   transformed or untransformed."
2. **Transformed Report** *(optional)* — "If the report above is untransformed, add its
   TA-transformed counterpart to compare before vs. after the transform." Stored but
   unused until increment 2 exists.
3. **General Report** *(optional)* — "VPhot's General Export for this session — adds
   SNR, FWHM, Skyglow, Max ADU, and (for ensembles) individual comparison star curves."

A different AAVSO Report can be loaded later via a standard File → Open, replacing the
current view. The General Report can be attached at any time after launch via the
inline callouts described below — there's no need to supply it upfront.

## Data source mapping

| Element | Source | If General Report missing |
|---|---|---|
| Table: Targets row(s) (Avg/Min/Max/Std, per filter) | AAVSO report (`MAG` column) | n/a — always available |
| Table: Check stars row(s) (Avg/Min/Max/Std, per filter) | AAVSO report (`KMAG` column, embedded in each target observation row via `KNAME`) | n/a — always available |
| Table: Comparison stars row(s) | AAVSO report (`CNAME`), reference display only, stats always "-" | n/a — always available |
| Table: Target `Avg. SNR` | General Report (`Err`≈1.0857/`SNR`, or `SNR` directly if present) | Shows "*" (see callout below) |
| Table: Check/Comparison `Avg. SNR` | **Not derivable from this export format at all** — VPhot's own per-star SNR (visible on its web page) isn't present in the downloadable General Export, which carries only one Err/SNR/FWHM/Skyglow/Max ADU/I.M. column set, for whichever single star was selected when the export was generated | Always "-", independent of whether the General Report is loaded |
| Target/Check Light Curve | AAVSO report only (`MAG`, `KMAG`, `FILT`) | n/a — always available |
| Comp Stars Light Curve (ensemble sessions only) | General Report — each named comparison star has its own raw instrumental-mag column (e.g. `103`, `108`, `114`...) | Shows "*" (see callout below) |
| SNR, FWHM, Skyglow, Max ADU (single curve each) | General Report | Shows "*" (see callout below) |
| Airmass | AAVSO report's `AMASS` field (already used by the notebook's Trend calc); General Report also carries it but isn't required | n/a — always available from the AAVSO report |
| Tracking Error (CCD X/Y) | **Dropped from scope** — no corresponding data exists in the General Export format (confirmed against VPhot's export field-selection dialog: no X/Y tracking columns offered) | n/a |

**General Report schema note:** confirmed against a real ensemble export
(`YZ+Boo.txt`): `JD, Filter, Airmass, <target>, <check>, <comp1>, <comp2>, ..., <compN>,
Err, SNR, FWHM, Skyglow, Max ADU, I.M.` (`I.M.` = Instrumental Magnitude of the selected
target). The diagnostic columns (`Err`/`SNR`/`FWHM`/`Skyglow`/`Max ADU`/`I.M.`) are
**optional and user-configurable** in VPhot's own export dialog — a real file may be
missing any of them. **The parser must detect columns by header name, not fixed
position/count** — don't assume the full column set is always present.

## Missing-General-Report UI

Every element that needs the General Report (Target `Avg. SNR`, Comp Stars Light Curve,
SNR/FWHM/Skyglow/Max ADU charts) shows a subtle "*"-style indicator in place of the
real content when the General Report hasn't been loaded. That indicator is itself
clickable and opens the General Report file picker; once loaded, all such elements
populate together (single shared state, not loaded per-element).

## Chart interaction model

Every chart (light curve, comp-star curve, and each diagnostic chart) is a small
thumbnail on the main view — matching VPhot's own "click charts to view details"
pattern. Clicking a thumbnail opens a full detail view for that chart with additional
controls:

- **Target/Check Light Curve** → filter multi-select, plus a target/check/both toggle
- **Comp Stars Light Curve** → filter multi-select, plus a checkbox per comparison star
- **SNR / FWHM / Skyglow / Max ADU / Airmass** → filter multi-select only (these are
  single-series per the General Report's schema — no target/check split available)

Thumbnails themselves stay simple (VPhot-style role coloring — blue target, red check —
overlaid across filters with no additional per-filter encoding); the richer
legend/filtering lives only in the detail view.

## Layout

- Table at top: Targets / Check stars / Comparison stars sections, columns Filter /
  Average / Min / Max / Std / Avg. SNR
- Chart grid below, mirroring VPhot's arrangement:
  - Target/Check Light Curve (full width alone; paired side-by-side with Comp Stars
    Light Curve when the report is an ensemble)
  - SNR + Airmass, paired
  - FWHM + Max ADU, paired
  - Skyglow, alone (its former pairing slot, Tracking Error, is dropped)

## Open items (not yet resolved)

- **Ensemble vs. single-comparison detection** from the AAVSO report itself — likely
  via the `CNAME` field (e.g. a literal `ENSEMBLE` value), but not yet confirmed against
  a real ensemble AAVSO report (only a real ensemble *General* Report has been checked
  so far).
- **Session metadata header** (star name, date, filters present, transformed/
  untransformed badge) shown above the table — not yet designed.
- **New project name and repo location** — not yet decided.
- **Styling/theming** — deliberately deferred; QSS can be layered on top of the
  finished layout/widget structure independent of feature work.
