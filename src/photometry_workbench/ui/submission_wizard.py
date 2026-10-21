"""Create Submission Report: walk the AAVSO uncertainty course's decision tree, then
preview and save the AAVSO Extended file to submit."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWizard,
    QWidget,
    QWizardPage,
)

from photometry_workbench.models.session import Session
from photometry_workbench.parsing.aavso_report import AavsoReport
from photometry_workbench.parsing.aavso_writer import format_report, write_aavso_report
from photometry_workbench.submission.builder import (
    CHECK_MAG_LIMIT,
    ErrorMethod,
    Submission,
    SubmissionError,
    build_submission,
    choose_method,
    frame_consistency,
)
from photometry_workbench.transform.engine import untransformable_reason
from photometry_workbench.ui import coloring
from photometry_workbench.ui.example_diagrams import combine_diagram, light_curve_diagram

PAGE_SOURCE, PAGE_COMBINE, PAGE_VARIES, PAGE_LIGHT_CURVE, PAGE_PREVIEW = range(5)

_PREVIEW_COLUMNS = ["DATE", "MAG", "MERR", "FILT", "TRANS", "CNAME", "KNAME", "AMASS", "GROUP"]
_PREVIEW_FORMATS = {"DATE": "{:.6f}", "MAG": "{:.3f}", "MERR": "{:.3f}", "AMASS": "{:.4f}"}


def _wrapped(text: str, gray: bool = False) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    if gray:
        label.setStyleSheet("color: gray;")
    return label


def _choice(page: QWizardPage, layout: QVBoxLayout, options: list[str]) -> QButtonGroup:
    group = QButtonGroup(page)
    for i, text in enumerate(options):
        button = QRadioButton(text)
        group.addButton(button, i)
        layout.addWidget(button)
    group.idToggled.connect(lambda *_: page.completeChanged.emit())
    return group


class _ChoiceCard(QFrame):
    """One answer as a card: a radio-button title, a diagram and a short description.
    Clicking anywhere on the card picks it."""

    def __init__(self, title: str, diagram: QWidget, text: str):
        super().__init__()
        self.setObjectName("card")
        self.radio = QRadioButton(title)
        font = self.radio.font()
        font.setBold(True)
        self.radio.setFont(font)
        self.radio.toggled.connect(self._restyle)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.addWidget(self.radio)
        layout.addWidget(diagram)
        layout.addWidget(_wrapped(text))
        layout.addStretch()
        self.setCursor(Qt.PointingHandCursor)
        for child in (self, *self.findChildren(QWidget)):
            child.installEventFilter(self)
        self._restyle()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.MouseButtonPress:
            self.radio.setChecked(True)
        return False

    def _restyle(self, *_):
        border = "2px solid palette(highlight)" if self.radio.isChecked() else "1px solid palette(mid)"
        self.setStyleSheet(f"QFrame#card {{ border: {border}; border-radius: 8px; }}")


def _read_only_table(columns: list[str]) -> QTableWidget:
    table = QTableWidget(0, len(columns))
    table.setHorizontalHeaderLabels(columns)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QTableWidget.NoEditTriggers)
    return table


def _fit_height(table: QTableWidget) -> None:
    """Shrink a small table to exactly its rows, so it takes no more room than it needs."""
    table.resizeRowsToContents()
    height = table.horizontalHeader().height() + 2 * table.frameWidth()
    table.setFixedHeight(height + sum(table.rowHeight(r) for r in range(table.rowCount())))


def _titled_table(parent_layout: QHBoxLayout, title: str, columns: list[str]) -> tuple[QWidget, QTableWidget]:
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(QLabel(title))
    table = _read_only_table(columns)
    layout.addWidget(table)
    layout.addStretch()
    parent_layout.addWidget(box)
    return box, table


def _set_cell(table: QTableWidget, r: int, c: int, text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setTextAlignment(Qt.AlignCenter)
    table.setItem(r, c, item)
    return item


class _SourcePage(QWizardPage):
    def __init__(self, session: Session):
        super().__init__()
        self.setTitle("Which magnitudes to submit")
        layout = QVBoxLayout(self)
        self.choice = None
        if session.has_after_report:
            layout.addWidget(_wrapped("Submit the transformed magnitudes, or the untransformed ones as VPhot reported them?"))
            self.choice = _choice(self, layout, [
                f"Transformed ({session.after_source or 'transformed report'})",
                "Untransformed (as reported)",
            ])
            self.choice.button(0).setChecked(True)
        elif session.is_transformed:
            layout.addWidget(_wrapped("The loaded report is already transformed (TRANS=YES)."))
        else:
            reason = untransformable_reason(session.aavso)
            hint = (
                "Close this and use Apply Transform… in step 2 first if you want to submit transformed magnitudes."
                if reason is None
                else f"It can't be transformed here: {reason}"
            )
            layout.addWidget(_wrapped(f"No transform is applied, so this is submitted untransformed (TRANS=NO). {hint}"))
        layout.addStretch()

    def use_transformed(self) -> bool:
        return self.choice is not None and self.choice.checkedId() == 0


class _CombinePage(QWizardPage):
    def __init__(self, wizard: "SubmissionWizard"):
        super().__init__()
        self._wizard = wizard
        self.setTitle("How will you report these frames?")
        layout = QVBoxLayout(self)
        cards = QHBoxLayout()
        cards.setSpacing(12)
        combine = _ChoiceCard(
            "One observation per filter",
            combine_diagram(),
            "Frames taken close together, averaged into a single point. Their scatter gives the "
            "uncertainty. Preferred when the science allows it: a supernova, a slow variable.",
        )
        light_curve = _ChoiceCard(
            "A light curve",
            light_curve_diagram(),
            "Every frame reported as its own point, to follow the star's changes through the "
            "session: a short-period pulsator, an eclipse.",
        )
        self.choice = QButtonGroup(self)
        for i, card in enumerate((combine, light_curve)):
            self.choice.addButton(card.radio, i)
            cards.addWidget(card, 1)
        self.choice.idToggled.connect(lambda *_: self.completeChanged.emit())
        layout.addLayout(cards)
        self.summary = _wrapped("", gray=True)
        layout.addWidget(self.summary)
        layout.addStretch()

    def initializePage(self):
        df = self._wizard.source_report().df
        parts = []
        for filt in self._wizard.source_report().filters:
            dates = df.loc[df["FILT"] == filt, "DATE"]
            parts.append(f"{filt}: {len(dates)} frames over {(dates.max() - dates.min()) * 1440:.1f} min")
        self.summary.setText("This session: " + "; ".join(parts))

    def isComplete(self):
        return self.choice.checkedId() >= 0

    def combine(self) -> bool:
        return self.choice.checkedId() == 0


class _VariesPage(QWizardPage):
    def __init__(self, wizard: "SubmissionWizard"):
        super().__init__()
        self._wizard = wizard
        self.setTitle("Will the target vary during the observation?")
        layout = QVBoxLayout(self)
        layout.addWidget(_wrapped(
            "Do you expect the variable to change brightness over the span of these frames? "
            "If not, its own scatter is the uncertainty. If it may (a fast pulsator, a flaring "
            "star), the scatter of a check star of similar brightness is used instead."
        ))
        self.choice = _choice(self, layout, [
            "No, not expected to change during the observation (e.g. a supernova, a slow variable)",
            "Yes, it may change during the observation",
        ])
        layout.addWidget(_wrapped(
            "A check: if the target's Std is well above the check star's (allowing for the target "
            "being fainter), the target is probably varying.", gray=True,
        ))
        self.table = _read_only_table(["Filter", "Frames", "Target Std", "Check Std", "Target − Check"])
        layout.addWidget(self.table)

    def initializePage(self):
        report = self._wizard.source_report()
        df = report.df
        self.table.setRowCount(len(report.filters))
        for r, filt in enumerate(report.filters):
            rows = df.loc[df["FILT"] == filt]
            separation = rows["MAG"].mean() - rows["KEFF"].mean()
            _set_cell(self.table, r, 0, filt)
            _set_cell(self.table, r, 1, str(len(rows)))
            _set_cell(self.table, r, 2, f"{rows['MAG'].std(ddof=1):.3f}")
            _set_cell(self.table, r, 3, f"{rows['KEFF'].std(ddof=1):.3f}")
            item = _set_cell(self.table, r, 4, f"{separation:+.2f} mag")
            if abs(separation) > CHECK_MAG_LIMIT:
                item.setForeground(coloring.FLAG)
                item.setToolTip(f"More than {CHECK_MAG_LIMIT:g} mag apart, a poor stand-in for the target's scatter.")

    def isComplete(self):
        return self.choice.checkedId() >= 0

    def varies(self) -> bool:
        return self.choice.checkedId() == 1


class _LightCurvePage(QWizardPage):
    def __init__(self, wizard: "SubmissionWizard"):
        super().__init__()
        self._wizard = wizard
        self.setTitle("Each frame reported on its own")
        layout = QVBoxLayout(self)
        self.text = _wrapped("")
        layout.addWidget(self.text)
        self.spread_caption = _wrapped(
            "Each frame's uncertainty combines two parts: how much the comparison stars disagree "
            "(their spread) and the target's own SNR error. Medians over the frames:", gray=True)
        layout.addWidget(self.spread_caption)
        self.spread_table = _read_only_table(["Filter", "Comps", "Spread across comps", "SNR error", "Reported (combined)"])
        layout.addWidget(self.spread_table)
        self.problem = _wrapped("")
        self.problem.setStyleSheet(f"color: {coloring.FLAG.name()};")
        layout.addWidget(self.problem)
        layout.addStretch()

    def initializePage(self):
        method = self._wizard.method()
        self.text.setText(method.description)
        ensemble = method == ErrorMethod.COMP_SPREAD
        self.spread_table.setVisible(ensemble)
        self.spread_caption.setVisible(ensemble)
        self.problem.clear()
        if ensemble:
            self._wizard.spread = None
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                self._wizard.spread = self._wizard.session.comp_spread(self._wizard.source_page.use_transformed())
            except ValueError as exc:
                self.problem.setText(str(exc))
            finally:
                QApplication.restoreOverrideCursor()
            self._fill_spread_table()
        self.completeChanged.emit()

    def _fill_spread_table(self):
        spread = self._wizard.spread
        report = self._wizard.source_report()
        self.spread_table.setRowCount(0 if spread is None else len(report.filters))
        if spread is None:
            return
        df = report.df
        for r, filt in enumerate(report.filters):
            rows = df["FILT"] == filt
            comps = sorted({int(n) for n in spread.loc[rows, "n"].dropna()})
            _set_cell(self.spread_table, r, 0, filt)
            _set_cell(self.spread_table, r, 1, "/".join(map(str, comps)) or "0")
            _set_cell(self.spread_table, r, 2, f"{spread.loc[rows, 'std'].median():.3f}")
            _set_cell(self.spread_table, r, 3, f"{df.loc[rows, 'MERR'].median():.3f}")
            combined = np.hypot(spread.loc[rows, "std"], df.loc[rows, "MERR"])
            _set_cell(self.spread_table, r, 4, f"{combined.median():.3f}")
        _fit_height(self.spread_table)

    def isComplete(self):
        return self._wizard.method() != ErrorMethod.COMP_SPREAD or self._wizard.spread is not None


class _PreviewPage(QWizardPage):
    def __init__(self, wizard: "SubmissionWizard"):
        super().__init__()
        self._wizard = wizard
        self.setTitle("Preview")
        self.setFinalPage(True)
        layout = QVBoxLayout(self)
        self.method_label = _wrapped("")
        layout.addWidget(self.method_label)
        self.warning_label = _wrapped("")
        self.warning_label.setStyleSheet(f"color: {coloring.BORDERLINE.name()};")
        layout.addWidget(self.warning_label)
        self.table = _read_only_table(_PREVIEW_COLUMNS)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.itemSelectionChanged.connect(self._show_notes)
        layout.addWidget(self.table, 2)
        layout.addWidget(QLabel("NOTES of the selected row"))
        self.notes = QPlainTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setMaximumHeight(70)
        layout.addWidget(self.notes)
        checks = QHBoxLayout()
        layout.addLayout(checks)
        # The consistency check for a combined point: the Std across frames should be about
        # the same as, or lower than, what a single frame's uncertainty predicts.
        self.consistency_box, self.consistency_table = _titled_table(
            checks, "Single frame vs across frames", ["Filter", "Single frame", "Across frames", "Ratio"]
        )
        # How much the transformed magnitudes depend on the transform coefficients'
        # own errors, judged against the MERR being submitted. It's a systematic
        # uncertainty, so it's shown for reference and not added to MERR.
        self.coeff_box, self.coeff_table = _titled_table(
            checks, "Transform coefficient uncertainty (not included in the reported uncertainty)", ["Filter", "Uncertainty", "Coeff ±"]
        )
        checks.addStretch()
        self.submission: Submission | None = None

    def initializePage(self):
        self.submission = None
        self.table.setRowCount(0)
        self.notes.clear()
        self.consistency_box.hide()
        self.coeff_box.hide()
        method = self._wizard.method()
        try:
            self.submission = build_submission(
                self._wizard.source_report(),
                method,
                comp_spread=self._wizard.spread,
            )
            format_report(self.submission.report)  # surface any field-validation error now, not at save
        except (SubmissionError, ValueError) as exc:
            self.submission = None
            self.method_label.setText(f"Can't build the report: {exc}")
            self.warning_label.clear()
            self.completeChanged.emit()
            return
        self.method_label.setText(method.description + " The uncertainty goes in the report's MERR column.")
        self.warning_label.setText("\n".join(self.submission.warnings))
        df = self.submission.report.df
        self.table.setRowCount(len(df))
        for r, (_, row) in enumerate(df.iterrows()):
            for c, column in enumerate(_PREVIEW_COLUMNS):
                value = row[column]
                fmt = _PREVIEW_FORMATS.get(column)
                _set_cell(self.table, r, c, fmt.format(value) if fmt and value == value else str(value))
        self.table.selectRow(0)
        if method == ErrorMethod.STD_TARGET:
            self._fill_consistency_table()
        self._fill_coeff_table()
        self.completeChanged.emit()

    def _fill_coeff_table(self):
        sensitivity = None
        if self._wizard.source_page.use_transformed():
            try:
                sensitivity = self._wizard.session.coefficient_sensitivity()
            except ValueError:
                sensitivity = None
        self.coeff_box.setVisible(bool(sensitivity))
        if not sensitivity:
            return
        # One MERR per filter for a combined point; a light curve's vary by frame.
        merr = self.submission.report.df.groupby("FILT")["MERR"].median()
        light_curve = not self._wizard.combine_page.combine()
        self.coeff_table.setHorizontalHeaderLabels(["Filter", "Uncertainty (median)" if light_curve else "Uncertainty", "Coeff ±"])
        filters = [f for f in self.submission.report.filters if f in sensitivity]
        self.coeff_table.setRowCount(len(filters))
        for r, filt in enumerate(filters):
            s = sensitivity[filt]
            reference = float(merr[filt]) if filt in merr else None
            _set_cell(self.coeff_table, r, 0, filt)
            _set_cell(self.coeff_table, r, 1, "-" if reference is None else f"{reference:.3f}")
            item = _set_cell(self.coeff_table, r, 2, f"{s.total:.3f}")
            # Same 1x/2x rule as the main window's Average deltas: past MERR, the
            # coefficients' systematic error outweighs the random one.
            color = coloring.ratio_color(s.total, reference)
            if color is not None:
                item.setForeground(color)
            item.setToolTip(
                "Shift in the mean magnitude with each coefficient moved by ± its error:\n"
                + "\n".join(f"{name}: {shift:.4f}" for name, shift in sorted(s.terms.items(), key=lambda t: -t[1]))
            )
        _fit_height(self.coeff_table)

    def _fill_consistency_table(self):
        report = self._wizard.source_report()
        spread = None
        if report.is_ensemble:
            try:
                spread = self._wizard.session.comp_spread(self._wizard.source_page.use_transformed())
            except ValueError:
                spread = None  # without the General Report, a frame's MERR is its SNR error alone
        if report.is_ensemble and spread is not None:
            source = "the spread across the comparison stars combined with the SNR uncertainty (Technique #3)"
        else:
            source = "its SNR uncertainty (as propagated by the transform when transformed)"
        rows = frame_consistency(report, spread)
        self.consistency_table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            _set_cell(self.consistency_table, r, 0, row.filt)
            _set_cell(self.consistency_table, r, 1, f"{row.single:.3f}").setToolTip(
                f"Median over the {row.filt} frames of each frame's uncertainty: {source}."
            )
            _set_cell(self.consistency_table, r, 2, f"{row.across:.3f}").setToolTip(
                "Std of the target's magnitude across the frames: the uncertainty being submitted."
            )
            item = _set_cell(self.consistency_table, r, 3, f"{row.ratio:.1f}×")
            color = coloring.consistency_color(row.ratio)
            if color is not None:
                item.setForeground(color)
            item.setToolTip(
                "About 1× or lower is expected. 1.5× or more means something is worth tracking down: "
                "one bad frame, clouds, or (when transformed) noise in each frame's color."
            )
        _fit_height(self.consistency_table)
        self.consistency_box.setVisible(bool(rows))

    def _show_notes(self):
        rows = self.table.selectionModel().selectedRows()
        if self.submission is not None and rows:
            self.notes.setPlainText(str(self.submission.report.df.iloc[rows[0].row()]["NOTES"]))

    def isComplete(self):
        return self.submission is not None


class SubmissionWizard(QWizard):
    def __init__(self, session: Session, parent=None):
        super().__init__(parent)
        self.session = session
        self.setWindowTitle("Create Submission Report")
        self.setMinimumSize(760, 560)
        self.setOption(QWizard.NoBackButtonOnLastPage, False)
        self.setButtonText(QWizard.FinishButton, "Save…")
        # The macOS style puts stock Qt artwork down the left; the classic style has none.
        self.setWizardStyle(QWizard.ClassicStyle)
        self.spread = None
        """Per-frame comp spread (Session.comp_spread) for an ensemble light curve."""
        self.source_page = _SourcePage(session)
        self.combine_page = _CombinePage(self)
        self.varies_page = _VariesPage(self)
        self.light_curve_page = _LightCurvePage(self)
        self.preview_page = _PreviewPage(self)
        self.setPage(PAGE_SOURCE, self.source_page)
        self.setPage(PAGE_COMBINE, self.combine_page)
        self.setPage(PAGE_VARIES, self.varies_page)
        self.setPage(PAGE_LIGHT_CURVE, self.light_curve_page)
        self.setPage(PAGE_PREVIEW, self.preview_page)
        self.saved_path: Path | None = None

    def source_report(self) -> AavsoReport:
        if self.source_page.use_transformed():
            return self.session.after
        return self.session.aavso

    def method(self) -> ErrorMethod:
        combine = self.combine_page.combine()
        varies = self.varies_page.varies() if combine else None
        return choose_method(combine, varies, self.source_report().is_ensemble)

    def nextId(self):
        current = self.currentId()
        if current == PAGE_SOURCE:
            return PAGE_COMBINE
        if current == PAGE_COMBINE:
            return PAGE_VARIES if self.combine_page.combine() else PAGE_LIGHT_CURVE
        if current in (PAGE_VARIES, PAGE_LIGHT_CURVE):
            return PAGE_PREVIEW
        return -1

    def _default_path(self) -> Path:
        report = self.source_report()
        date = self.session.session_date
        name = "_".join([
            "AAVSOReport",
            self.session.star_name.replace(" ", "-"),
            "".join(report.filters),
            date.strftime("%Y%m%d") if date else "session",
            "submit",
        ]) + ".txt"
        folder = self.session.aavso.source_path.parent if self.session.aavso.source_path else Path.home()
        return folder / name

    def accept(self):
        submission = self.preview_page.submission
        if submission is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Submission Report", str(self._default_path()), "Text files (*.txt);;All files (*)"
        )
        if not path:
            return  # stay in the wizard
        try:
            write_aavso_report(submission.report, path)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Failed to save report", str(exc))
            return
        self.saved_path = Path(path)
        super().accept()
