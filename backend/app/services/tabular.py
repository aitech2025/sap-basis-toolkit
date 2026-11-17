"""Tolerant parsing of SAP table exports (SE16/SE16N, SUIM, ST03N, DB02, HANA SQL console).

SAP GUI "Spreadsheet"/"Unconverted" downloads vary by locale: comma, semicolon,
tab or pipe delimiters, padded columns, and dates as YYYYMMDD, DD.MM.YYYY or ISO.
"""
import csv
import io
import re
from datetime import date, datetime

DELIMITERS = [",", ";", "\t", "|"]


class InputFormatError(ValueError):
    pass


def _norm(header: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", header.strip().upper())


def decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            text = raw.decode(encoding)
            if "\x00" not in text:
                return text
        except UnicodeDecodeError:
            continue
    raise InputFormatError("Unable to decode upload; export as UTF-8 or ANSI text")


def read_rows(raw: bytes | str) -> list[dict]:
    """Return rows as dicts keyed by normalised header (upper-case alphanumerics)."""
    text = decode(raw) if isinstance(raw, bytes) else raw
    # SAP list outputs wrap tables in dashed lines; drop them.
    lines = [line for line in text.splitlines() if line.strip() and not re.fullmatch(r"[-=|+\s]+", line)]
    if not lines:
        raise InputFormatError("Upload is empty")
    header_line = lines[0]
    delimiter = max(DELIMITERS, key=header_line.count)
    if header_line.count(delimiter) == 0:
        raise InputFormatError("Could not detect a delimiter (comma, semicolon, tab or pipe)")
    reader = csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter)
    rows = list(reader)
    headers = [_norm(h) for h in rows[0]]
    result = []
    for values in rows[1:]:
        if not any(v.strip() for v in values):
            continue
        result.append({h: (values[i].strip() if i < len(values) else "") for i, h in enumerate(headers) if h})
    return result


def pick(row: dict, *aliases: str, default: str = "") -> str:
    for alias in aliases:
        value = row.get(_norm(alias))
        if value not in (None, ""):
            return value
    return default


def require_columns(rows: list[dict], groups: dict[str, tuple[str, ...]]) -> None:
    if not rows:
        raise InputFormatError("Upload contains a header but no data rows")
    present = set(rows[0].keys())
    missing = [name for name, aliases in groups.items() if not any(_norm(a) in present for a in aliases)]
    if missing:
        raise InputFormatError(
            "Missing required column(s): " + "; ".join(
                f"{name} (accepted headers: {', '.join(groups[name])})" for name in missing
            )
        )


def parse_number(value: str) -> float | None:
    value = (value or "").strip().replace(" ", "")
    if not value:
        return None
    # 1.234.567,89 (DE) vs 1,234,567.89 (EN)
    if "," in value and "." in value:
        value = value.replace(".", "").replace(",", ".") if value.rfind(",") > value.rfind(".") else value.replace(",", "")
    elif "," in value:
        value = value.replace(",", ".") if len(value.split(",")[-1]) != 3 else value.replace(",", "")
    try:
        return float(value)
    except ValueError:
        return None


def parse_sap_date(value: str) -> date | None:
    value = (value or "").strip()
    if not value or set(value) <= {"0", ".", "-", "/"}:
        return None  # 00000000 = never
    for fmt in ("%Y%m%d", "%d.%m.%Y", "%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(value[:10], fmt).date()
        except ValueError:
            continue
    return None


def to_csv(rows: list[dict]) -> str:
    if not rows:
        return ""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()
