import ipaddress
import socket

from .. import config


class TargetPolicyError(ValueError):
    """Raised when a target is outside the configured outbound access policy."""


def validate_target(hostname: str, port: int, check_port: bool = True) -> None:
    host = hostname.strip().lower().rstrip(".")
    if not config.ALLOWED_TARGET_HOSTS:
        raise TargetPolicyError("Target registration is disabled: configure ALLOWED_TARGET_HOSTS first")
    if check_port and port not in config.ALLOWED_TARGET_PORTS:
        raise TargetPolicyError(
            f"Port {port} is not permitted; configure ALLOWED_TARGET_PORTS explicitly"
        )
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    for entry in config.ALLOWED_TARGET_HOSTS:
        rule = entry.lower().rstrip(".")
        if rule.startswith("*."):
            suffix = rule[1:]
            if host.endswith(suffix) and host != suffix[1:]:
                return
        elif host == rule:
            return
    if address is not None:
        raise TargetPolicyError("IP targets must be listed explicitly in ALLOWED_TARGET_HOSTS")
    raise TargetPolicyError("Hostname is not included in ALLOWED_TARGET_HOSTS")


def resolve_approved_addresses(hostname: str, port: int, check_port: bool = True) -> list[str]:
    """Validate host (and port) against policy and return DNS answers that are all inside policy.

    Collectors pass check_port=False and validate their protocol-specific port ranges themselves
    (sapcontrol 5<NN>13/14, HANA SQL 3<NN>xx)."""
    validate_target(hostname, port, check_port)
    try:
        answers = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise TargetPolicyError(f"DNS resolution failed for approved target: {exc}") from exc
    addresses = sorted({item[4][0] for item in answers})
    if not addresses:
        raise TargetPolicyError("Target hostname did not resolve to an address")
    networks = [ipaddress.ip_network(item, strict=False) for item in config.ALLOWED_TARGET_CIDRS]
    for raw in addresses:
        ip = ipaddress.ip_address(raw)
        if networks:
            if not any(ip in network for network in networks):
                raise TargetPolicyError(f"Resolved address {ip} is outside ALLOWED_TARGET_CIDRS")
        elif not ip.is_global:
            raise TargetPolicyError(
                f"Resolved address {ip} is not globally routable; configure its private network in ALLOWED_TARGET_CIDRS"
            )
    return addresses
