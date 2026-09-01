"""Tests email_service (SMTP stdlib, mocks uniquement, aucun envoi réel)."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import email_service
import config


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.starttls_called = False
        self.login_called = False
        self.login_args = None
        self.sent_msg = None
        FakeSMTP.instances.append(self)

    def starttls(self):
        self.starttls_called = True

    def login(self, user, pwd):
        self.login_called = True
        self.login_args = (user, pwd)

    def send_message(self, msg):
        self.sent_msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _configure_smtp(monkeypatch, host="smtp.test.com", port=587, from_addr="noreply@hayett.tn",
                    user="u", pwd="p", use_tls=True):
    monkeypatch.setattr(config, "SMTP_HOST", host)
    monkeypatch.setattr(config, "SMTP_FROM", from_addr)
    monkeypatch.setattr(config, "SMTP_PORT", port)
    monkeypatch.setattr(config, "SMTP_USERNAME", user)
    monkeypatch.setattr(config, "SMTP_PASSWORD", pwd)
    monkeypatch.setattr(config, "SMTP_USE_TLS", use_tls)
    monkeypatch.setattr(email_service, "SMTP_HOST", host)
    monkeypatch.setattr(email_service, "SMTP_FROM", from_addr)
    monkeypatch.setattr(email_service, "SMTP_PORT", port)
    monkeypatch.setattr(email_service, "SMTP_USERNAME", user)
    monkeypatch.setattr(email_service, "SMTP_PASSWORD", pwd)
    monkeypatch.setattr(email_service, "SMTP_USE_TLS", use_tls)


class TestBuildWelcomeEmail:
    def test_headers(self, monkeypatch):
        _configure_smtp(monkeypatch)
        msg = email_service.build_welcome_email("Ahmed", "Ben Salah", "ahmed@gmail.com", "Secret123!")
        assert msg["To"] == "ahmed@gmail.com"
        assert msg["From"] == "noreply@hayett.tn"
        assert msg["Subject"] == "Bienvenue dans votre espace client HAYETT"

    def test_body_contains_password_at_build(self, monkeypatch):
        _configure_smtp(monkeypatch)
        pwd = "MyPass9!"
        msg = email_service.build_welcome_email("Fatma", "Trabelsi", "fatma@gmail.com", pwd)
        body = msg.get_body(preferencelist=('plain',)).get_content()
        assert "fatma@gmail.com" in body
        assert pwd in body
        assert msg["Subject"] == "Bienvenue dans votre espace client HAYETT"

    def test_password_only_in_email_not_hash(self, monkeypatch):
        _configure_smtp(monkeypatch)
        from security import hash_password
        pwd = "Secret123!"
        hashed = hash_password(pwd)
        msg = email_service.build_welcome_email("Ahmed", "Ben Salah", "ahmed@gmail.com", pwd)
        body = msg.get_body(preferencelist=('plain',)).get_content()
        assert pwd in body
        assert hashed not in body

    def test_html_alternative_present(self, monkeypatch):
        _configure_smtp(monkeypatch)
        msg = email_service.build_welcome_email("A", "B", "a@b.com", "pwd")
        html = msg.get_body(preferencelist=('html',)).get_content()
        assert "<ul>" in html
        assert "pwd" in html


class TestIsEmailConfigured:
    def test_not_configured_when_host_empty(self, monkeypatch):
        _configure_smtp(monkeypatch, host="", from_addr="noreply@hayett.tn")
        assert email_service.is_email_configured() is False

    def test_not_configured_when_from_empty(self, monkeypatch):
        _configure_smtp(monkeypatch, host="smtp.test.com", from_addr="")
        assert email_service.is_email_configured() is False

    def test_configured_when_both_present(self, monkeypatch):
        _configure_smtp(monkeypatch)
        assert email_service.is_email_configured() is True


class TestSendWelcomeEmail:
    def test_send_with_tls_and_login(self, monkeypatch):
        _configure_smtp(monkeypatch, use_tls=True, user="user1", pwd="pass1")
        FakeSMTP.instances.clear()
        monkeypatch.setattr("smtplib.SMTP", FakeSMTP)
        email_service.send_welcome_email("client@hayett.tn", "Ahmed", "Ben Salah", "Secret123!")
        inst = FakeSMTP.instances[-1]
        assert inst.starttls_called is True
        assert inst.login_called is True
        assert inst.login_args == ("user1", "pass1")
        assert inst.sent_msg["To"] == "client@hayett.tn"

    def test_send_without_tls_no_starttls(self, monkeypatch):
        _configure_smtp(monkeypatch, use_tls=False, user="", pwd="")
        FakeSMTP.instances.clear()
        monkeypatch.setattr("smtplib.SMTP", FakeSMTP)
        email_service.send_welcome_email("c@h.tn", "A", "B", "pwd")
        inst = FakeSMTP.instances[-1]
        assert inst.starttls_called is False
        assert inst.login_called is False

    def test_send_not_configured_raises(self, monkeypatch):
        _configure_smtp(monkeypatch, host="", from_addr="")
        import pytest
        with pytest.raises(RuntimeError, match="Email non configur"):
            email_service.send_welcome_email("c@h.tn", "A", "B", "pwd")

    def test_smtp_exception_propagated(self, monkeypatch):
        _configure_smtp(monkeypatch)
        class BoomSMTP(FakeSMTP):
            def send_message(self, msg):
                import smtplib
                raise smtplib.SMTPException("boom")

        monkeypatch.setattr("smtplib.SMTP", BoomSMTP)
        import pytest
        with pytest.raises(Exception):
            email_service.send_welcome_email("c@h.tn", "A", "B", "pwd")

    def test_no_password_in_logs(self, monkeypatch, caplog):
        _configure_smtp(monkeypatch)
        FakeSMTP.instances.clear()
        monkeypatch.setattr("smtplib.SMTP", FakeSMTP)
        pwd = "SuperSecret999!"
        with caplog.at_level("INFO"):
            email_service.send_welcome_email("c@h.tn", "A", "B", pwd)
        # Logs contain email but never the password
        for rec in caplog.records:
            assert pwd not in rec.getMessage()
            assert pwd not in str(rec.args)
