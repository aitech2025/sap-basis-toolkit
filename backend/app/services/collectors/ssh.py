"""Read-only OS inventory over SSH (root or <sid>adm).

Only fixed, read-only commands are executed. Every value that is interpolated into a command (SID,
instance directory) is validated against a strict pattern first; nothing the user types reaches the
shell. The connection goes to the policy-approved, pre-resolved IP address, and the host key is pinned
on first use (TOFU): later connections fail if the key changes.

What is read
- OS layer: /etc/os-release, uname, hostname, CPU, memory, uptime, virtualisation, timezone, IPs and
  the SAP-relevant file systems.
- SAP layer: /usr/sap/sapservices (every sapstartsrv on the host with its profile), the instance
  profiles and DEFAULT.PFL (SAPDBHOST, dbms/type, message server host).
- Database layer: HANA manifests (version), saphostctrl ListDatabases when running as root, and the
  usual Oracle / Db2 / ASE / MaxDB directories for non-HANA databases.
"""
import base64
import hashlib
import io
import re
import socket
from dataclasses import dataclass

from ... import config
from ..target_policy import TargetPolicyError, resolve_approved_addresses
from . import CollectorError, simulator

SID_RE = re.compile(r"^[A-Z][A-Z0-9]{2}$")
INSTANCE_DIR_RE = re.compile(r"^(ASCS|SCS|ERS|DVEBMGS|D|J|W|G|SMDA|HDB)\d{2}$")
PROFILE_RE = re.compile(r"pf=(/[\w./-]+/profile/([A-Z][A-Z0-9]{2})_([A-Z]+)(\d{2})_([\w.-]+))")
MAX_OUTPUT = 512 * 1024

INVENTORY_SCRIPT = r"""
echo '@@os-release'; cat /etc/os-release 2>/dev/null
echo '@@uname'; uname -srm 2>/dev/null
echo '@@hostname'; hostname 2>/dev/null; hostname -f 2>/dev/null
echo '@@ip'; hostname -I 2>/dev/null
echo '@@cpu'; nproc 2>/dev/null; grep -m1 'model name' /proc/cpuinfo 2>/dev/null
echo '@@mem'; grep -E '^(MemTotal|MemAvailable|SwapTotal|SwapFree):' /proc/meminfo 2>/dev/null
echo '@@load'; cat /proc/loadavg 2>/dev/null
echo '@@cpustat'; head -1 /proc/stat 2>/dev/null; sleep 1; head -1 /proc/stat 2>/dev/null
echo '@@uptime'; cat /proc/uptime 2>/dev/null
echo '@@virt'; (systemd-detect-virt 2>/dev/null || echo unknown)
echo '@@tz'; date +%Z%z 2>/dev/null
echo '@@df'; df -P / /usr/sap /sapmnt /hana/data /hana/log /hana/shared /oracle /db2 /sybase /sapdb 2>/dev/null
echo '@@whoami'; id -un 2>/dev/null
echo '@@sapservices'; cat /usr/sap/sapservices 2>/dev/null
echo '@@usrsap'; ls -1d /usr/sap/[A-Z][A-Z0-9][A-Z0-9] 2>/dev/null
echo '@@procs'; ps -eo stat=,comm= 2>/dev/null | awk '$1 !~ /^Z/ {print $2}' | sort -u | grep -E '^(hdbindexserver|hdbnameserver|hdbdaemon|ora_pmon_|ora_smon_|db2sysc|dataserver|sqlservr|sapstartsrv|disp\+work|msg_server|enq_server|enserver|enq_replicator|enrepserver|jstart|icman|gwrd|saphostexec|sapdbctrl)'
echo '@@dbdirs'; ls -1d /oracle/[A-Z][A-Z0-9][A-Z0-9]/[0-9]* /db2/db2[a-z][a-z0-9][a-z0-9] /sybase/[A-Z][A-Z0-9][A-Z0-9]/ASE-* /sapdb/[A-Z][A-Z0-9][A-Z0-9] 2>/dev/null
echo '@@end'
"""


@dataclass
class SshTarget:
    host: str
    port: int = 22
    username: str = ""
    password: str | None = None
    private_key: str | None = None
    expected_host_key: str | None = None


def fingerprint(key_bytes: bytes) -> str:
    return "SHA256:" + base64.b64encode(hashlib.sha256(key_bytes).digest()).decode().rstrip("=")


def validate_target(target: SshTarget) -> None:
    allowed = {int(p) for p in str(config.SSH_ALLOWED_PORTS).split(",") if p.strip()}
    if target.port not in allowed and not simulator.is_sim(target.host):
        raise CollectorError(f"SSH port {target.port} is not allowed (SSH_ALLOWED_PORTS={config.SSH_ALLOWED_PORTS})")
    if not target.username or not re.match(r"^[a-z_][a-z0-9_.-]{0,31}$", target.username):
        raise CollectorError("Enter the OS user, e.g. root or s4hadm")
    if not (target.password or target.private_key):
        raise CollectorError("Enter the OS password or a private key")


class Session:
    """One SSH connection; `run` executes a fixed command and returns its stdout."""

    def __init__(self, target: SshTarget):
        validate_target(target)
        self.target = target
        self.sim = simulator.is_sim(target.host)
        self.host_key = None
        if self.sim:
            if not config.SIMULATOR_ENABLED:
                raise CollectorError("The demo simulator is disabled (SIMULATOR_ENABLED=false)")
            if target.password == "wrong":
                raise CollectorError("SSH authentication failed for simulated host")
            self.host_key = simulator.ssh_host_key(target.host)
            self._check_key()
            return
        import paramiko  # imported lazily: only needed when SSH discovery is used

        try:
            address = resolve_approved_addresses(target.host, target.port, check_port=False)[0]
        except TargetPolicyError as exc:
            raise CollectorError(f"Blocked by outbound policy: {exc}") from exc
        try:
            sock = socket.create_connection((address, target.port), timeout=config.SSH_TIMEOUT_SECONDS)
        except OSError as exc:
            raise CollectorError(f"Cannot reach {target.host}:{target.port}: {exc}") from exc
        self.transport = paramiko.Transport(sock)
        try:
            self.transport.start_client(timeout=config.SSH_TIMEOUT_SECONDS)
            key = self.transport.get_remote_server_key()
            self.host_key = fingerprint(key.asbytes())
            self._check_key()
            if target.private_key:
                pkey = None
                for cls in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
                    try:
                        pkey = cls.from_private_key(io.StringIO(target.private_key), password=target.password or None)
                        break
                    except Exception:  # noqa: BLE001 - try the next key type
                        continue
                if pkey is None:
                    raise CollectorError("The private key could not be read (Ed25519, ECDSA or RSA in OpenSSH/PEM format)")
                self.transport.auth_publickey(target.username, pkey)
            else:
                try:
                    self.transport.auth_password(target.username, target.password)
                except paramiko.BadAuthenticationType:
                    self.transport.auth_interactive_dumb(target.username)
        except paramiko.AuthenticationException as exc:
            self.close()
            raise CollectorError(f"SSH authentication failed for {target.username}@{target.host}") from exc
        except paramiko.SSHException as exc:
            self.close()
            raise CollectorError(f"SSH error on {target.host}: {exc}") from exc

    def _check_key(self) -> None:
        if self.target.expected_host_key and self.target.expected_host_key != self.host_key:
            self.close()
            raise CollectorError(f"Host key of {self.target.host} changed (expected {self.target.expected_host_key}, "
                                 f"got {self.host_key}). Verify the server before trusting the new key.")

    def run(self, command: str) -> str:
        """Run a fixed script with /bin/sh. The script is sent on stdin, so it works whatever the login shell is
        (<sid>adm usually has csh/tcsh, which cannot parse VAR=value prefixes or multi-line quotes)."""
        if self.sim:
            return simulator.ssh_command(self.target.host, self.target.username, command)
        channel = self.transport.open_session(timeout=config.SSH_TIMEOUT_SECONDS)
        channel.settimeout(config.SSH_TIMEOUT_SECONDS)
        channel.exec_command("env LC_ALL=C /bin/sh -s")
        channel.sendall(command.encode() + b"\n")
        channel.shutdown_write()
        chunks, size = [], 0
        try:
            while True:
                data = channel.recv(65536)
                if not data:
                    break
                size += len(data)
                if size > MAX_OUTPUT:
                    break
                chunks.append(data)
        except socket.timeout as exc:
            raise CollectorError(f"Command timed out on {self.target.host}") from exc
        finally:
            channel.close()
        return b"".join(chunks).decode("utf-8", "replace")

    def close(self) -> None:
        transport = getattr(self, "transport", None)
        if transport is not None:
            transport.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


# ------------------------------------------------------------------------------------------- parsing
def sections(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        if line.startswith("@@"):
            current = line[2:].strip()
            out[current] = []
        elif current:
            out[current].append(line.rstrip())
    return out


def parse_profile(text: str) -> dict[str, str]:
    params = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        params[key.strip()] = value.strip()
    return params


def _kv(lines: list[str]) -> dict[str, str]:
    out = {}
    for line in lines:
        if "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip().strip('"')
    return out


def cpu_busy_pct(lines: list[str]) -> float | None:
    """CPU utilisation between two /proc/stat 'cpu' lines taken one second apart (as ST06/vmstat report it)."""
    samples = []
    for line in lines:
        parts = line.split()
        if len(parts) >= 5 and parts[0] == "cpu" and all(p.isdigit() for p in parts[1:]):
            values = [int(p) for p in parts[1:]]
            idle = values[3] + (values[4] if len(values) > 4 else 0)  # idle + iowait
            samples.append((sum(values[:8]), idle))
    if len(samples) < 2:
        return None
    total, idle = samples[-1][0] - samples[0][0], samples[-1][1] - samples[0][1]
    return round(max(0.0, min(100.0, (total - idle) / total * 100)), 1) if total > 0 else None


def parse_os(sec: dict[str, list[str]]) -> dict:
    rel = _kv(sec.get("os-release", []))
    uname = (sec.get("uname") or [""])[0].split()
    names = [x for x in sec.get("hostname", []) if x]
    cpu = sec.get("cpu", [])
    mem = {}
    for line in sec.get("mem", []):
        m = re.match(r"^(\w+):\s+(\d+)", line)
        if m:
            mem[m.group(1)] = int(m.group(2)) * 1024
    total, avail = mem.get("MemTotal"), mem.get("MemAvailable")
    load = (sec.get("load") or [""])[0].split()
    uptime = (sec.get("uptime") or [""])[0].split()
    disks = []
    for line in sec.get("df", [])[1:]:
        parts = line.split()
        if len(parts) >= 6 and parts[4].endswith("%"):
            disks.append({"mount": parts[5], "size_gb": round(int(parts[1]) / 1048576, 1),
                          "used_pct": int(parts[4][:-1])})
    seen, unique = set(), []
    for d in disks:
        if d["mount"] not in seen:
            seen.add(d["mount"])
            unique.append(d)
    model = next((c.split(":", 1)[1].strip() for c in cpu if c.startswith("model name")), None)
    swap_total, swap_free = mem.get("SwapTotal"), mem.get("SwapFree")
    return {
        "hostname": names[0] if names else None,
        "fqdn": names[1] if len(names) > 1 else (names[0] if names else None),
        "ip_addresses": (sec.get("ip") or [""])[0].split()[:8],
        "os_name": rel.get("NAME"), "os_version": rel.get("VERSION") or rel.get("VERSION_ID"),
        "os_pretty": rel.get("PRETTY_NAME"), "os_id": rel.get("ID"),
        "kernel": uname[1] if len(uname) > 1 else None, "arch": uname[2] if len(uname) > 2 else None,
        "os_family": uname[0] if uname else None,
        "cpu_count": int(cpu[0]) if cpu and cpu[0].isdigit() else None, "cpu_model": model,
        "memory_gb": round(total / 1024 ** 3, 1) if total else None,
        "memory_used_pct": round((total - avail) / total * 100, 1) if total and avail is not None else None,
        "swap_gb": round(mem["SwapTotal"] / 1024 ** 3, 1) if mem.get("SwapTotal") else 0,
        "swap_used_pct": round((swap_total - swap_free) / swap_total * 100, 1) if swap_total and swap_free is not None else None,
        "cpu_pct": cpu_busy_pct(sec.get("cpustat", [])),
        "load1": float(load[0]) if load and _num(load[0]) else None,
        "uptime_days": round(float(uptime[0]) / 86400, 1) if uptime and _num(uptime[0]) else None,
        "virtualization": ((sec.get("virt") or ["unknown"])[0] or "unknown").strip(),
        "timezone": (sec.get("tz") or [None])[0],
        "filesystems": unique[:12],
        "login_user": (sec.get("whoami") or [None])[0],
        "processes": sorted({p.strip() for p in sec.get("procs", []) if p.strip()})[:40],
    }


def _num(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def parse_sapservices(lines: list[str]) -> list[dict]:
    found, seen = [], set()
    for line in lines:
        if line.strip().startswith("#") and "sapstartsrv" not in line:
            continue
        for m in PROFILE_RE.finditer(line):
            path, sid, name, nr, host = m.groups()
            key = (sid, name, nr)
            if key in seen or not SID_RE.match(sid):
                continue
            seen.add(key)
            found.append({"sid": sid, "instance_name": f"{name}{nr}", "instance_nr": nr, "profile_host": host.lower(),
                          "profile": path})
    return found


def parse_db_dirs(lines: list[str]) -> list[dict]:
    out = []
    for line in lines:
        if m := re.match(r"^/oracle/([A-Z][A-Z0-9]{2})/(\d[\w.]*)$", line):
            out.append({"type": "ORACLE", "db_sid": m.group(1), "version": m.group(2)})
        elif m := re.match(r"^/db2/db2([a-z][a-z0-9]{2})$", line):
            out.append({"type": "DB2", "db_sid": m.group(1).upper(), "version": None})
        elif m := re.match(r"^/sybase/([A-Z][A-Z0-9]{2})/ASE-([\w.]+)$", line):
            out.append({"type": "ASE", "db_sid": m.group(1), "version": m.group(2).replace("_", ".")})
        elif m := re.match(r"^/sapdb/([A-Z][A-Z0-9]{2})$", line):
            out.append({"type": "MAXDB", "db_sid": m.group(1), "version": None})
    return out


def parse_hostctrl_databases(text: str) -> list[dict]:
    """saphostctrl -function ListDatabases output: 'Instance name: HDB00, Database name: S4H, Database type: hdb, ...'."""
    out = []
    for line in text.splitlines():
        fields = dict((k.strip().lower(), v.strip()) for k, _, v in
                      (part.partition(":") for part in line.split(",")) if v)
        if fields.get("database name") and fields.get("database type"):
            out.append({"instance": fields.get("instance name"), "name": fields["database name"],
                        "type": fields["database type"], "status": fields.get("database status")})
    return out


def inventory(target: SshTarget) -> dict:
    """OS, SAP instance and database facts for one host."""
    with Session(target) as session:
        sec = sections(session.run(INVENTORY_SCRIPT))
        os_info = parse_os(sec)
        instances = parse_sapservices(sec.get("sapservices", []))
        if not instances:  # no sapservices file: fall back to /usr/sap/<SID> directories
            for line in sec.get("usrsap", []):
                sid = line.rsplit("/", 1)[-1]
                if SID_RE.match(sid) and sid not in ("SYS", "TMP"):
                    instances.append({"sid": sid, "instance_name": None, "instance_nr": None, "profile_host": None, "profile": None})
        sids = sorted({i["sid"] for i in instances})
        profiles = {}
        for sid in sids:
            if not SID_RE.match(sid):
                continue
            text = session.run(f"cat /sapmnt/{sid}/profile/DEFAULT.PFL 2>/dev/null || cat /usr/sap/{sid}/SYS/profile/DEFAULT.PFL 2>/dev/null")
            profiles[sid] = parse_profile(text)
        hana_versions = {}
        for inst in instances:
            if inst["instance_name"] and inst["instance_name"].startswith("HDB") and INSTANCE_DIR_RE.match(inst["instance_name"]) \
                    and SID_RE.match(inst["sid"]):
                manifest = parse_profile(session.run(f"cat /usr/sap/{inst['sid']}/{inst['instance_name']}/exe/manifest 2>/dev/null")
                                         .replace(": ", "="))
                hana_versions[inst["sid"]] = manifest.get("fullversion") or manifest.get("release")
        hostctrl = []
        if os_info.get("login_user") == "root":
            hostctrl = parse_hostctrl_databases(session.run("/usr/sap/hostctrl/exe/saphostctrl -function ListDatabases 2>/dev/null"))
        return {"host": target.host, "host_key": session.host_key, "os": os_info, "instances": instances,
                "profiles": profiles, "hana_versions": hana_versions, "other_databases": parse_db_dirs(sec.get("dbdirs", [])),
                "hostctrl_databases": hostctrl}


DB_PROCESS = {"HANA": ("hdbindexserver",), "ORACLE": ("ora_pmon_",), "DB2": ("db2sysc",), "DB6Z": ("db2sysc",),
              "ASE": ("dataserver",), "MSSQL": ("sqlservr",)}


def database_running(os_info: dict, db_type: str | None) -> bool | None:
    """Whether the database's main process runs on this host (None when the type has no known process)."""
    prefixes = DB_PROCESS.get((db_type or "").upper())
    if not prefixes:
        return None
    return any(p.startswith(prefixes) for p in os_info.get("processes") or [])


APP_PROCESSES = ("disp+work", "msg_server", "enq_server", "enserver", "enq_replicator", "enrepserver", "jstart", "icman", "gwrd")
DB_PROCESSES = tuple(p for names in DB_PROCESS.values() for p in names)


def host_role(os_info: dict, instances: list[dict]) -> str:
    """Role of a host from its own evidence: SAP instances registered in sapservices and running processes.

    Registered instances count even when stopped, so a stopped database is still recognised as this host's database."""
    procs = os_info.get("processes") or []
    names = [i.get("instance_name") or "" for i in instances]
    db = any(n.startswith("HDB") for n in names) or any(p.startswith(DB_PROCESSES) for p in procs)
    app = any(n and not n.startswith("HDB") for n in names) or any(p in APP_PROCESSES for p in procs)
    if app and db:
        return "application + database"
    return "database" if db else "application"


def utilisation(target: SshTarget) -> dict:
    """Light refresh used by the os_ssh collector: OS facts, current utilisation and the host's own role evidence."""
    with Session(target) as session:
        sec = sections(session.run(INVENTORY_SCRIPT))
        os_info, instances = parse_os(sec), parse_sapservices(sec.get("sapservices", []))
        return {"host_key": session.host_key, "os": os_info, "instances": instances, "role": host_role(os_info, instances)}
