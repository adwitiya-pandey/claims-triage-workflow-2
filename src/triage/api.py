"""FastAPI application endpoints per SPEC 14."""

import re
import secrets
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from triage.config import TriageConfig
from triage.models import ClaimId, ReviewAction, StrictBaseModel
from triage.service import TriageService, get_service

_CLAIM_ID_PAT = re.compile(r"^CLM[0-9]{8}$")


class TriageRequest(StrictBaseModel):
    """Request payload for starting claim triage."""

    claim_id: ClaimId
    reprocess: bool = False


def create_app(
    config: TriageConfig | None = None, service: TriageService | None = None
) -> FastAPI:
    """Application factory for claims triage REST API."""
    cfg = config or TriageConfig.load()
    svc = service or get_service()

    app = FastAPI(
        title="Claims Triage API",
        version="0.1.0",
        docs_url=None,  # Minimal footprint per SPEC 14
        redoc_url=None,
    )

    # -------------------------------------------------------------
    # Exception Handlers: Never leak tracebacks or claim text (S7)
    # -------------------------------------------------------------

    @app.exception_handler(RequestValidationError)
    def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": "Invalid request parameters or payload format"},
        )

    @app.exception_handler(Exception)
    def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal processing error"},
        )

    # -------------------------------------------------------------
    # Security Dependency
    # -------------------------------------------------------------

    def verify_api_key(
        x_api_key: str | None = Header(None, alias="X-API-Key"),
    ) -> str:
        """Enforce constant-time API key verification (SPEC 14)."""
        if not x_api_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing X-API-Key header",
            )
        expected = cfg.TRIAGE_API_KEY
        if not expected or not secrets.compare_digest(x_api_key, expected):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key",
            )
        return x_api_key

    # -------------------------------------------------------------
    # Plain `def` Endpoints (executed in worker thread pool)
    # -------------------------------------------------------------

    @app.get("/health")
    def health() -> dict[str, Any]:
        """Health check endpoint without secrets or traceback leaks."""
        try:
            db_path = Path(cfg.TRIAGE_DB_PATH)
            db_writable = db_path.parent.exists() or db_path.exists()
            cp_path = Path(cfg.CHECKPOINT_DB_PATH)
            cp_writable = cp_path.parent.exists() or cp_path.exists()
        except Exception:
            db_writable = False
            cp_writable = False

        return {
            "status": "ok" if (db_writable and cp_writable) else "degraded",
            "version": "0.1.0",
        }

    @app.post("/triage")
    def start_triage_endpoint(
        body: TriageRequest,
        _: str = Depends(verify_api_key),
    ) -> dict[str, Any]:
        """Initiate or retrieve claims triage idempotently."""
        result = svc.start_triage(claim_id=body.claim_id, reprocess=body.reprocess)
        if result["status"] == "awaiting_review":
            return {
                "status": "awaiting_review",
                "thread_id": result["thread_id"],
                "version": result["version"],
                "packet": result.get("review_packet"),
            }
        return {
            "status": "completed",
            "decision": result.get("decision"),
        }

    @app.get("/reviews/pending")
    def list_pending_endpoint(
        _: str = Depends(verify_api_key),
    ) -> list[dict[str, Any]]:
        """List awaiting human reviews."""
        return svc.list_pending()

    @app.post("/reviews/{thread_id}")
    def submit_review_endpoint(
        thread_id: str,
        action: ReviewAction,
        _: str = Depends(verify_api_key),
    ) -> dict[str, Any]:
        """Resume a paused triage review."""
        try:
            res = svc.submit_review(
                thread_id=thread_id,
                action=action.model_dump(mode="json"),
            )
            return {
                "status": "completed",
                "decision": res.get("decision"),
            }
        except ValueError as exc:
            msg = str(exc).lower()
            if "not found" in msg:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Review thread not found",
                ) from exc
            if "already resolved" in msg:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Review thread is already resolved",
                ) from exc
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc

    @app.get("/decisions/{claim_id}")
    def get_decisions_endpoint(
        claim_id: str,
        _: str = Depends(verify_api_key),
    ) -> list[dict[str, Any]]:
        """Retrieve all historical decision versions for claim_id."""
        if not _CLAIM_ID_PAT.fullmatch(claim_id):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid claim_id format",
            )
        decisions = svc.get_decisions(claim_id)
        if not decisions:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No decisions found for claim {claim_id}",
            )
        return decisions

    return app


app = create_app()
