"""Create Submission Report: walk the AAVSO uncertainty course's decision tree, then
preview and save the AAVSO Extended file to submit."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from aavso_viewer.models.session import Session
from aavso_viewer.parsing.aavso_report import AavsoReport
from aavso_viewer.parsing.aavso_writer import format_report, write_aavso_report
from aavso_viewer.submission.builder import (
    CHECK_MAG_LIMIT,
    ErrorMethod,
    Submission,
    SubmissionError,
    build_submission,
    choose_method,
)
from aavso_viewer.transform.engine import untransformable_reason
from aavso_viewer.ui import coloring

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


def _read_only_table(columns: list[str]) -> QTableWidget:
    table = QTableWidget(0, len(columns))
    table.setHorizontalHeaderLabels(columns)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QTableWidget.NoEditTriggers)
    return table


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
                "Close this and use Transform → Apply Transform… first if you want to submit transformed magnitudes."
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
        self.setTitle("Combine frames into one observation?")
        layout = QVBoxLayout(self)
        layout.addWidget(_wrapped(
            "Do you have more than one image that you'll report as a single data point? "
            "Combining frames is preferred when the science allows it -- the scatter across "
            "the frames then gives a real uncertainty instead of one based on SNR alone."
        ))
        self.summary = _wrapped("", gray=True)
        layout.addWidget(self.summary)
        self.choice = _choice(self, layout, [
            "Yes -- combine all frames into one observation per filter",
            "No -- report every frame on its own (a light curve)",
        ])
        layout.addStretch()

    def initializePage(self):
        df = self._wizard.source_report().df
        parts = []
        for filt in self._wizard.source_report().filters:
            dates = df.loc[df["FILT"] == filt, "DATE"]
            parts.append(f"{filt}: {len(dates)} frames over {(dates.max() - dates.min()) * 1440:.1f} min")
        self.summary.setText("This session -- " + "; ".join(parts))

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
            "No -- not expected to change during the observation (e.g. a supernova, a slow variable)",
            "Yes -- it may change during the observation",
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
                item.setToolTip(f"More than {CHECK_MAG_LIMIT:g} mag apart -- a poor stand-in for the target's scatter.")

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
        layout.addStretch()

    def initializePage(self):
        method = self._wizard.method()
        text = "The target is assumed bright (well above sky, read and dark noise). " + method.description
        if method == ErrorMethod.COMP_SPREAD:
            text += "\n\nThis isn't supported yet -- ensemble light curves are still to be built."
        self.text.setText(text)
        self.completeChanged.emit()

    def isComplete(self):
        return self._wizard.method() != ErrorMethod.COMP_SPREAD


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
        self.submission: Submission | None = None

    def initializePage(self):
        self.submission = None
        self.table.setRowCount(0)
        self.notes.clear()
        method = self._wizard.method()
        try:
            self.submission = build_submission(self._wizard.source_report(), method)
            format_report(self.submission.report)  # surface any field-validation error now, not at save
        except (SubmissionError, ValueError) as exc:
            self.submission = None
            self.method_label.setText(f"Can't build the report: {exc}")
            self.warning_label.clear()
            self.completeChanged.emit()
            return
        self.method_label.setText(method.description)
        self.warning_label.setText("\n".join(self.submission.warnings))
        df = self.submission.report.df
        self.table.setRowCount(len(df))
        for r, (_, row) in enumerate(df.iterrows()):
            for c, column in enumerate(_PREVIEW_COLUMNS):
                value = row[column]
                fmt = _PREVIEW_FORMATS.get(column)
                _set_cell(self.table, r, c, fmt.format(value) if fmt and value == value else str(value))
        self.table.selectRow(0)
        self.completeChanged.emit()

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
