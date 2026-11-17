"""Tolerant parsing of SAP table exports (SE16/SE16N, SUIM, ST03N, DB02, HANA SQL console).

SAP GUI "Spreadsheet"/"Unconverted" downloads vary by locale: comma, semicolon,
tab or pipe delimiters, padded columns, and dates as YYYYMMDD, DD.MM.YYYY or ISO.
"""
import csv
import io
import re
from datetime import date, datetime, time, timedelta, timezone, tzinfo

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


def parse_sap_time(value: str) -> time | None:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 4:
        digits += "00"
    if len(digits) != 6:
        return None
    try:
        return time(int(digits[:2]), int(digits[2:4]), int(digits[4:6]))
    except ValueError:
        return None


def parse_sap_datetime(date_value: str, time_value: str = "", zone: tzinfo = timezone.utc) -> datetime | None:
    """Combine SAP date/time fields (or a 14-digit timestamp such as TPALOG TRTIME) into an aware UTC datetime."""
    raw = (date_value or "").strip()
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 14 and raw.isdigit():
        d, t = parse_sap_date(digits[:8]), parse_sap_time(digits[8:])
    else:
        d, t = parse_sap_date(raw[:10]), parse_sap_time(time_value) or (parse_sap_time(raw[11:]) if len(raw) > 10 else None)
    if d is None:
        return None
    return datetime.combine(d, t or time(0, 0), tzinfo=zone).astimezone(timezone.utc)


def parse_duration_seconds(value: str) -> int | None:
    """'3600', '01:00:00' or '1.234' (thousand separators) to seconds."""
    v = (value or "").strip()
    if not v:
        return None
    if ":" in v:
        parts = [int(p) for p in v.split(":") if p.isdigit()]
        total = 0
        for part in parts:
            total = total * 60 + part
        return total
    if re.fullmatch(r"\d{1,3}([.,\s]\d{3})+", v):  # whole seconds with thousands separators (1.234 / 1,234)
        return int(re.sub(r"\D", "", v))
    number = parse_number(v)
    return int(number) if number is not None else None


def local_time_before(reference: datetime, t: time, zone: tzinfo) -> datetime:
    """Latest datetime <= reference (in the system zone) whose clock time is t; used when SM12 shows only a time."""
    local_ref = reference.astimezone(zone)
    candidate = datetime.combine(local_ref.date(), t, tzinfo=zone)
    if candidate > local_ref:
        candidate -= timedelta(days=1)
    return candidate.astimezone(timezone.utc)
