from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QColor
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

from aavso_viewer.models.session import (
    SECTION_CHECK,
    SECTION_COMPARISON,
    SECTION_TARGETS,
    Session,
)
from aavso_viewer.parsing.aavso_report import parse_aavso_report
from aavso_viewer.parsing.general_report import parse_general_report
from aavso_viewer.ui import plotting
from aavso_viewer.ui.chart_detail_dialog import (
    CompStarsDetailDialog,
    DiagnosticDetailDialog,
    LightCurveDetailDialog,
)
from aavso_viewer.ui.chart_thumbnail import ChartThumbnail
from aavso_viewer.ui.startup_dialog import StartupDialog

TABLE_COLUMNS = ["Star", "Filter", "Average", "Min", "Max", "Std", "KREFERR", "Avg. SNR"]

# Delta vs. catalog uncertainty: the natural yardstick for whether an observed offset
# from the catalog magnitude is real. Matches the notebook's Good/Flag palette.
COLOR_GOOD = QColor("#2e7d32")
COLOR_FLAG = QColor("#d32f2f")

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


class MainWindow(QMainWindow):
    def __init__(self, session: Session):
        super().__init__()
        self.session = session
        self.transformed_report_path: str | None = None

        self.setWindowTitle("AAVSO Report Viewer")

        self._build_menu()

        central = QWidget()
        layout = QVBoxLayout(central)

        self.header_label = QLabel()
        self.header_label.setStyleSheet("font-size: 13px; font-weight: 600; padding: 4px;")
        layout.addWidget(self.header_label)

        self.table = QTableWidget()
        self.table.setColumnCount(len(TABLE_COLUMNS))
        self.table.setHorizontalHeaderLabels(TABLE_COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.cellClicked.connect(self._on_table_cell_clicked)
        # The table always renders every row at full height (no internal scrollbar of
        # its own) -- the outer QScrollArea below is what scrolls when the window is
        # smaller than the table + chart grid combined, so resizing the window is left
        # entirely up to the user instead of the app fighting it on every refresh.
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        layout.addWidget(self.table)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(central)
        self.setCentralWidget(scroll_area)
        self._scroll_area = scroll_area

        self._wheel_forwarder = _WheelForwarder(scroll_area, self)
        self.table.viewport().installEventFilter(self._wheel_forwarder)
        self.table.installEventFilter(self._wheel_forwarder)

        self._set_initial_size()

        self.chart_grid = QGridLayout()
        self.chart_grid.setSpacing(8)
        layout.addLayout(self.chart_grid)

        self.thumbnails: dict[str, ChartThumbnail] = {}

        self._refresh_all()

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

    def _open_report_set(self) -> None:
        """Load a whole new session (AAVSO Report + optional Transformed/General
        Report) via the same three-field dialog shown at startup, without needing to
        restart the app."""
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
        self.transformed_report_path = dialog.transformed_report_path()
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

    # -- header -----------------------------------------------------------------

    def _refresh_header(self) -> None:
        s = self.session
        date_str = s.session_date.isoformat() if s.session_date else "?"
        badges = []
        badges.append("Transformed" if s.is_transformed else "Untransformed")
        if s.is_ensemble:
            badges.append("Ensemble")
        filters_str = ", ".join(s.filters)
        self.header_label.setText(
            f"{s.star_name}  —  {date_str}  —  Filters: {filters_str}  —  {' / '.join(badges)}"
        )

    # -- summary table -----------------------------------------------------------------

    def _refresh_table(self) -> None:
        rows = self.session.build_summary_rows()
        sections = [SECTION_TARGETS, SECTION_CHECK, SECTION_COMPARISON]
        by_section = {s: [r for r in rows if r.section == s] for s in sections}

        total_rows = len(sections) + len(rows)  # one header row per section
        self.table.setRowCount(total_rows)

        r = 0
        for section in sections:
            self.table.setSpan(r, 0, 1, len(TABLE_COLUMNS))
            header_item = QTableWidgetItem(section)
            header_item.setFlags(Qt.ItemIsEnabled)
            header_item.setBackground(Qt.lightGray)
            font = header_item.font()
            font.setBold(True)
            header_item.setFont(font)
            self.table.setItem(r, 0, header_item)
            r += 1

            for row in by_section[section]:
                self.table.setItem(r, 0, self._star_item(row.star_label, row.catalog_mag))
                self.table.setItem(r, 1, self._center_item(row.filt))
                self.table.setItem(r, 2, self._average_item(row))
                self.table.setItem(r, 3, self._numeric_item(row.minimum, row.needs_general_report))
                self.table.setItem(r, 4, self._numeric_item(row.maximum, row.needs_general_report))
                self.table.setItem(r, 5, self._numeric_item(row.std, row.needs_general_report))
                self.table.setItem(r, 6, self._numeric_item(row.kreferr))
                self.table.setItem(r, 7, self._snr_item(row.avg_snr, row.avg_snr_available))
                r += 1

        self._resize_table_to_contents()

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
    def _numeric_item(value: float | None, clickable: bool = False) -> QTableWidgetItem:
        if value is not None:
            text = f"{value:.3f}"
        else:
            text = "*" if clickable else "-"
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignCenter)
        if text == "*":
            item.setForeground(Qt.gray)
        return item

    @staticmethod
    def _average_item(row) -> QTableWidgetItem:
        if row.average is None:
            item = QTableWidgetItem("*" if row.needs_general_report else "-")
            if row.needs_general_report:
                item.setForeground(Qt.gray)
        else:
            delta = row.delta
            text = f"{row.average:.3f}" if delta is None else f"{row.average:.3f} ({delta:+.3f})"
            item = QTableWidgetItem(text)
            if delta is not None and row.kreferr:
                item.setForeground(COLOR_FLAG if abs(delta) > row.kreferr else COLOR_GOOD)
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

    def _resize_table_to_contents(self) -> None:
        """Size the table to its content height so every row renders without the
        table's own scrollbar -- the outer page-level QScrollArea (see __init__)
        handles overflow instead, leaving the window itself fully user-resizable."""
        self.table.resizeRowsToContents()
        height = self.table.horizontalHeader().height() + 2 * self.table.frameWidth()
        for r in range(self.table.rowCount()):
            height += self.table.rowHeight(r)
        self.table.setMinimumHeight(height)
        self.table.setMaximumHeight(height)

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

    # -- combined refresh -----------------------------------------------------------------

    def _refresh_all(self) -> None:
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
