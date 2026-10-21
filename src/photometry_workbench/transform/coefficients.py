"""Transform coefficients, as produced by AAVSO's Transform Generator (TG)."""

from __future__ import annotations

import configparser
import re
from dataclasses import dataclass, field
from pathlib import Path

# TransformApplier echoes the coefficients it used into its output's header as
# '#Tbv= 1.5830, +/- 0.0170' -- the same values TG wrote to its .ini file.
_HEADER_COEFF = re.compile(r"^#(T\w+)=\s*([-\d.]+),\s*\+/-\s*([-\d.]+)")
_HEADER_DESC = re.compile(r"^#\s*desc:\s*(.*)$")


@dataclass(frozen=True)
class TransformCoefficients:
    values: dict[str, float]
    """e.g. {'Tbv': 1.583, 'Tb_bv': 0.336, ...}"""
    errors: dict[str, float] = field(default_factory=dict)
    description: str = ""
    """TG's own description line, e.g. 'TG - Version 6.9a, Telescope= C8 VIS, ...'."""
    source: str = ""
    """File name the coefficients were read from, for display and provenance notes."""

    def value(self, name: str) -> float:
        try:
            return self.values[name]
        except KeyError:
            raise KeyError(f"Transform coefficient {name} is missing from {self.source or 'the coefficients'}") from None

    def error(self, name: str) -> float | None:
        return self.errors.get(name)

    def header_lines(self) -> list[str]:
        """The coefficients as TA-style '#Tx= v, +/- e' comment lines."""
        lines = [f"#      desc: {self.description}"] if self.description else []
        for name, value in self.values.items():
            err = self.errors.get(name)
            err_text = f"{err:.4f}" if err is not None else "na"
            lines.append(f"#{name}= {value:.4f}, +/- {err_text}")
        return lines


def parse_tg_ini(path: str | Path) -> TransformCoefficients:
    """Parse a TG coefficients file: [Setup] description=..., [Coefficients] Tbv=...,
    [Error] Tbv=..."""
    path = Path(path)
    parser = configparser.ConfigParser()
    parser.optionxform = str  # coefficient names are case-sensitive (Tb_bv vs Tbv)
    parser.read_string(path.read_text())
    if not parser.has_section("Coefficients"):
        raise ValueError(f"{path.name} has no [Coefficients] section -- not a TG coefficients file?")
    values = {k: float(v) for k, v in parser.items("Coefficients")}
    errors = {k: float(v) for k, v in parser.items("Error")} if parser.has_section("Error") else {}
    description = parser.get("Setup", "description", fallback="").strip()
    return TransformCoefficients(values, errors, description, path.name)


def parse_report_header(path: str | Path) -> TransformCoefficients:
    """Recover the coefficients a TransformApplier report was produced with."""
    path = Path(path)
    values: dict[str, float] = {}
    errors: dict[str, float] = {}
    description = ""
    for line in path.read_text().splitlines():
        if m := _HEADER_COEFF.match(line):
            values[m[1]] = float(m[2])
            errors[m[1]] = float(m[3])
        elif not description and (m := _HEADER_DESC.match(line)):
            description = m[1].strip()
    if not values:
        raise ValueError(f"No transform coefficients found in {path.name}")
    return TransformCoefficients(values, errors, description, path.name)
