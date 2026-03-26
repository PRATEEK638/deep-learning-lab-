"""Policy & Risk Engine for FRIDAY.

Enforces sensitivity tiers (L0–L3) and egress rules so that
personal / secret data never reaches cloud models.

Sensitivity tiers
-----------------
L0  – public information                  → cloud allowed
L1  – abstracted personal (e.g. name)     → user choice; sanitize first
L2  – personal (files, emails, system)    → local-only
L3  – secrets (passwords, keys)           → refuse; never process
"""

from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Iterable

from .config import get_config


class Sensitivity(IntEnum):
    L0_PUBLIC = 0
    L1_PERSONAL_ABSTRACT = 1
    L2_PERSONAL = 2
    L3_SECRET = 3


# ---------------------------------------------------------------------------
# Redaction patterns
# ---------------------------------------------------------------------------

_REDACT_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("EMAIL", re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")),
    ("PHONE", re.compile(r"\b(?:\+?\d[\d\s\-().]{7,}\d)\b")),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("CREDIT_CARD", re.compile(r"\b(?:\d[ \-]?){13,16}\b")),
    ("IP_ADDR", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("API_KEY", re.compile(r"\b[A-Za-z0-9_\-]{20,}\b")),
    (
        "SECRET_ENV",
        re.compile(
            r"(?:password|secret|token|api[_\s]?key|private[_\s]?key)\s*[:=]\s*\S+",
            re.IGNORECASE,
        ),
    ),
]

# Base path regex shared with router.py.
# Matches Unix (/…), Windows (C:\…), and tilde (~/…) paths.
# Exported so router.py can build its broader LOCAL_FILE pattern from it.
_PATH_RE_CORE = r"(?:/[^\s\"')]+|[A-Za-z]:\\[^\s\"')]+|~/[^\s\"')*]*)"

# Local path patterns – presence of these in text → L2 at minimum
_PATH_PATTERN = re.compile(
    r"(?:^|[\s\"'(])" + _PATH_RE_CORE,
    re.MULTILINE,
)


@dataclass
class PolicyDecision:
    allowed: bool
    sensitivity: Sensitivity
    reason: str
    sanitized_text: str = ""
    requires_confirmation: bool = False
    blocked_paths: list[str] = field(default_factory=list)


class PolicyEngine:
    """Deterministic, code-enforced policy layer."""

    def __init__(self, config: dict | None = None):
        cfg = config or get_config()
        policy_cfg = cfg.get("policy", {})

        self._blocked_dirs: list[pathlib.Path] = [
            pathlib.Path(d).expanduser()
            for d in policy_cfg.get("blocked_directories", [])
        ]
        self._blocked_exts: set[str] = set(
            policy_cfg.get("blocked_extensions", [])
        )
        self._require_confirm_delete: bool = policy_cfg.get(
            "require_confirmation_on_delete", True
        )
        self._cloud_enabled: bool = policy_cfg.get("cloud_enabled", False)
        self._max_file_mb: int = policy_cfg.get("max_file_size_mb", 100)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def classify_text(self, text: str) -> Sensitivity:
        """Return the highest sensitivity tier found in *text*."""
        lower = text.lower()

        # L3 – outright secrets
        if re.search(
            r"(?:password|private[_\s]?key|secret[_\s]?key|api[_\s]?key"
            r"|ssh[_\s]?key|pgp|passphrase)\s*[:=]",
            lower,
        ):
            return Sensitivity.L3_SECRET

        # L3 – high-entropy tokens (rough heuristic: >=40 hex chars)
        if re.search(r"\b[0-9a-f]{40,}\b", lower) or re.search(
            r"\b[A-Za-z0-9+/]{40,}={0,2}\b", text
        ):
            return Sensitivity.L3_SECRET

        # L2 – PII / local paths
        if _PATH_PATTERN.search(text):
            return Sensitivity.L2_PERSONAL

        for _label, pat in _REDACT_PATTERNS[:5]:  # EMAIL, PHONE, SSN, CC, IP
            if pat.search(text):
                return Sensitivity.L2_PERSONAL

        # L1 – personal names / first-person references (simple heuristic)
        if re.search(r"\b(?:my|mine|i am|i'm|myself)\b", lower):
            return Sensitivity.L1_PERSONAL_ABSTRACT

        return Sensitivity.L0_PUBLIC

    def redact(self, text: str) -> str:
        """Return a copy of *text* with PII replaced by placeholders."""
        result = text
        for label, pat in _REDACT_PATTERNS:
            result = pat.sub(f"[REDACTED_{label}]", result)
        result = _PATH_PATTERN.sub(" [REDACTED_PATH]", result)
        return result

    def check_path(self, path: str | pathlib.Path) -> PolicyDecision:
        """Validate that *path* is not in a blocked directory / extension."""
        p = pathlib.Path(path).expanduser().resolve()

        for bd in self._blocked_dirs:
            try:
                p.relative_to(bd.resolve())
                return PolicyDecision(
                    allowed=False,
                    sensitivity=Sensitivity.L3_SECRET,
                    reason=f"Path is inside blocked directory: {bd}",
                    blocked_paths=[str(p)],
                )
            except ValueError:
                pass

        if p.suffix.lower() in self._blocked_exts:
            return PolicyDecision(
                allowed=False,
                sensitivity=Sensitivity.L3_SECRET,
                reason=f"Blocked file extension: {p.suffix}",
                blocked_paths=[str(p)],
            )

        return PolicyDecision(
            allowed=True,
            sensitivity=Sensitivity.L2_PERSONAL,
            reason="Path check passed",
        )

    def check_cloud_egress(
        self,
        text: str,
        paths: Iterable[str] | None = None,
    ) -> PolicyDecision:
        """Decide whether *text* (and optionally file paths) may go to cloud."""
        if not self._cloud_enabled:
            return PolicyDecision(
                allowed=False,
                sensitivity=Sensitivity.L2_PERSONAL,
                reason="Cloud is disabled in config (cloud_enabled=false)",
            )

        sensitivity = self.classify_text(text)

        if paths:
            for p in paths:
                path_decision = self.check_path(p)
                if not path_decision.allowed:
                    return path_decision
                if path_decision.sensitivity > sensitivity:
                    sensitivity = path_decision.sensitivity

        if sensitivity >= Sensitivity.L2_PERSONAL:
            return PolicyDecision(
                allowed=False,
                sensitivity=sensitivity,
                reason="Data classification ≥ L2 — cloud egress blocked",
                sanitized_text=self.redact(text),
            )

        return PolicyDecision(
            allowed=True,
            sensitivity=sensitivity,
            reason="Egress allowed",
            sanitized_text=self.redact(text) if sensitivity >= Sensitivity.L1_PERSONAL_ABSTRACT else text,
        )

    def check_destructive_op(self, op: str, targets: list[str]) -> PolicyDecision:
        """Return decision for destructive operations (delete/move-many/send)."""
        if not self._require_confirm_delete:
            return PolicyDecision(
                allowed=True,
                sensitivity=Sensitivity.L2_PERSONAL,
                reason="Destructive op allowed without confirmation (policy disabled)",
            )

        return PolicyDecision(
            allowed=True,
            sensitivity=Sensitivity.L2_PERSONAL,
            reason=f"Destructive op '{op}' requires user confirmation",
            requires_confirmation=True,
            blocked_paths=targets,
        )
