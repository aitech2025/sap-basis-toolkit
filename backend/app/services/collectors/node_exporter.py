"""Bounded, read-only Prometheus node_exporter collector.

Only a small allowlist of numeric host signals is parsed. The scrape is performed against a
policy-resolved IP while preserving TLS hostname verification; arbitrary labels and payloads
are not persisted.
"""
import http.client
import re
import ssl
from datetime import datetime, timezone

from ... import config
from ..target_policy import TargetPolicyError, resolve_approved_addresses
from . import CollectorError
from .sapcontrol import _PinnedHTTPSConnection

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
SAMPLE_RE = re.compile(r'^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{([^}]*)\})?\s+([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)$')
LABEL_RE = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:\\.|[^"])*)"')


def parse_metrics(payload: str) -> dict:
    """Parse only the numeric node_exporter samples needed for host-level RCA."""
    values = {}
    filesystems = []
    for line in payload.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = SAMPLE_RE.match(line)
        if not match:
            continue
        name, raw_labels, raw_value = match.groups()
        try:
            value = float(raw_value)
        except ValueError:
            continue
        labels = dict(LABEL_RE.findall(raw_labels or ""))
        if name == "node_load1":
            values["host.load1"] = value
        elif name in ("node_memory_MemTotal_bytes", "node_memory_MemAvailable_bytes", "node_memory_SwapTotal_bytes",
                      "node_memory_SwapFree_bytes"):
            values[name] = value
        elif name == "node_cpu_seconds_total":
            key = "cpu_idle" if labels.get("mode") in ("idle", "iowait") else "cpu_busy"
            values[key] = values.get(key, 0.0) + value
        elif name in ("node_filesystem_size_bytes", "node_filesystem_avail_bytes"):
            if labels.get("mountpoint") == "/" and labels.get("fstype") not in ("tmpfs", "overlay", "squashfs"):
                filesystems.append((name, value))
    total = values.get("node_memory_MemTotal_bytes")
    available = values.get("node_memory_MemAvailable_bytes")
    if total and available is not None and total > 0:
        values["host.memory_used_pct"] = max(0.0, min(100.0, (total - available) / total * 100))
    swap_total, swap_free = values.get("node_memory_SwapTotal_bytes"), values.get("node_memory_SwapFree_bytes")
    if swap_total and swap_free is not None:
        values["host.swap_used_pct"] = max(0.0, min(100.0, (swap_total - swap_free) / swap_total * 100))
    fs = dict(filesystems)
    if fs.get("node_filesystem_size_bytes", 0) > 0 and "node_filesystem_avail_bytes" in fs:
        values["host.disk_used_pct"] = max(
            0.0, min(100.0, (fs["node_filesystem_size_bytes"] - fs["node_filesystem_avail_bytes"])
                    / fs["node_filesystem_size_bytes"] * 100))
    out = {k: round(v, 3) for k, v in values.items() if k.startswith("host.")}
    if "cpu_busy" in values:
        out["_cpu_counters"] = (values["cpu_busy"], values.get("cpu_idle", 0.0))
    return out


_previous_cpu: dict[str, tuple[float, float]] = {}


def cpu_from_counters(target: str, counters: tuple[float, float] | None) -> float | None:
    """CPU % between this scrape and the previous one of the same target (node_cpu_seconds_total is a counter)."""
    if counters is None:
        return None
    previous = _previous_cpu.get(target)
    _previous_cpu[target] = counters
    if previous is None:
        return None
    busy, idle = counters[0] - previous[0], counters[1] - previous[1]
    if busy < 0 or idle < 0 or busy + idle <= 0:  # counter reset (reboot)
        return None
    return round(busy / (busy + idle) * 100, 1)


def collect(host: str, port: int, use_tls: bool, verify_tls: bool) -> dict:
    try:
        addresses = resolve_approved_addresses(host, port, check_port=False)
    except TargetPolicyError as exc:
        raise CollectorError(f"Blocked by outbound policy: {exc}") from exc
    last_error = None
    for address in addresses:
        conn = None
        try:
            if use_tls:
                context = ssl.create_default_context()
                if not verify_tls:
                    context.check_hostname = False
                    context.verify_mode = ssl.CERT_NONE
                conn = _PinnedHTTPSConnection(address, port, host, context, config.COLLECTOR_TIMEOUT_SECONDS)
            else:
                conn = http.client.HTTPConnection(address, port, timeout=config.COLLECTOR_TIMEOUT_SECONDS)
            conn.request("GET", "/metrics", headers={"Host": f"{host}:{port}", "Accept": "text/plain"})
            response = conn.getresponse()
            if response.status != 200:
                raise CollectorError(f"node_exporter returned HTTP {response.status}")
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise CollectorError("node_exporter response exceeds 2 MiB limit")
            metrics = parse_metrics(body.decode("utf-8", "replace"))
            cpu = cpu_from_counters(f"{host}:{port}", metrics.pop("_cpu_counters", None))
            if cpu is not None:
                metrics["host.cpu_pct"] = cpu
            if not metrics:
                raise CollectorError("No supported node_exporter metrics found")
            return {"metrics": metrics, "collected_at": datetime.now(timezone.utc)}
        except (OSError, ssl.SSLError, http.client.HTTPException, CollectorError) as exc:
            last_error = exc
        finally:
            if conn:
                conn.close()
    raise CollectorError(f"node_exporter scrape failed: {last_error}")
