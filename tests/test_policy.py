"""Tests for the Policy & Risk Engine."""

import pytest
from friday.policy import PolicyEngine, Sensitivity


@pytest.fixture
def engine():
    cfg = {
        "policy": {
            "blocked_directories": ["/tmp/friday_blocked"],
            "blocked_extensions": [".key", ".pem"],
            "require_confirmation_on_delete": True,
            "cloud_enabled": False,
            "max_file_size_mb": 10,
        }
    }
    return PolicyEngine(cfg)


# ---------------------------------------------------------------------------
# Text classification
# ---------------------------------------------------------------------------

class TestClassifyText:
    def test_l0_public(self, engine):
        assert engine.classify_text("What is the weather today?") == Sensitivity.L0_PUBLIC

    def test_l1_personal_abstract(self, engine):
        assert engine.classify_text("I am looking for a recipe") == Sensitivity.L1_PERSONAL_ABSTRACT

    def test_l2_email(self, engine):
        assert engine.classify_text("Send to alice@example.com") == Sensitivity.L2_PERSONAL

    def test_l2_local_path_unix(self, engine):
        assert engine.classify_text("Open /home/user/document.txt") == Sensitivity.L2_PERSONAL

    def test_l2_local_path_windows(self, engine):
        assert engine.classify_text("Read C:\\Users\\user\\file.txt") == Sensitivity.L2_PERSONAL

    def test_l2_tilde_path(self, engine):
        assert engine.classify_text("List ~/Documents") == Sensitivity.L2_PERSONAL

    def test_l3_password_assignment(self, engine):
        result = engine.classify_text("password=SuperSecret123")
        assert result == Sensitivity.L3_SECRET

    def test_l3_api_key(self, engine):
        result = engine.classify_text("api_key: abc123xyzlong_value_here_1234")
        assert result == Sensitivity.L3_SECRET

    def test_l3_hex_hash(self, engine):
        result = engine.classify_text(
            "token: " + "a" * 41
        )
        assert result == Sensitivity.L3_SECRET


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

class TestRedact:
    def test_redact_email(self, engine):
        redacted = engine.redact("Contact bob@example.com for info")
        assert "bob@example.com" not in redacted
        assert "[REDACTED_EMAIL]" in redacted

    def test_redact_path(self, engine):
        redacted = engine.redact("File at /home/user/secrets.txt")
        assert "/home/user/secrets.txt" not in redacted
        assert "REDACTED" in redacted

    def test_no_pii_unchanged_structure(self, engine):
        text = "What is 2+2?"
        # should not raise; content is mostly preserved (no PII matches)
        redacted = engine.redact(text)
        assert isinstance(redacted, str)


# ---------------------------------------------------------------------------
# Path checks
# ---------------------------------------------------------------------------

class TestCheckPath:
    def test_blocked_dir_rejected(self, engine, tmp_path):
        import pathlib
        # Patch blocked dirs to tmp_path / "blocked"
        blocked = tmp_path / "blocked"
        blocked.mkdir()
        engine._blocked_dirs = [blocked]
        victim = blocked / "file.txt"
        victim.write_text("hi")
        decision = engine.check_path(victim)
        assert not decision.allowed
        assert decision.sensitivity == Sensitivity.L3_SECRET

    def test_blocked_extension(self, engine, tmp_path):
        f = tmp_path / "id_rsa.key"
        f.write_text("key content")
        decision = engine.check_path(f)
        assert not decision.allowed

    def test_allowed_path(self, engine, tmp_path):
        f = tmp_path / "report.xlsx"
        f.write_text("data")
        decision = engine.check_path(f)
        assert decision.allowed


# ---------------------------------------------------------------------------
# Cloud egress guard
# ---------------------------------------------------------------------------

class TestCloudEgress:
    def test_cloud_disabled_blocks(self, engine):
        decision = engine.check_cloud_egress("hello world")
        assert not decision.allowed
        assert "disabled" in decision.reason.lower()

    def test_cloud_enabled_l0_allowed(self):
        cfg = {"policy": {"cloud_enabled": True, "blocked_directories": [], "blocked_extensions": []}}
        eng = PolicyEngine(cfg)
        decision = eng.check_cloud_egress("What is the capital of France?")
        assert decision.allowed

    def test_cloud_enabled_l2_blocked(self):
        cfg = {"policy": {"cloud_enabled": True, "blocked_directories": [], "blocked_extensions": []}}
        eng = PolicyEngine(cfg)
        decision = eng.check_cloud_egress("Check ~/Documents/report.pdf")
        assert not decision.allowed

    def test_cloud_enabled_path_blocked(self, tmp_path):
        blocked = tmp_path / "secrets"
        blocked.mkdir()
        cfg = {
            "policy": {
                "cloud_enabled": True,
                "blocked_directories": [str(blocked)],
                "blocked_extensions": [],
            }
        }
        eng = PolicyEngine(cfg)
        victim = blocked / "key.txt"
        victim.write_text("secret")
        decision = eng.check_cloud_egress("process this", paths=[str(victim)])
        assert not decision.allowed


# ---------------------------------------------------------------------------
# Destructive op confirmation
# ---------------------------------------------------------------------------

class TestDestructiveOp:
    def test_requires_confirmation_when_policy_enabled(self, engine):
        decision = engine.check_destructive_op("delete", ["/tmp/file.txt"])
        assert decision.allowed
        assert decision.requires_confirmation

    def test_no_confirmation_when_policy_disabled(self):
        cfg = {
            "policy": {
                "require_confirmation_on_delete": False,
                "blocked_directories": [],
                "blocked_extensions": [],
            }
        }
        eng = PolicyEngine(cfg)
        decision = eng.check_destructive_op("delete", ["/tmp/file.txt"])
        assert decision.allowed
        assert not decision.requires_confirmation
