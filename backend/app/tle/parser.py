from __future__ import annotations

from pathlib import Path
from typing import List

from pydantic import BaseModel, field_validator


class TLEEntry(BaseModel):
    """Represents a parsed Two-Line Element set entry."""

    name: str
    norad_id: int
    classification: str
    launch_year: int
    launch_number: int
    inclination: float
    raan: float
    eccentricity: float
    arg_perigee: float
    mean_anomaly: float
    mean_motion: float
    epoch_year: int
    epoch_day: float
    line1: str
    line2: str

    @field_validator("classification")
    @classmethod
    def _validate_classification(cls, v: str) -> str:
        if v not in ("U", "S"):
            raise ValueError(f"classification must be 'U' or 'S', got '{v}'")
        return v


def _compute_checksum(line: str) -> int:
    """Compute TLE line checksum (modulo 10 of sum of all digits, minus signs count as 1)."""
    total = 0
    for ch in line[:-1]:
        if ch.isdigit():
            total += int(ch)
        elif ch == "-":
            total += 1
    return total % 10


def _validate_line(line: str, expected_line_number: int, strict: bool = True) -> None:
    """Validate a TLE line's format and optionally its checksum."""
    if len(line) < 69:
        raise ValueError(
            f"Line {expected_line_number} too short ({len(line)} chars, expected 69): {line}"
        )
    if line[0] != str(expected_line_number):
        raise ValueError(
            f"Expected line number {expected_line_number}, got '{line[0]}': {line}"
        )
    if strict:
        computed = _compute_checksum(line)
        stored = int(line[68])
        if computed != stored:
            raise ValueError(
                f"Checksum mismatch on line {expected_line_number}: "
                f"computed {computed}, stored {stored}: {line}"
            )


def _parse_line1(line: str) -> dict:
    """Extract fields from TLE line 1."""
    two_digit_year = int(line[9:11])
    launch_year = 1900 + two_digit_year if two_digit_year >= 57 else 2000 + two_digit_year
    return {
        "norad_id": int(line[2:7]),
        "classification": line[7],
        "launch_year": launch_year,
        "launch_number": int(line[11:14]),
        "epoch_year": int(line[18:20]),
        "epoch_day": float(line[20:32]),
    }


def _parse_line2(line: str) -> dict:
    """Extract fields from TLE line 2."""
    return {
        "inclination": float(line[8:16]),
        "raan": float(line[17:25]),
        "eccentricity": float("0." + line[26:33].strip()),
        "arg_perigee": float(line[34:42]),
        "mean_anomaly": float(line[43:51]),
        "mean_motion": float(line[52:63]),
    }


def _parse_tle_lines(lines: List[str], strict: bool = True) -> List[TLEEntry]:
    """Parse a list of non-empty, stripped TLE lines into TLEEntry objects."""
    entries: List[TLEEntry] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("1 ") or line.startswith("2 "):
            if not line.startswith("1 "):
                raise ValueError(f"Expected line 1 starting with '1 ', got: {line}")
            if i + 1 >= len(lines):
                raise ValueError(f"Line 1 found without following line 2: {line}")
            line1 = lines[i]
            line2 = lines[i + 1]
            name = f"UNKNOWN-{line1[2:7]}"
            i += 2
        elif line.startswith("0 ") or not line[0].isdigit():
            name = line[2:].strip() if line.startswith("0 ") else line.strip()
            if i + 2 >= len(lines):
                raise ValueError(f"Name line '{name}' without complete TLE pair")
            line1 = lines[i + 1]
            line2 = lines[i + 2]
            if not line1.startswith("1 ") or not line2.startswith("2 "):
                raise ValueError(
                    f"Expected '1 ' and '2 ' lines after name '{name}'"
                )
            i += 3
        else:
            raise ValueError(f"Unrecognized TLE line: {line}")

        _validate_line(line1, 1, strict=strict)
        _validate_line(line2, 2, strict=strict)

        l1_fields = _parse_line1(line1)
        l2_fields = _parse_line2(line2)

        entries.append(
            TLEEntry(
                name=name,
                line1=line1,
                line2=line2,
                **l1_fields,
                **l2_fields,
            )
        )

    return entries


def parse_tle_file(filepath: str, strict: bool = True) -> List[TLEEntry]:
    """Read a TLE file and return all parsed entries.

    Args:
        filepath: Path to the TLE file.
        strict: If True, validate checksums on every line. Set to False
                for sample or generated TLE data that may have incorrect checksums.
    """
    path = Path(filepath)
    content = path.read_text(encoding="utf-8")
    return parse_tle_string(content, strict=strict)


def parse_tle_string(content: str, strict: bool = True) -> List[TLEEntry]:
    """Parse TLE data from a raw string and return all entries.

    Args:
        content: Raw TLE text content.
        strict: If True, validate checksums on every line. Set to False
                for sample or generated TLE data that may have incorrect checksums.
    """
    lines = [ln.strip() for ln in content.strip().splitlines() if ln.strip()]
    if not lines:
        return []
    return _parse_tle_lines(lines, strict=strict)


def classify_orbit(orbital_period_minutes: float) -> str:
    """Classify an orbit based on its period in minutes."""
    if orbital_period_minutes < 128:
        return "LEO"
    if orbital_period_minutes <= 1200:
        return "MEO"
    if 1380 <= orbital_period_minutes <= 1500:
        return "GEO"
    return "HEO"


def get_orbit_info(entry: TLEEntry) -> dict:
    """Return orbit classification, estimated altitude range, and orbital period for a TLE entry."""
    orbital_period_minutes = 1440.0 / entry.mean_motion
    classification = classify_orbit(orbital_period_minutes)
    if entry.eccentricity >= 0.25 and classification == "MEO":
        classification = "HEO"

    mu = 398600.4418
    n_rad_s = entry.mean_motion * 2.0 * 3.141592653589793 / 86400.0
    semi_major_axis = (mu / (n_rad_s ** 2)) ** (1.0 / 3.0)
    earth_radius = 6371.0
    perigee_alt = semi_major_axis * (1.0 - entry.eccentricity) - earth_radius
    apogee_alt = semi_major_axis * (1.0 + entry.eccentricity) - earth_radius
    altitude_range = (
        round(max(perigee_alt, 0.0)),
        round(max(apogee_alt, 0.0)),
    )

    return {
        "classification": classification,
        "orbital_period_minutes": round(orbital_period_minutes, 4),
        "altitude_range_km": altitude_range,
    }
