"""MCP client implementation of ClaimsTools per SPEC 8."""

import asyncio
import json
import re
import time
from typing import Any, Callable

from mcp import ClientSession
from mcp.client.streamable_http import httpx2, streamable_http_client

from triage.tools.base import ToolError

_RE_ERROR_CODE = re.compile(r"^([A-Z_]+)(?::\s*(.*))?$", re.DOTALL)


class McpToolsBackend:
    """Read-only claims tools backed by a live MCP server over HTTP or stdio."""

    def __init__(
        self,
        url: str = "",
        key: str = "",
        stdio_cmd: str = "",
        timeout: float = 10.0,
        delays: list[float] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
        session_caller: Callable[[str, dict[str, Any]], Any] | None = None,
    ) -> None:
        self.url = url
        self.key = key
        self.stdio_cmd = stdio_cmd
        self.timeout = timeout
        self.delays = delays if delays is not None else [1.0, 2.0, 4.0]
        self.sleep_fn = sleep_fn if sleep_fn is not None else time.sleep
        self._session_caller = session_caller

    async def _call_mcp_raw(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Low-level async MCP call via HTTP streaming transport."""
        if self._session_caller is not None:
            res = self._session_caller(tool_name, arguments)
            if asyncio.iscoroutine(res):
                res = await res
            return res

        headers = {}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"

        try:
            async with httpx2.AsyncClient(
                headers=headers, timeout=self.timeout
            ) as http_client:
                async with streamable_http_client(
                    self.url, http_client=http_client
                ) as (read_stream, write_stream):
                    async with ClientSession(
                        read_stream, write_stream, read_timeout_seconds=self.timeout
                    ) as session:
                        await session.initialize()
                        result = await session.call_tool(tool_name, arguments)
        except Exception as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if status_code == 401:
                raise ToolError(
                    "UNAUTHENTICATED", "Authentication failed (HTTP 401)"
                ) from exc
            if status_code == 403:
                raise ToolError(
                    "FORBIDDEN_TOOL", "Tool access forbidden (HTTP 403)"
                ) from exc
            raise ToolError("INTERNAL", f"Transport error: {exc}") from exc

        if result.is_error:
            raw_text = ""
            if result.content:
                first = result.content[0]
                raw_text = getattr(first, "text", str(first))
            match = _RE_ERROR_CODE.match(raw_text.strip())
            if match:
                code = match.group(1)
                msg = match.group(2) or ""
            else:
                code = "INTERNAL"
                msg = raw_text
            raise ToolError(code, msg)

        # Parse success output
        if (
            hasattr(result, "structured_content")
            and result.structured_content is not None
        ):
            return result.structured_content  # type: ignore[no-any-return]

        if result.content:
            first = result.content[0]
            text = getattr(first, "text", "")
            if text:
                try:
                    return json.loads(text)  # type: ignore[no-any-return]
                except Exception as json_err:
                    raise ToolError(
                        "INTERNAL", f"Malformed JSON from tool: {json_err}"
                    ) from json_err

        return {}

    def _call_with_retry(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute tool synchronously with retry backoff only on RATE_LIMITED."""
        for attempt, delay in enumerate([0.0] + self.delays):
            if delay > 0:
                self.sleep_fn(delay)
            try:
                return asyncio.run(self._call_mcp_raw(tool_name, arguments))
            except ToolError as err:
                if err.code != "RATE_LIMITED" or attempt == len(self.delays):
                    raise
        raise ToolError("RATE_LIMITED", "Exceeded rate limit retries")

    # -------------------------------------------------------------
    # ClaimsTools Protocol Implementations (Read-Only)
    # -------------------------------------------------------------

    def get_claim(self, claim_id: str) -> dict[str, Any]:
        """Fetch claim by ID, joined with provider network status and plan."""
        return self._call_with_retry("get_claim", {"claim_id": claim_id})

    def list_member_claims(
        self,
        member_id: str,
        status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List historical claims for a member ordered by service_date desc."""
        args: dict[str, Any] = {
            "member_id": member_id,
            "limit": limit,
            "offset": offset,
        }
        if status is not None:
            args["status"] = status
        return self._call_with_retry("list_member_claims", args)

    def get_coverage_rule(self, plan_id: str, procedure_code: str) -> dict[str, Any]:
        """Fetch coverage rule for a plan and procedure."""
        return self._call_with_retry(
            "get_coverage_rule",
            {"plan_id": plan_id, "procedure_code": procedure_code},
        )

    def get_member_ytd_summary(self, member_id: str, year: int) -> dict[str, Any]:
        """Calculate year-to-date claim totals and remaining annual limit."""
        return self._call_with_retry(
            "get_member_ytd_summary",
            {"member_id": member_id, "year": year},
        )

    def get_claim_notes(self, claim_id: str, limit: int = 10) -> dict[str, Any]:
        """Fetch clinical/administrative notes for a claim."""
        return self._call_with_retry(
            "get_claim_notes",
            {"claim_id": claim_id, "limit": limit},
        )
