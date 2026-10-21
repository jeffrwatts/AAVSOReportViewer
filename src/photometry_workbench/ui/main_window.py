from __future__ import annotations

from pathlib import Path


from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from photometry_workbench import APP_NAME
from photometry_workbench.models.session import (
    SECTION_CHECK,
    SECTION_COMPARISON,
    SECTION_TARGETS,
    Session,
)
from photometry_workbench.parsing.aavso_report import parse_aavso_report
from photometry_workbench.parsing.general_report import parse_general_report
from photometry_workbench.transform.engine import untransformable_reason
from photometry_workbench.ui import coloring, plotting
from photometry_workbench.ui.chart_detail_dialog import (
    CheckBeforeAfterDetailDialog,
    CompStarsDetailDialog,
    DiagnosticDetailDialog,
    LightCurveDetailDialog,
    TargetBeforeAfterDetailDialog,
)
from photometry_workbench.ui.chart_thumbnail import ChartThumbnail
from photometry_workbench.ui.share_plot_dialog import SharePlotDialog
from photometry_workbench.ui.startup_dialog import StartupDialog
from photometry_workbench.ui.step_strip import CURRENT, DONE, UPCOMING, StepState, StepStrip
from photometry_workbench.ui.submission_wizard import SubmissionWizard
from photometry_workbench.ui.transform_dialog import TransformDialog

# Column order. The "(After)" columns are hidden entirely (not just blank) unless the
# transform has been applied, to keep the table lean.
TABLE_COLUMNS = [
    "Star", "Filter", "Average", "Average (After)", "Range", "Range (After)",
    "Std", "Std (After)", "KREFERR", "VSX Range", "Avg. SNR",
]
_AFTER_ONLY_COLUMNS = ["Average (After)", "Range (After)", "Std (After)"]

# Noise Floor and Trend are still computed (Session.build_summary_rows) and still
# drive Std's coloring, but they're not shown as their own
# columns, to keep the table lean.

_DIAGNOSTIC_SPECS = [
    # (General Report column, thumbnail title, ylabel)
    ("SNR", "SNR", "SNR"),
    ("FWHM", "FWHM", "FWHM (px)"),
    ("Max ADU", "Max ADU", "Max ADU"),
    ("Skyglow", "Skyglow", "Skyglow"),
]


class _WheelForwarder(QObject):
    """Matplotlib canvases (and the table's viewport) accept wheel events for their
    own purposes and never let them bubble up, which would otherwise leave the page's
    outer QScrollArea unreachable by wheel/trackpad whenever the cursor is over a
    chart or the table. Installed as an event filter, this applies the wheel delta
    straight to the scroll area's scrollbar instead of trying to redeliver the event
    (redelivery carries the original widget's local coordinates, which QScrollArea
    doesn't interpret correctly when sent directly)."""

    def __init__(self, scroll_area: QScrollArea, parent=None):
        super().__init__(parent)
        self._scroll_area = scroll_area

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Wheel:
            bar = self._scroll_area.verticalScrollBar()
            bar.setValue(bar.value() - event.angleDelta().y())
            return True
        return False


class _FitTable(QTableWidget):
    """A table whose columns are each at least as wide as their content, with any
    spare width shared equally -- unlike QHeaderView.Stretch, which gives every
    column the same width regardless of content and so wraps wide cells like
    '12.280 (+0.063)' onto two lines while narrow ones ('V') sit half empty. Its
    minimum width is the content total, so a too-narrow window scrolls horizontally
    (via the page's QScrollArea) instead of wrapping."""

    def fit_columns(self) -> None:
        header = self.horizontalHeader()
        cols = [c for c in range(self.columnCount()) if not self.isColumnHidden(c)]
        if not cols:
            return
        padding = 16
        widths = {
            c: max(self.sizeHintForColumn(c), header.sectionSizeHint(c)) + padding for c in cols
        }
        self.setMinimumWidth(sum(widths.values()) + 2 * self.frameWidth())
        spare = max(self.viewport().width() - sum(widths.values()), 0)
        extra, remainder = divmod(spare, len(cols))
        for i, c in enumerate(cols):
            header.resizeSection(c, widths[c] + extra + (1 if i < remainder else 0))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_columns()


def _outside(delta: float | None, catalog_err: float | None) -> bool:
    return delta is not None and bool(catalog_err) and abs(delta) > catalog_err


def _mark(item: QTableWidgetItem, color, tip: str | None) -> None:
    """Color a cell's text and give the reason as its tooltip (coloring.*_tip)."""
    if color is not None:
        item.setForeground(color)
    if tip:
        item.setToolTip(tip)


class MainWindow(QMainWindow):
    def __init__(self, session: Session):
        super().__init__()
        self.session = session
        self._saved_path: Path | None = None

        self.setWindowTitle(APP_NAME)

        self._build_menu()

        central = QWidget()
        layout = QVBoxLayout(central)

        self.header_label = QLabel()
        self.header_label.setStyleSheet("font-size: 13px; font-weight: 600; padding: 4px;")
        layout.addWidget(self.header_label)

        self.table = self._make_table(TABLE_COLUMNS)
        self.table.cellClicked.connect(self._on_table_cell_clicked)
        layout.addWidget(self.table)


        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(central)
        self._scroll_area = scroll_area

        # The step strip stays put above the scrolling tables and charts.
        self.step_strip = StepStrip()
        outer = QWidget()
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)
        outer_layout.addWidget(self.step_strip)
        outer_layout.addWidget(scroll_area, 1)
        self.setCentralWidget(outer)

        self._wheel_forwarder = _WheelForwarder(scroll_area, self)
        for table in (self.table,):
            table.viewport().installEventFilter(self._wheel_forwarder)
            table.installEventFilter(self._wheel_forwarder)

        self._set_initial_size()

        self.chart_grid = QGridLayout()
        self.chart_grid.setSpacing(8)
        layout.addLayout(self.chart_grid)

        self.thumbnails: dict[str, ChartThumbnail] = {}

        self._refresh_all()

    @staticmethod
    def _make_table(columns: list[str]) -> _FitTable:
        table = _FitTable()
        table.setColumnCount(len(columns))
        table.setHorizontalHeaderLabels(columns)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Fixed)
        table.setWordWrap(False)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        # Tables always render every row at full height (no internal scrollbar of
        # their own) -- the outer QScrollArea is what scrolls when the window is
        # smaller than the tables + chart grid combined, so resizing the window is left
        # entirely up to the user instead of the app fighting it on every refresh.
        table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        return table

    # -- menu -----------------------------------------------------------------

    def _build_menu(self) -> None:
        menu = self.menuBar().addMenu("&File")

        open_set_action = menu.addAction("Open Report Set…")
        open_set_action.triggered.connect(self._open_report_set)

        menu.addSeparator()
        open_action = menu.addAction("Open AAVSO Report…")
        open_action.triggered.connect(self._open_aavso_report)

        open_general_action = menu.addAction("Open General Report…")
        open_general_action.triggered.connect(self._prompt_for_general_report)

        menu.addSeparator()
        exit_action = menu.addAction("Exit")
        exit_action.triggered.connect(self.close)

        transform_menu = self.menuBar().addMenu("&Transform")
        apply_action = transform_menu.addAction("Apply Transform…")
        apply_action.triggered.connect(self._apply_transform)
        self._clear_transform_action = transform_menu.addAction("Clear Transform")
        self._clear_transform_action.triggered.connect(self._clear_transform)
        transform_menu.aboutToShow.connect(self._update_transform_menu)

        report_menu = self.menuBar().addMenu("&Report")
        submit_action = report_menu.addAction("Create Submission Report…")
        submit_action.triggered.connect(self._create_submission)

        chart_menu = self.menuBar().addMenu("&Chart")
        share_plot_action = chart_menu.addAction("Share Plot…")
        share_plot_action.triggered.connect(self._open_share_plot)

    def _open_report_set(self) -> None:
        """Load a whole new session (AAVSO Report + optional General Report) via the same dialog shown at startup, without needing to restart the app."""
        dialog = StartupDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            report = parse_aavso_report(dialog.aavso_report_path())
            general_path = dialog.general_report_path()
            general = parse_general_report(general_path) if general_path else None
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Failed to load report set", str(exc))
            return
        self.session = Session(report, general)
        self._saved_path = None
        self._refresh_all()

    def _open_aavso_report(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open AAVSO Report", "", "Text files (*.txt);;All files (*)"
        )
        if not path:
            return
        try:
            report = parse_aavso_report(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Failed to load report", str(exc))
            return
        self.session = Session(report)
        self._saved_path = None
        self._refresh_all()

    def _prompt_for_general_report(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open General Report", "", "Text files (*.txt);;All files (*)"
        )
        if not path:
            return
        try:
            general = parse_general_report(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Failed to load General Report", str(exc))
            return
        self.session.attach_general_report(general)
        self._refresh_all()

    def _apply_transform(self) -> None:
        reason = untransformable_reason(self.session.aavso)
        if reason:
            QMessageBox.information(self, "Apply Transform", reason)
            return
        dialog = TransformDialog(self.session, self)
        if dialog.exec() != QDialog.Accepted:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            result = self.session.apply_transform(dialog.coefficients(), dialog.lookup_auids())
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Transform failed", str(exc))
            return
        finally:
            QApplication.restoreOverrideCursor()
        self._refresh_all()
        if result.skipped:
            QMessageBox.warning(
                self,
                "Some frames left untransformed",
                f"{len(result.skipped)} frame(s) couldn't be paired and stay untransformed (TRANS=NO):\n\n"
                + "\n".join(result.skipped[:15]),
            )
        problem = self.session.ensemble_cross_check()
        if problem:
            QMessageBox.warning(self, "Ensemble cross-check", problem)

    def _update_transform_menu(self) -> None:
        self._clear_transform_action.setEnabled(self.session.has_after_report)

    def _clear_transform(self) -> None:
        self.session.clear_transform()
        self._refresh_all()

    def _create_submission(self) -> None:
        wizard = SubmissionWizard(self.session, self)
        if wizard.exec() == QDialog.Accepted and wizard.saved_path is not None:
            self._saved_path = wizard.saved_path
            self._refresh_steps()
            QMessageBox.information(
                self,
                "Submission report saved",
                f"Saved to\n{wizard.saved_path}\n\nUpload it to the AAVSO with WebObs (File upload).",
            )

    def _open_share_plot(self) -> None:
        SharePlotDialog(self.session, self).exec()

    # -- header -----------------------------------------------------------------

    def _refresh_header(self) -> None:
        s = self.session
        date_str = s.session_date.isoformat() if s.session_date else "?"
        badges = []
        badges.append("Transformed" if s.is_transformed else "Untransformed")
        if s.is_ensemble:
            badges.append("Ensemble")
        if s.has_after_report:
            badges.append(f"Before/After: {s.after_source}" if s.after_source else "Before/After Comparison")
        filters_str = ", ".join(s.filters)
        self.header_label.setText(
            f"{s.star_name}  —  {date_str}  —  Filters: {filters_str}  —  {' / '.join(badges)}"
        )

    # -- summary table -----------------------------------------------------------------

    def _refresh_table(self) -> None:
        has_after = self.session.has_after_report
        for col, name in enumerate(TABLE_COLUMNS):
            self.table.setColumnHidden(col, name in _AFTER_ONLY_COLUMNS and not has_after)

        rows = self.session.build_summary_rows()
        sections = [SECTION_TARGETS, SECTION_CHECK, SECTION_COMPARISON]
        by_section = {s: [r for r in rows if r.section == s] for s in sections}

        total_rows = len(sections) + len(rows)  # one header row per section
        self.table.clearSpans()
        self.table.setRowCount(total_rows)

        r = 0
        for section in sections:
            self._set_section_header(self.table, r, section, len(TABLE_COLUMNS))
            r += 1

            is_target = section == SECTION_TARGETS
            for row in by_section[section]:
                self.table.setItem(r, 0, self._star_item(row.star_label, row.catalog_mag))
                self.table.setItem(r, 1, self._center_item(row.filt))
                average = self._average_item(row.average, row.delta, row.kreferr, row.needs_general_report)
                average_after = self._average_item(row.average_after, row.delta_after, row.kreferr, False)
                if section == SECTION_CHECK:
                    self._add_check_hints(average, average_after, row)
                self.table.setItem(r, 2, average)
                self.table.setItem(r, 3, average_after)
                self.table.setItem(r, 4, self._range_item(row) if is_target else self._dash_item())
                self.table.setItem(r, 5, self._range_after_item(row) if is_target else self._dash_item())
                # Std is only a meaningful measurement-precision signal for non-
                # variable stars (check/comparison) -- a target's own Std reflects
                # real astrophysical variability too (rapid pulsators like CY Aqr
                # routinely swing 0.1+ mag per session), so coloring it against the
                # precision guide would be a category error. The check star's
                # colored Std already serves as the proxy for the target's likely
                # measurement error.
                self.table.setItem(r, 6, self._std_item(row.std, row.needs_general_report, is_target))
                self.table.setItem(r, 7, self._std_item(row.std_after, False, is_target))
                self.table.setItem(r, 8, self._numeric_item(row.kreferr))
                self.table.setItem(r, 9, self._vsx_range_item(row))
                self.table.setItem(r, 10, self._snr_item(row.avg_snr, row.avg_snr_available))
                r += 1

        self._resize_table_to_contents(self.table)

    @staticmethod
    def _set_section_header(table: QTableWidget, r: int, section: str, n_columns: int) -> None:
        table.setSpan(r, 0, 1, n_columns)
        header_item = QTableWidgetItem(section)
        header_item.setFlags(Qt.ItemIsEnabled)
        header_item.setBackground(Qt.lightGray)
        font = header_item.font()
        font.setBold(True)
        header_item.setFont(font)
        table.setItem(r, 0, header_item)

    @staticmethod
    def _center_item(text: str) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignCenter)
        return item

    @staticmethod
    def _star_item(star_label: str, catalog_mag: float | None) -> QTableWidgetItem:
        text = star_label if catalog_mag is None else f"{star_label} ({catalog_mag:.3f})"
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignCenter)
        return item

    @staticmethod
    def _numeric_item(value: float | None, clickable: bool = False, color=None) -> QTableWidgetItem:
        if value is not None:
            text = f"{value:.3f}"
        else:
            text = "*" if clickable else "-"
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignCenter)
        if text == "*":
            item.setForeground(Qt.gray)
        elif color is not None:
            item.setForeground(color)
        return item

    @staticmethod
    def _average_item(value: float | None, delta: float | None, kreferr: float | None, clickable: bool) -> QTableWidgetItem:
        if value is None:
            item = QTableWidgetItem("*" if clickable else "-")
            if clickable:
                item.setForeground(Qt.gray)
        else:
            text = f"{value:.3f}" if delta is None else f"{value:.3f} ({delta:+.3f})"
            item = QTableWidgetItem(text)
            _mark(item, coloring.ratio_color(delta, kreferr), coloring.ratio_tip(delta, kreferr))
        item.setTextAlignment(Qt.AlignCenter)
        return item

    def _add_check_hints(self, average: QTableWidgetItem, average_after: QTableWidgetItem, row) -> None:
        """A check star off by more than its catalog error gets a next step in its
        tooltip: transform if that hasn't been done, else where else to look."""
        if not _outside(row.delta, row.kreferr):
            return
        if not (self.session.is_transformed or self.session.has_after_report):
            average.setToolTip(average.toolTip() + "\n" + coloring.CHECK_TRY_TRANSFORM)
        elif self.session.has_after_report and _outside(row.delta_after, row.kreferr):
            average_after.setToolTip(average_after.toolTip() + "\n" + coloring.CHECK_STILL_OUTSIDE)

    @classmethod
    def _std_item(cls, std: float | None, clickable: bool, is_target: bool) -> QTableWidgetItem:
        item = cls._numeric_item(std, clickable)
        if not is_target:
            _mark(item, coloring.std_color(std), coloring.std_tip(std))
        return item

    @staticmethod
    def _dash_item() -> QTableWidgetItem:
        item = QTableWidgetItem("-")
        item.setTextAlignment(Qt.AlignCenter)
        return item

    @staticmethod
    def _range_item(row) -> QTableWidgetItem:
        if row.minimum is None or row.maximum is None:
            text = "-"
        else:
            text = f"{row.minimum:.3f}–{row.maximum:.3f}"
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignCenter)
        _mark(item, coloring.range_color(row.vsx_excess), coloring.range_tip(row.vsx_excess))
        return item

    @staticmethod
    def _range_after_item(row) -> QTableWidgetItem:
        if row.range_after is None:
            text = "-"
        else:
            text = f"{row.range_after[0]:.3f}–{row.range_after[1]:.3f}"
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignCenter)
        _mark(item, coloring.range_color(row.vsx_excess_after), coloring.range_tip(row.vsx_excess_after))
        return item

    @staticmethod
    def _vsx_range_item(row) -> QTableWidgetItem:
        if row.vsx_bright is None:
            text = "-"
        else:
            text = f"{row.vsx_bright:.2f}–{row.vsx_faint:.2f}"
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignCenter)
        return item

    @staticmethod
    def _snr_item(value: float | None, available: bool) -> QTableWidgetItem:
        if not available:
            text = "-"
        elif value is None:
            text = "*"
        else:
            text = f"{value:.0f}"
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignCenter)
        if text == "*":
            item.setForeground(Qt.gray)
        return item

    @staticmethod
    def _resize_table_to_contents(table: _FitTable) -> None:
        """Size a table to its content height so every row renders without the
        table's own scrollbar -- the outer page-level QScrollArea (see __init__)
        handles overflow instead, leaving the window itself fully user-resizable."""
        table.fit_columns()
        table.resizeRowsToContents()
        height = table.horizontalHeader().height() + 2 * table.frameWidth()
        for r in range(table.rowCount()):
            height += table.rowHeight(r)
        table.setMinimumHeight(height)
        table.setMaximumHeight(height)

    def _on_table_cell_clicked(self, row: int, column: int) -> None:
        item = self.table.item(row, column)
        if item is not None and item.text() == "*":
            self._prompt_for_general_report()

    # -- chart grid -----------------------------------------------------------------

    def _clear_chart_grid(self) -> None:
        while self.chart_grid.count():
            item = self.chart_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.thumbnails.clear()

    def _make_thumbnail(self, key: str, title: str) -> ChartThumbnail:
        thumb = ChartThumbnail(title)
        thumb.canvas.installEventFilter(self._wheel_forwarder)
        self.thumbnails[key] = thumb
        return thumb

    def _refresh_charts(self) -> None:
        self._clear_chart_grid()
        s = self.session
        grid_row = 0

        # Row 1: Target/Check Light Curve, full width alone -- paired with the Comp
        # Stars Light Curve when the session is an ensemble.
        light_curve = self._make_thumbnail("light_curve", "Target / Check Light Curve")
        light_curve.clicked.connect(lambda: LightCurveDetailDialog(s, self).exec())
        light_curve.render(
            lambda ax, thumbnail: plotting.plot_light_curve(
                ax, s.target_check_light_curve(), roles=("target", "check"), thumbnail=thumbnail
            )
        )

        if s.is_ensemble:
            self.chart_grid.addWidget(light_curve, grid_row, 0)
            comp_thumb = self._make_thumbnail("comp_stars", "Comparison Star Curves")
            if s.has_general_report:
                comp_thumb.clicked.connect(lambda: CompStarsDetailDialog(s, self).exec())
                comp_thumb.render(
                    lambda ax, thumbnail: plotting.plot_comp_stars(
                        ax, s.comp_stars_light_curve(), thumbnail=thumbnail
                    )
                )
            else:
                comp_thumb.clicked.connect(self._prompt_for_general_report)
                comp_thumb.set_unavailable()
            self.chart_grid.addWidget(comp_thumb, grid_row, 1)
        else:
            self.chart_grid.addWidget(light_curve, grid_row, 0, 1, 2)
        grid_row += 1

        # Row 2: SNR + Airmass, paired.
        snr_thumb = self._make_thumbnail("snr", "SNR")
        if s.has_general_report:
            snr_thumb.clicked.connect(
                lambda: DiagnosticDetailDialog(s, "SNR", "SNR", "SNR", parent=self).exec()
            )
            snr_thumb.render(
                lambda ax, thumbnail: plotting.plot_diagnostic(
                    ax, s.diagnostic_series("SNR"), thumbnail=thumbnail, ylabel="SNR"
                )
            )
        else:
            snr_thumb.clicked.connect(self._prompt_for_general_report)
            snr_thumb.set_unavailable()
        self.chart_grid.addWidget(snr_thumb, grid_row, 0)

        airmass_thumb = self._make_thumbnail("airmass", "Airmass")
        airmass_thumb.clicked.connect(
            lambda: DiagnosticDetailDialog(s, "AMASS", "Airmass", "Airmass", is_airmass=True, parent=self).exec()
        )
        airmass_thumb.render(
            lambda ax, thumbnail: plotting.plot_airmass(ax, s.airmass_series(), thumbnail=thumbnail)
        )
        self.chart_grid.addWidget(airmass_thumb, grid_row, 1)
        grid_row += 1

        # Row 3: FWHM + Max ADU, paired.
        fwhm_thumb = self._make_thumbnail("fwhm", "FWHM")
        if s.has_general_report:
            fwhm_thumb.clicked.connect(
                lambda: DiagnosticDetailDialog(s, "FWHM", "FWHM", "FWHM (px)", parent=self).exec()
            )
            fwhm_thumb.render(
                lambda ax, thumbnail: plotting.plot_diagnostic(
                    ax, s.diagnostic_series("FWHM"), thumbnail=thumbnail, ylabel="FWHM (px)"
                )
            )
        else:
            fwhm_thumb.clicked.connect(self._prompt_for_general_report)
            fwhm_thumb.set_unavailable()
        self.chart_grid.addWidget(fwhm_thumb, grid_row, 0)

        maxadu_thumb = self._make_thumbnail("max_adu", "Max ADU")
        if s.has_general_report:
            maxadu_thumb.clicked.connect(
                lambda: DiagnosticDetailDialog(s, "Max ADU", "Max ADU", "Max ADU", parent=self).exec()
            )
            maxadu_thumb.render(
                lambda ax, thumbnail: plotting.plot_diagnostic(
                    ax, s.diagnostic_series("Max ADU"), thumbnail=thumbnail, ylabel="Max ADU"
                )
            )
        else:
            maxadu_thumb.clicked.connect(self._prompt_for_general_report)
            maxadu_thumb.set_unavailable()
        self.chart_grid.addWidget(maxadu_thumb, grid_row, 1)
        grid_row += 1

        # Row 4: Skyglow, alone (Tracking Error's former pairing slot is dropped).
        skyglow_thumb = self._make_thumbnail("skyglow", "Skyglow")
        if s.has_general_report:
            skyglow_thumb.clicked.connect(
                lambda: DiagnosticDetailDialog(s, "Skyglow", "Skyglow", "Skyglow", parent=self).exec()
            )
            skyglow_thumb.render(
                lambda ax, thumbnail: plotting.plot_diagnostic(
                    ax, s.diagnostic_series("Skyglow"), thumbnail=thumbnail, ylabel="Skyglow"
                )
            )
        else:
            skyglow_thumb.clicked.connect(self._prompt_for_general_report)
            skyglow_thumb.set_unavailable()
        self.chart_grid.addWidget(skyglow_thumb, grid_row, 0)
        grid_row += 1

        # Row 5: Check Star Before/After + Target Before/After, paired -- only shown
        # once the transform has been applied.
        if s.has_after_report:
            check_ba_thumb = self._make_thumbnail("check_before_after", "Check Star Before/After")
            check_ba_thumb.clicked.connect(lambda: CheckBeforeAfterDetailDialog(s, self).exec())
            check_ba_thumb.render(
                lambda ax, thumbnail: plotting.plot_check_before_after(ax, s, thumbnail=thumbnail)
            )
            self.chart_grid.addWidget(check_ba_thumb, grid_row, 0)

            target_ba_thumb = self._make_thumbnail("target_before_after", "Target Before/After")
            target_ba_thumb.clicked.connect(lambda: TargetBeforeAfterDetailDialog(s, self).exec())
            target_ba_thumb.render(
                lambda ax, thumbnail: plotting.plot_target_before_after(ax, s, thumbnail=thumbnail)
            )
            self.chart_grid.addWidget(target_ba_thumb, grid_row, 1)

    # -- combined refresh -----------------------------------------------------------------

    def _refresh_steps(self) -> None:
        self.step_strip.show_states(self._step_states())

    def _step_states(self) -> list[StepState]:
        s = self.session
        n_frames = int((s.aavso.df["FILT"] == s.filters[0]).sum()) if s.filters else 0
        comps = f"ensemble of {len(s.comp_star_names())} comps" if s.is_ensemble else "single comp"
        general = "General Report loaded" if s.has_general_report else "no General Report"
        inspect_actions = []
        if not s.has_general_report:
            inspect_actions.append(("Add General Report…", self._prompt_for_general_report))
        inspect = StepState(
            f"{s.star_name}, {n_frames} frames × {'/'.join(s.filters)}<br>{comps}, {general}", DONE, inspect_actions
        )

        transformed = s.has_after_report
        if s.is_transformed:
            transform = StepState("This report is already transformed (TRANS=YES).", DONE)
        elif transformed:
            source = s.after_coeffs.source if s.after_coeffs else (s.after_source or "")
            transform = StepState(
                f"Applied with {source}", DONE,
                [("Change…", self._apply_transform), ("Clear", self._clear_transform)],
            )
        else:
            transform = StepState(
                "Not applied yet. Uses your Transform Generator coefficients (.ini).", CURRENT,
                [("Apply Transform…", self._apply_transform)],
            )

        if transformed:
            check = [r for r in s.build_summary_rows() if r.section == SECTION_CHECK]
            moves = " · ".join(
                f"{r.filt} {r.delta:+.3f} → {r.delta_after:+.3f}"
                for r in check if r.delta is not None and r.delta_after is not None
            )
            name = check[0].star_label if check else "check star"
            compare = StepState(
                f"Check {name} vs catalog:<br>{moves}", DONE, [("Before/after charts", self._show_before_after)]
            )
        elif s.is_transformed:
            compare = StepState("No before/after: the report was transformed outside the app.", UPCOMING, enabled=False)
        else:
            compare = StepState("After transforming: how far the check star and comps moved.", UPCOMING, enabled=False)

        if self._saved_path is not None:
            submit = StepState(
                f"Saved {self._saved_path.name}.<br>Upload it with WebObs.", DONE,
                [("Create Again…", self._create_submission)],
            )
        else:
            ready = transformed or s.is_transformed
            submit = StepState(
                "Build the report to submit, with the right uncertainty." if ready
                else "You can submit untransformed, but transforming first is better.",
                CURRENT if ready else UPCOMING,
                [("Create Submission Report…", self._create_submission)],
            )
        return [inspect, transform, compare, submit]

    def _show_before_after(self) -> None:
        thumb = self.thumbnails.get("check_before_after")
        if thumb is not None:
            self._scroll_area.ensureWidgetVisible(thumb, 0, 40)

    def _refresh_all(self) -> None:
        self._refresh_steps()
        self._refresh_header()
        self._refresh_table()
        self._refresh_charts()

    def _set_initial_size(self) -> None:
        """Pick a generous default window size, bounded to the screen, once at
        startup -- never re-applied on refresh, so the user's own resizing (and the
        page-level scroll area for whatever doesn't fit) is always respected."""
        width, height = 1150, 950
        screen = QApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            width = min(width, avail.width() - 80)
            height = min(height, avail.height() - 80)
        self.resize(width, height)
