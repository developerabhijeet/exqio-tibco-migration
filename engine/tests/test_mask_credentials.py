"""X24: credential masking lives inside the shared mask_sensitive."""
import pytest

from engine.agents.provider import mask_sensitive


@pytest.mark.parametrize("text,secret", [
    ("password=hunter2xyz", "hunter2xyz"),
    ("PASSWORD: 'hunter2xyz'", "hunter2xyz"),
    ('{"pwd": "hunter2xyz"}', "hunter2xyz"),
    ("passwd = hunter2xyz", "hunter2xyz"),
    ("secret=s3cr3tvalue", "s3cr3tvalue"),
    ("client_secret=abc123SECRET", "abc123SECRET"),
    ("db_password=abc123SECRET", "abc123SECRET"),
    ("api_key=AKIA1234567890", "AKIA1234567890"),
    ("apikey: AKIA1234567890", "AKIA1234567890"),
    ("token=tok_9f8e7d6c", "tok_9f8e7d6c"),
    ("access_token: \"eyJhbGciOi.payload.sig\"", "eyJhbGciOi.payload.sig"),
    ("Authorization: Bearer abc.def.ghi123", "abc.def.ghi123"),
    ("authorization: Basic dXNlcjpwYXNzd29yZA==", "dXNlcjpwYXNzd29yZA"),
    ("Server=db;User Id=sa;Password=Pa55w0rd!;Encrypt=true", "Pa55w0rd"),
    ("curl https://bob:pa55word@example.com/x", "pa55word"),
    ("use token ghp_abcdefghijklmnop1234 for git", "ghp_abcdefghijklmnop1234"),
    ("<password>hunter2xyz</password>", "hunter2xyz"),
])
def test_credentials_masked(text, secret):
    assert secret not in mask_sensitive(text)


def test_shapes_preserved():
    assert mask_sensitive("Server=db;Password=Pa55w0rd!;Encrypt=true") == \
        "Server=db;Password=***;Encrypt=true"
    assert mask_sensitive("https://bob:pa55word@example.com/x") == "https://bob:***@example.com/x"
    assert mask_sensitive("password: abc, user: bob") == "password: ***, user: bob"
    assert mask_sensitive("Authorization: Bearer abc.def") == "Authorization: Bearer ***"


@pytest.mark.parametrize("text", [
    "the password field is required",
    "the token expires after an hour",
    "a token authentication-mechanism is used",
    "set max_tokens=4096 for the model",
    "see https://example.com/a:b/path and user@example.com",
    "Bearer of bad news",
    "passwords must be rotated",
])
def test_prose_untouched(text):
    assert mask_sensitive(text) == text


def test_existing_fields_and_extras_still_work():
    assert mask_sensitive("ssn=123-45-6789") == "ssn=***"
    assert "zz9" not in mask_sensitive("loan_id=zz9", extra_fields=["loan_id"])


def test_idempotent():
    once = mask_sensitive("password=abc12345; token=tok_9f8e7d6c")
    assert mask_sensitive(once) == once


_GH = "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
_PEM = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\nabc123==\n-----END RSA PRIVATE KEY-----"


@pytest.mark.parametrize("text,secret", [
    (f"clone with ghp_{_GH} now", _GH),
    (f"gho_{_GH} and ghu_{_GH}", _GH),
    (f"ghs_{_GH}, ghr_{_GH}", _GH),
    ("pat github_pat_11ABCDEFG0abcdefghijkl_mnopqrstuvwxyz0123456789", "mnopqrstuvwxyz"),
    (f"https://user:ghp_{_GH}@github.com/o/r.git", _GH),
    ("DefaultEndpointsProtocol=https;AccountName=a;AccountKey=abc123+/xyz==;EndpointSuffix=x", "abc123+/xyz"),
    ("SharedAccessSignature=sv=2020&sr=b&sig=Zm9vYmFy%3D", "Zm9vYmFy"),
    ("https://a.blob.core.windows.net/c?sv=2020&sig=Zm9vYmFy%3D&se=2030", "Zm9vYmFy"),
    ("X-Figma-Token: figd_abcdefghij1234", "figd_abcdefghij1234"),
    ("atlassian_token=ATATT3xFfGF0abcdef", "ATATT3xFfGF0abcdef"),
    (f"key:\n{_PEM}\nafter", "MIIEowIBAAKCAQEA"),
    ("-----BEGIN PRIVATE KEY-----\nMIIabc\n-----END PRIVATE KEY-----", "MIIabc"),
])
def test_more_credentials_masked(text, secret):
    assert secret not in mask_sensitive(text)


def test_pem_keeps_surroundings():
    assert mask_sensitive(f"before\n{_PEM}\nafter") == "before\n***\nafter"


@pytest.mark.parametrize("text", [
    "tokens start with ghp_ for GitHub",
    "ghp_short is not a token",
    "the AccountKey field holds the key",
    "the sig parameter signs the URL",
    "a signature=valid check",
    "-----BEGIN CERTIFICATE----- is a public cert header",
    "the public key block starts with BEGIN PUBLIC KEY",
])
def test_more_prose_untouched(text):
    assert mask_sensitive(text) == text
