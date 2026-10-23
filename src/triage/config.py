"""Configuration settings for claims triage with secure secret handling."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

SECRET_FIELDS = {"GEMINI_API_KEY", "CLAIMS_MCP_KEY", "TRIAGE_API_KEY"}


@dataclass
class TriageConfig:
    """Triage configuration loaded from environment and .env."""

    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-3.8-flash"
    LLM_MODE: str = "replay"
    CASSETTE_PATH: str = "data/cassettes/cassette.jsonl"
    CLAIMS_TOOLS_BACKEND: str = "fixture"
    CLAIMS_MCP_URL: str = ""
    CLAIMS_MCP_KEY: str = ""
    CLAIMS_MCP_STDIO_CMD: str = ""
    HIGH_AMOUNT_THRESHOLD: float = 10000.0
    TIMELY_FILING_DAYS: int = 90
    MIN_EXTRACT_CONFIDENCE: float = 0.7
    TOP_K: int = 4
    TRIAGE_DB_PATH: str = "data/triage.db"
    CHECKPOINT_DB_PATH: str = "data/checkpoint.db"
    TRIAGE_API_KEY: str = ""
    API_HOST: str = "127.0.0.1"
    API_PORT: int = 8800
    LOG_LEVEL: str = "INFO"

    @classmethod
    def load(cls, env_path: str | Path | None = None) -> "TriageConfig":
        """Load configuration from environment, optionally loading from an env file."""
        if env_path is not None:
            load_dotenv(dotenv_path=env_path, override=True)
        else:
            load_dotenv()

        cfg = cls(
            GEMINI_API_KEY=os.getenv("GEMINI_API_KEY", ""),
            GEMINI_MODEL=os.getenv("GEMINI_MODEL", "gemini-3.8-flash"),
            LLM_MODE=os.getenv("LLM_MODE", "replay"),
            CASSETTE_PATH=os.getenv("CASSETTE_PATH", "data/cassettes/cassette.jsonl"),
            CLAIMS_TOOLS_BACKEND=os.getenv("CLAIMS_TOOLS_BACKEND", "fixture"),
            CLAIMS_MCP_URL=os.getenv("CLAIMS_MCP_URL", ""),
            CLAIMS_MCP_KEY=os.getenv("CLAIMS_MCP_KEY", ""),
            CLAIMS_MCP_STDIO_CMD=os.getenv("CLAIMS_MCP_STDIO_CMD", ""),
            HIGH_AMOUNT_THRESHOLD=float(os.getenv("HIGH_AMOUNT_THRESHOLD", "10000.0")),
            TIMELY_FILING_DAYS=int(os.getenv("TIMELY_FILING_DAYS", "90")),
            MIN_EXTRACT_CONFIDENCE=float(os.getenv("MIN_EXTRACT_CONFIDENCE", "0.7")),
            TOP_K=int(os.getenv("TOP_K", "4")),
            TRIAGE_DB_PATH=os.getenv("TRIAGE_DB_PATH", "data/triage.db"),
            CHECKPOINT_DB_PATH=os.getenv("CHECKPOINT_DB_PATH", "data/checkpoint.db"),
            TRIAGE_API_KEY=os.getenv("TRIAGE_API_KEY", ""),
            API_HOST=os.getenv("API_HOST", "127.0.0.1"),
            API_PORT=int(os.getenv("API_PORT", "8800")),
            LOG_LEVEL=os.getenv("LOG_LEVEL", "INFO"),
        )
        cfg.validate()
        return cfg

    def validate(self) -> None:
        """Validate config parameters without exposing secrets in errors."""
        valid_llm_modes = {"live", "record", "replay", "oracle"}
        if self.LLM_MODE not in valid_llm_modes:
            raise ValueError(
                f"Invalid LLM_MODE: {self.LLM_MODE}. "
                f"Must be one of {sorted(valid_llm_modes)}"
            )

        if self.LLM_MODE in {"live", "record"} and not self.GEMINI_API_KEY.strip():
            raise ValueError(
                f"GEMINI_API_KEY is required when LLM_MODE is '{self.LLM_MODE}'"
            )

        valid_backends = {"fixture", "mcp"}
        if self.CLAIMS_TOOLS_BACKEND not in valid_backends:
            raise ValueError(
                f"Invalid CLAIMS_TOOLS_BACKEND: {self.CLAIMS_TOOLS_BACKEND}. "
                f"Must be one of {sorted(valid_backends)}"
            )

        if self.CLAIMS_TOOLS_BACKEND == "mcp":
            if not self.CLAIMS_MCP_URL and not self.CLAIMS_MCP_STDIO_CMD:
                raise ValueError(
                    "Either CLAIMS_MCP_URL or CLAIMS_MCP_STDIO_CMD must be set "
                    "when CLAIMS_TOOLS_BACKEND is 'mcp'"
                )

        if self.HIGH_AMOUNT_THRESHOLD <= 0:
            raise ValueError("HIGH_AMOUNT_THRESHOLD must be positive")
        if self.TIMELY_FILING_DAYS <= 0:
            raise ValueError("TIMELY_FILING_DAYS must be positive")
        if not (0.0 <= self.MIN_EXTRACT_CONFIDENCE <= 1.0):
            raise ValueError("MIN_EXTRACT_CONFIDENCE must be between 0.0 and 1.0")
        if self.TOP_K < 1:
            raise ValueError("TOP_K must be at least 1")

    def __repr__(self) -> str:
        """String representation masking secret fields."""
        items = []
        for key, val in self.__dict__.items():
            if key in SECRET_FIELDS:
                display_val = "***" if val else ""
            else:
                display_val = repr(val)
            items.append(f"{key}={display_val}")
        return f"TriageConfig({', '.join(items)})"

    def __str__(self) -> str:
        return self.__repr__()
