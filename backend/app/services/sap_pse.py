"""SAP-side certificate collector boundary.

A production STRUST/PSE adapter must be implemented against a supported SAP_BASIS
release and a documented/released interface. This module deliberately does not
read STRUST filesystem files or guess internal table layouts.
"""
from typing import Protocol

class SapPseCollector(Protocol):
    def list_certificates(self, system_id: str, client: str | None = None) -> list[dict]:
        """Return public certificate metadata only; never private keys or PSE passwords."""
        ...

class SapPseCollectorNotConfigured(RuntimeError):
    pass

class RfcPseCollector:
    """Placeholder adapter requiring a customer-approved RFC implementation.

    Configure a dedicated least-privilege RFC destination and a reviewed RFC
    function module/API for the exact SAP_BASIS release before implementing this
    adapter. No standard RFC function is assumed to expose every STRUST PSE.
    """
    def __init__(self, destination_name: str):
        self.destination_name = destination_name

    def list_certificates(self, system_id: str, client: str | None = None) -> list[dict]:
        raise SapPseCollectorNotConfigured(
            "STRUST/PSE RFC adapter requires a release-validated SAP interface; "
            "endpoint certificate monitoring remains available."
        )
