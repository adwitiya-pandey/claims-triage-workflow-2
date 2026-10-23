"""Claims tools interface definitions and protocol per SPEC 8."""

from typing import Any, Protocol, runtime_checkable


class ToolError(Exception):
    """Exception raised by claims tools backends on error."""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}" if message else code)


@runtime_checkable
class ClaimsTools(Protocol):
    """Protocol for reading claims facts. Read-only by design (S4)."""

    def get_claim(self, claim_id: str) -> dict[str, Any]:
        """Fetch claim by ID, joined with provider network status and plan."""
        ...

    def list_member_claims(
        self,
        member_id: str,
        status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List historical claims for a member ordered by service_date desc."""
        ...

    def get_coverage_rule(self, plan_id: str, procedure_code: str) -> dict[str, Any]:
        """Fetch coverage rule for a plan and procedure."""
        ...

    def get_member_ytd_summary(self, member_id: str, year: int) -> dict[str, Any]:
        """Calculate year-to-date claim totals and remaining annual limit."""
        ...

    def get_claim_notes(self, claim_id: str, limit: int = 10) -> dict[str, Any]:
        """Fetch clinical/administrative notes for a claim."""
        ...
