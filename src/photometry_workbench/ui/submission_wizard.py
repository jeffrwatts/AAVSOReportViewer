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
    QScrollArea,
    QSizePolicy,
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
    ErrorMethod,
    Submission,
    SubmissionError,
    build_submission,
    choose_method,
    frame_consistency,
)
from photometry_workbench.transform.engine import untransformable_reason
from photometry_workbench.ui import coloring
from photometry_workbench.ui.example_diagrams import (
    combine_diagram,
    light_curve_diagram,
    steady_diagram,
    varies_diagram,
)

PAGE_SOURCE, PAGE_COMBINE, PAGE_VARIES, PAGE_LIGHT_CURVE, PAGE_PREVIEW = range(5)

_CAPTION_WIDTH = 540

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


def _card_row(cards: list["_ChoiceCard"], page: QWizardPage) -> tuple[QWidget, QButtonGroup]:
    """Cards side by side, equal in height and no taller than the tallest needs, as one
    choice (button ids in order)."""
    row = QWidget()
    row.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(12)
    group = QButtonGroup(page)
    for i, card in enumerate(cards):
        group.addButton(card.radio, i)
        layout.addWidget(card, 1)
    group.idToggled.connect(lambda *_: page.completeChanged.emit())
    return row, group


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


def _titled_table(
    parent_layout: QVBoxLayout, title: str, columns: list[str], caption: str = ""
) -> tuple[QWidget, QTableWidget]:
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(QLabel(title))
    if caption:
        # A fixed width lets the wrapped caption claim its full height; beside another
        # table in a row, Qt would otherwise squeeze it.
        label = _wrapped(caption, gray=True)
        label.setFixedWidth(_CAPTION_WIDTH)
        label.setMinimumHeight(label.heightForWidth(_CAPTION_WIDTH))
        layout.addWidget(label)
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
        combine = _ChoiceCard(
            "One observation per filter",
            combine_diagram(),
            "Frames taken close together, averaged into a single point; their scatter gives the "
            "uncertainty. For a supernova or a slow variable.",
        )
        light_curve = _ChoiceCard(
            "A light curve",
            light_curve_diagram(),
            "Every frame reported as its own point, to follow the star's changes through the "
            "session. For a short-period pulsator or an eclipse.",
        )
        row, self.choice = _card_row([combine, light_curve], self)
        layout.addWidget(row)
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
        self.setTitle("Will the target change during these frames?")
        layout = QVBoxLayout(self)
        steady = _ChoiceCard(
            "Steady during these frames",
            steady_diagram(),
            "The target holds its brightness while these frames are taken, so its own scatter is "
            "the uncertainty. For a supernova or a slow variable.",
        )
        varies = _ChoiceCard(
            "May change during these frames",
            varies_diagram(),
            "The target may brighten or fade while these frames are taken, so its scatter would "
            "include that change. The check star's scatter is the uncertainty instead. For a fast "
            "pulsator or a flaring star.",
        )
        row, self.choice = _card_row([steady, varies], self)
        layout.addWidget(row)
        self.decide = _wrapped("")
        layout.addWidget(self.decide)
        layout.addStretch()

    def initializePage(self):
        df = self._wizard.source_report().df
        minutes = (df["DATE"].max() - df["DATE"].min()) * 1440
        self.decide.setText(
            "Decide from what you know about the star: do you expect it to change over the "
            f"{minutes:.1f} minutes these frames span? A supernova or a slow variable doesn't; a "
            "fast pulsator or a flaring star can."
        )

    def isComplete(self):
        return self.choice.checkedId() >= 0

    def varies(self) -> bool:
        return self.choice.checkedId() == 1


class _LightCurvePage(QWizardPage):
    def __init__(self, wizard: "SubmissionWizard"):
        super().__init__()
        self._wizard = wizard
        self.setTitle("The uncertainty for each frame")
        layout = QVBoxLayout(self)
        self.text = _wrapped("")
        layout.addWidget(self.text)
        self.spread_caption = _wrapped("Typical values in this session (median over the frames):", gray=True)
        layout.addWidget(self.spread_caption)
        self.spread_table = _read_only_table(
            ["Filter", "Comparison stars", "Spread between comps", "SNR error", "Reported uncertainty"]
        )
        layout.addWidget(self.spread_table)
        self.problem = _wrapped("")
        self.problem.setStyleSheet(f"color: {coloring.FLAG.name()};")
        layout.addWidget(self.problem)
        layout.addWidget(_wrapped("Nothing to choose here. Next shows the rows that will be saved.", gray=True))
        layout.addStretch()

    def initializePage(self):
        method = self._wizard.method()
        ensemble = method == ErrorMethod.COMP_SPREAD
        transformed = self._wizard.source_report().is_transformed
        if ensemble:
            n = len(self._wizard.session.comp_star_names())
            self.text.setText(
                f"Each of the {n} comparison stars gives its own estimate of the target's magnitude in "
                "every frame. How much those estimates disagree measures the uncertainty directly: a "
                "frame where the comparison stars agree closely gets a small uncertainty, and one where "
                "they disagree gets a larger one.\n\n"
                "That spread is combined with the target's SNR error (the two added in quadrature) to "
                "give the uncertainty reported for the frame. So there's always some uncertainty: even "
                "when the comparison stars agree perfectly, the target's own SNR error remains."
            )
        else:
            self.text.setText(
                "With one comparison star there's one estimate of the target per frame, so each frame's "
                "uncertainty is its SNR-based error from VPhot"
                + (", carried through the transform." if transformed else ".")
            )
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
        self.setTitle("Check the report before saving")
        self.setFinalPage(True)
        # The page scrolls in a small window instead of squeezing its tables together.
        body = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(body)
        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.addWidget(scroll)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(9, 0, 9, 0)  # line up with the page title
        self.intro = _wrapped("")
        layout.addWidget(self.intro)
        self.method_label = _wrapped("", gray=True)
        layout.addWidget(self.method_label)
        self.warning_label = _wrapped("")
        self.warning_label.setStyleSheet(f"color: {coloring.BORDERLINE.name()};")
        layout.addWidget(self.warning_label)
        self.table = _read_only_table(_PREVIEW_COLUMNS)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.itemSelectionChanged.connect(self._show_notes)
        layout.addWidget(self.table, 2)
        layout.addWidget(QLabel("NOTES for the selected row: how it was measured and transformed"))
        self.notes = QPlainTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setFixedHeight(90)
        layout.addWidget(self.notes)
        checks = QVBoxLayout()
        checks.setSpacing(16)
        layout.addLayout(checks)
        # The consistency check for a combined point: the Std across frames should be about
        # the same as, or lower than, what a single frame's uncertainty predicts.
        self.consistency_box, self.consistency_table = _titled_table(
            checks,
            "Do the frames agree as well as their SNR predicts?",
            ["Filter", "One frame's SNR error", "Scatter across frames", "Ratio"],
            caption=(
                "One frame's SNR error is the uncertainty a single frame would have (median over "
                "the frames). The scatter across frames is the uncertainty being reported. About 1x "
                "means the frames agree as well as their SNR allows. Higher means some extra "
                "scatter, from thin cloud, a bad frame, or (after a transform) each frame's own "
                "color noise. It's already in the reported uncertainty."
            ),
        )
        self.consistency_box.setMaximumWidth(_CAPTION_WIDTH + 20)
        # How much the transformed magnitudes depend on the transform coefficients'
        # own errors, judged against the MERR being submitted. It's a systematic
        # uncertainty, so it's shown for reference and not added to MERR.
        self.coeff_box, self.coeff_table = _titled_table(
            checks,
            "Transform coefficient uncertainty (not included in the reported uncertainty)",
            ["Filter", "Reported uncertainty", "Shift from coefficients"],
            caption=(
                "How far the magnitudes would move if the transform coefficients were off by their "
                "own errors. That shift is the same for every frame, a systematic offset rather than "
                "random scatter, so it isn't added to each frame's uncertainty. Green: smaller than "
                "the reported uncertainty."
            ),
        )
        self.coeff_box.setMaximumWidth(_CAPTION_WIDTH + 20)
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
            self.intro.setText(f"Can't build the report: {exc}")
            self.method_label.clear()
            self.warning_label.clear()
            self.completeChanged.emit()
            return
        rows = "one per filter" if self._wizard.combine_page.combine() else "one per frame and filter"
        self.intro.setText(
            f"These are the rows that will be saved ({rows}), in the AAVSO Extended Format that WebObs "
            "accepts. MAG is the magnitude and MERR its uncertainty. Click a row to see its NOTES."
        )
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
        if len(df) <= 10:
            _fit_height(self.table)  # a combined point's few rows, without empty space
        else:
            self.table.setMinimumHeight(200)  # about five rows, however small the window
            self.table.setMaximumHeight(16777215)
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
        self.coeff_table.setHorizontalHeaderLabels(
            ["Filter", "Reported uncertainty (median)" if light_curve else "Reported uncertainty", "Shift from coefficients"]
        )
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
            source = "the spread across the comparison stars combined with the SNR uncertainty"
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
                "About 1× or lower: the frames agree as well as their SNR allows. 1.5× or more: extra "
                "scatter worth a look (thin cloud, a bad frame, or each frame's color noise after a "
                "transform). It's already included in the reported uncertainty."
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
        width, height = 980, 900
        screen = QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            width, height = min(width, available.width() - 80), min(height, available.height() - 80)
        self.resize(width, height)
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
