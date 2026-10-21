# Photometry Workbench

A desktop app for VPhot photometry reports: inspect a session, apply the transform
(single comparison star or ensemble), compare the results before and after, and
build a report ready to submit to the AAVSO.

Full documentation is coming. For now:

```
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/photometry-workbench
```

## How the ensemble transform works

[`notebooks/ensemble_transform_walkthrough.ipynb`](notebooks/ensemble_transform_walkthrough.ipynb)
walks through the method step by step on a real session (CY Aqr, 2026-09-05), using
the app's own code: the single-comp transform checked against TransformApplier, how
each comparison star's instrumental magnitude is recovered from VPhot's ensemble report
and General Report, and why the per-comp spread is the uncertainty. To run it:
`pip install -e ".[notebook]"`, then open it with Jupyter from the `notebooks` folder.

MIT licensed; see [LICENSE](LICENSE).
