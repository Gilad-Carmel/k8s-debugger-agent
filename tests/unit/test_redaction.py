"""
tests/unit/test_redaction.py

Unit tests for double-pass regex redaction (src/agent/redaction.py).

Covers positive patterns (secrets ARE redacted) and negative cases (plain
text is NOT redacted).  95% coverage tier enforced by CI.

Corresponds to tasks.md T034.
"""

import pytest

from src.agent.redaction import redact


class TestBearerToken:
    def test_bearer_in_log_line(self) -> None:
        line = "Authorization: Bearer eyJhbGciOiJSUzI1NiJ9.payload.sig"
        result = redact(line)
        assert "eyJhbGciOiJSUzI1NiJ9" not in result
        assert "[REDACTED]" in result

    def test_bearer_standalone(self) -> None:
        result = redact("token=Bearer ABCDEFGHIJ1234567890abcd")
        assert "ABCDEFGHIJ1234567890abcd" not in result

    def test_plain_text_not_redacted(self) -> None:
        plain = "2024-01-01T00:00:00Z INFO server started"
        assert redact(plain) == plain


class TestJWT:
    def test_jwt_three_part(self) -> None:
        jwt = "eyJhbGciOiJSUzI1NiIsImtpZCI6ImFiYyJ9.eyJzdWIiOiJ1c2VyIn0.MEUCIQD123456"
        result = redact(jwt)
        assert jwt not in result
        assert "[REDACTED_JWT]" in result

    def test_jwt_in_log_line(self) -> None:
        line = f"token: eyJhbGciOiJSUzI1NiIsImtpZCI6ImFiYyJ9.eyJzdWIiOiJ1c2VyIn0.SIGSIGSIGsig123"
        result = redact(line)
        assert "eyJhbGciOiJSUzI1NiIsImtpZCI6ImFiYyJ9" not in result

    def test_short_base64_not_redacted(self) -> None:
        # Short string that looks like base64 but is not a JWT
        short = "ey12.ab.cd"
        # Too short to trigger the pattern (min 10 chars per segment)
        result = redact(short)
        # Should remain unchanged (each segment is only 2-4 chars)
        assert "REDACTED" not in result


class TestAWSKey:
    def test_aws_access_key(self) -> None:
        line = "Using key AKIAIOSFODNN7EXAMPLE for request"
        result = redact(line)
        assert "AKIAIOSFODNN7EXAMPLE" not in result
        assert "[REDACTED_AWS_KEY]" in result

    def test_asia_key(self) -> None:
        line = "Assumed role with key ASIAIOSFODNN7EXAMPLE"
        result = redact(line)
        assert "ASIAIOSFODNN7EXAMPLE" not in result

    def test_non_aws_key_not_redacted(self) -> None:
        result = redact("key=SOMERANDSTRING12345678")
        # No AKIA/ASIA prefix → should not be redacted by aws_access_key pattern
        assert "SOMERANDSTRING12345678" in result


class TestDBConnectionString:
    def test_postgres_url(self) -> None:
        url = "postgresql://admin:secret123@db.internal:5432/mydb"
        result = redact(url)
        assert "secret123" not in result
        assert "[REDACTED_DB_URL]" in result

    def test_mysql_url(self) -> None:
        url = "mysql://user:pass@localhost/testdb"
        result = redact(url)
        assert "user:pass" not in result

    def test_mongodb_url(self) -> None:
        url = "mongodb://appuser:apppass@mongo:27017/appdb"
        result = redact(url)
        assert "apppass" not in result


class TestGenericKV:
    def test_password_equals(self) -> None:
        result = redact("password=mysupersecretpassword")
        assert "mysupersecretpassword" not in result
        assert "[REDACTED]" in result

    def test_secret_colon(self) -> None:
        result = redact("secret: abc123xyz")
        assert "abc123xyz" not in result

    def test_api_key(self) -> None:
        result = redact("api_key=sk-proj-1234567890abcdef")
        assert "sk-proj-1234567890abcdef" not in result

    def test_multiple_secrets_one_line(self) -> None:
        line = "password=abc123 token=xyz789 api_key=mykey123"
        result = redact(line)
        assert "abc123" not in result
        assert "xyz789" not in result
        assert "mykey123" not in result

    def test_regular_words_not_redacted(self) -> None:
        assert redact("No secrets here at all.") == "No secrets here at all."

    def test_idempotent(self) -> None:
        line = "token=mysecret"
        once = redact(line)
        twice = redact(once)
        assert once == twice
