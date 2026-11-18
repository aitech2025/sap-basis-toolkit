"""Live data collectors (SAP Start Service / sapcontrol and SAP HANA SQL)."""
import re


class CollectorError(RuntimeError):
    pass


PORT_RULES = {
    "sapcontrol": (re.compile(r"^5\d\d1[34]$"), "sapcontrol ports are 5<NN>13 (HTTP) or 5<NN>14 (HTTPS)"),
    "hana": (re.compile(r"^3\d{4}$"), "HANA SQL ports are 3<NN>xx, e.g. 3<NN>15 for the first tenant or 3<NN>13 for SYSTEMDB"),
    "node_exporter": (re.compile(r"^9100$"), "node_exporter uses the approved scrape port 9100"),
    "rfc": (re.compile(r"^(33|48)\d\d$"), "RFC uses the gateway port 33<NN> (or 48<NN> with SNC)"),
}


def validate_port(kind: str, port: int) -> None:
    pattern, message = PORT_RULES[kind]
    if not pattern.match(str(port)):
        raise CollectorError(message)
