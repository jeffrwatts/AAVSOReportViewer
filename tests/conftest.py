import pytest


@pytest.fixture(autouse=True)
def no_live_vsx_fetch(monkeypatch):
    """VSX lookups hit a real network API. Session.build_summary_rows() now calls
    vsx_range() unconditionally for Targets, so every test that builds summary rows
    would otherwise make a live request -- stub it out by default so the suite stays
    fast and offline-safe. Tests that specifically exercise VSX behavior can override
    this with their own monkeypatch.setattr call (which takes precedence)."""
    monkeypatch.setattr("photometry_workbench.models.session.fetch_vsx_range", lambda star_name: None)
