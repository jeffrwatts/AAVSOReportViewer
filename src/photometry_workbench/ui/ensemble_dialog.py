"""Ensemble Comps: which comparison stars were measured in which frames, and how well
they agree with each other -- the two things an ensemble transform depends on."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from photometry_workbench.models.session import Session
from photometry_workbench.ui import coloring

# Missing-frame JDs listed in a membership cell's tooltip before it's cut short.
_MAX_LISTED_FRAMES = 15


def _table(columns: list[str]) -> QTableWidget:
    table = QTableWidget(0, len(columns))
    table.setHorizontalHeaderLabels(columns)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QTableWidget.NoEditTriggers)
    table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    return table


def _fit_height(table: QTableWidget) -> None:
    table.resizeRowsToContents()
    height = table.horizontalHeader().height() + 2 * table.frameWidth()
    table.setFixedHeight(height + sum(table.rowHeight(r) for r in range(table.rowCount())))


def _cell(table: QTableWidget, r: int, c: int, text: str, color=None, tooltip: str | None = None) -> None:
    item = QTableWidgetItem(text)
    item.setTextAlignment(Qt.AlignCenter)
    if color is not None:
        item.setForeground(color)
    if tooltip:
        item.setToolTip(tooltip)
    table.setItem(r, c, item)


def _caption(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("color: gray; font-size: 11px; padding: 2px 0 4px 0;")
    return label


def _heading(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet("font-size: 13px; font-weight: 600; padding: 8px 0 4px 0;")
    return label


class EnsembleCompsDialog(QDialog):
    def __init__(self, session: Session, parent=None):
        super().__init__(parent)
        self.session = session
        self.setWindowTitle(f"{session.star_name} — Ensemble Comps")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        filters = session.filters

        layout.addWidget(_heading("Membership: frames each comparison star was measured in"))
        self.membership_table = _table(["Comp", "Catalog " + " / ".join(filters), *filters])
        layout.addWidget(self.membership_table)
        layout.addWidget(_caption(
            "The ensemble transform treats the comps as one average star, so that average has to be "
            "the same stars in every filter. All green means every comp was measured in every frame."
        ))

        layout.addWidget(_heading("Measured − catalog (median over frames)"))
        self.offsets_table = _table(["Comp"])
        layout.addWidget(self.offsets_table)
        layout.addWidget(_caption(
            "How far each comp reads from its catalog value, measured against the other comps. Green is "
            "within that comp's catalog error. A comp that's far off may have a bad catalog value, be "
            "variable, or be blended with a neighbor, and is worth dropping. "
            + ("Columns marked (After) are after the transform, which removes the part of each offset that "
            "comes from a comp's color differing from the others'." if session.after_coeffs is not None else "These are before the transform. Apply it to add an (After) column per filter, which removes "
            "the part of each offset that comes from a comp's color differing from the others'.")
        ))
        self.problem = QLabel()
        self.problem.setWordWrap(True)
        self.problem.setStyleSheet(f"color: {coloring.FLAG.name()};")
        layout.addWidget(self.problem)
        layout.addStretch()

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            catalog = self._fill_offsets()
            self._fill_membership(catalog)
        finally:
            QApplication.restoreOverrideCursor()

    def _fill_membership(self, catalog) -> None:
        filters = self.session.filters
        membership = self.session.comp_membership()
        table = self.membership_table
        table.setRowCount(len(membership))
        for r, (comp, by_filter) in enumerate(membership.items()):
            mags = (catalog or {}).get(comp, {})
            _cell(table, r, 0, comp)
            _cell(table, r, 1, " / ".join(f"{mags[f].mag:.3f}" if f in mags else "–" for f in filters))
            for c, filt in enumerate(filters, start=2):
                m = by_filter[filt]
                if catalog is not None and filt not in mags:
                    color, tip = coloring.FLAG, f"No catalog {filt} magnitude, so it can't be used in a transform."
                elif m.measured == m.frames:
                    color, tip = coloring.GOOD, f"Measured in every {filt} frame."
                elif m.measured == 0:
                    color, tip = coloring.FLAG, f"Not measured in any {filt} frame."
                else:
                    listed = ", ".join(f"{jd:.6f}" for jd in m.missing[:_MAX_LISTED_FRAMES])
                    more = f", and {len(m.missing) - _MAX_LISTED_FRAMES} more" if len(m.missing) > _MAX_LISTED_FRAMES else ""
                    color = coloring.BORDERLINE
                    tip = f"Missing from {len(m.missing)} {filt} frame(s), so their ensemble is a different set of stars: JD {listed}{more}"
                _cell(table, r, c, f"{m.measured}/{m.frames}", color, tip)
        _fit_height(table)

    def _fill_offsets(self):
        """Fills the offsets table; returns the chart catalog, or None if unavailable."""
        try:
            before = self.session.comp_offsets(transformed=False)
        except ValueError as exc:
            self.problem.setText(str(exc))
            self.offsets_table.hide()
            return None
        after = None
        if self.session.after_coeffs is not None:
            after = self.session.comp_offsets(transformed=True)
        catalog = self.session.chart_photometry()

        filters = self.session.filters
        columns = [(filt, before, f"{filt}") for filt in filters]
        if after is not None:
            columns = [col for filt in filters for col in ((filt, before, filt), (filt, after, f"{filt} (After)"))]
        table = self.offsets_table
        table.setColumnCount(1 + len(columns))
        table.setHorizontalHeaderLabels(["Comp", *(label for _, _, label in columns)])
        table.setRowCount(len(before.index))
        for r, comp in enumerate(before.index):
            _cell(table, r, 0, comp)
            for c, (filt, offsets, _) in enumerate(columns, start=1):
                value = offsets.at[comp, filt] if comp in offsets.index else float("nan")
                if value != value:
                    _cell(table, r, c, "-")
                    continue
                ref = catalog.get(comp, {}).get(filt)
                error = ref.error if ref is not None else None
                # Same rule as the main table's Average deltas: within the catalog error is good.
                tip = coloring.ratio_tip(value, error) or (
                    f"The chart lists no {filt} error for {comp}, so there's nothing to judge this offset against."
                )
                _cell(table, r, c, f"{value:+.3f}", coloring.ratio_color(value, error), tip)
        _fit_height(table)
        return catalog
