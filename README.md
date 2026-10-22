# Photometry Workbench

A desktop app for photometry processed in the AAVSO's VPhot. Open a session's AAVSO
report, inspect your results, transform them (single comparison star or ensemble),
compare the check star and comps against the catalog before and after the transform,
and submit: save a report ready to upload to the AAVSO with WebObs. The reported
uncertainty is chosen following the AAVSO CHOICE course *Uncertainty about
Uncertainty* ([how](docs/user-guide.md#how-the-uncertainty-is-chosen)).

<br>

![The main window after a transform](docs/images/05-transformed.png)

<br>

What it does:

- **Inspect** a session: target, check star and every comparison star against the
  catalog, with potential issues flagged and the reason on hover. Light curves for the
  target, check and comps, plus VPhot's SNR, FWHM, sky and peak counts, charted
  much as VPhot shows them today.
- **Transform** with your Transform Generator (TG) coefficients. A single
  comparison star is transformed the same way TransformApplier does it. For an
  **ensemble**, the target is transformed against the ensemble's average comparison
  star, as VPhot reports it, and against each comparison star separately; how much
  those disagree gives the uncertainty.
- **Compare** the check star and comps against the catalog, before and after the
  transform.
- **Submit**: choose how to report the uncertainty with a few questions (combine
  frames or not, does the target vary), preview the rows, and save an AAVSO Extended
  Format report ready to upload to the AAVSO with WebObs.

## Download

Download the zip for your computer from the
[latest release](https://github.com/jeffrwatts/PhotometryWorkbench/releases/latest):

- **Mac** (Apple Silicon): `PhotometryWorkbench-<version>-macos.zip`. Unzip it and move
  Photometry Workbench to Applications.
- **Windows**: `PhotometryWorkbench-<version>-windows.zip`. Extract it (don't run it from
  inside the zip) and run `Photometry Workbench.exe` in the folder.

The downloads aren't signed yet, so the first time you open the app:

- **Mac:** macOS says it can't verify the app. Click Done, then open System Settings >
  Privacy & Security, scroll down and click **Open Anyway**.
- **Windows:** if "Windows protected your PC" appears, click **More info**, then
  **Run anyway**.

To update, download the new release and replace the old copy. Your settings are kept.

## Install with Python

On an Intel Mac or Linux, or if you'd rather run it from Python, install it with
[pipx](https://pipx.pypa.io), which keeps it in its own environment and puts the
`photometry-workbench` command on your path. You need Python 3.10 or newer. On a Mac
the built-in `python3` is too old (3.9); install a current one from
[python.org](https://www.python.org/downloads/) or with Homebrew (`brew install python`).

```
brew install pipx        # or, with python.org's Python: python3.13 -m pip install --user pipx
pipx ensurepath          # then open a new terminal window
pipx install git+https://github.com/jeffrwatts/PhotometryWorkbench
```

Then start it with:

```
photometry-workbench
```

To update to the latest version: `pipx reinstall photometry-workbench`.

The Mac and Windows downloads are built and tested automatically for each release.
Linux should work through pipx (it's Python and Qt) but hasn't been tried yet.

## Using it

The [user guide](docs/user-guide.md) walks through a real session from VPhot export
to the saved report, including which VPhot export options to choose and how the
uncertainty is chosen.

## How the ensemble transform works

[`notebooks/ensemble_transform_walkthrough.ipynb`](notebooks/ensemble_transform_walkthrough.ipynb)
walks through the method step by step on a real session (CY Aqr, 2026-09-05), using
the app's own code: the single-comp transform checked against TransformApplier, how
each comparison star's instrumental magnitude is recovered from VPhot's ensemble
report and General Report, and why the spread across comparison stars is the
uncertainty.

## Running from source

`python3` must be 3.10 or newer (`python3 --version`); otherwise use a versioned one,
such as `python3.13 -m venv .venv`.

```
git clone https://github.com/jeffrwatts/PhotometryWorkbench
cd PhotometryWorkbench
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
.venv/bin/photometry-workbench
```

To run the notebook, add Jupyter to the same environment and open it, still from the
`PhotometryWorkbench` folder:

```
.venv/bin/pip install -e ".[notebook]"
.venv/bin/jupyter notebook notebooks/ensemble_transform_walkthrough.ipynb
```

It opens in your browser; **Run › Run All Cells** runs the walkthrough.

## License

MIT; see [LICENSE](LICENSE). This is an independent project, not affiliated with or
endorsed by the AAVSO.
