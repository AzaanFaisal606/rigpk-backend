"""
Adversarial coverage for backend.routers.parts._safe_error (F1/F2/F3 fix round).

The original implementation only stripped `/`-delimited path runs, so anything
secret-shaped with no slash — a bare JWT, a SQL fragment, a Windows path, a query
string on a connection URL — passed through to the public /api/stats endpoint
completely unredacted. Every test below is built to fail against that version:
each input is deliberately slash-free (or otherwise outside the old pattern) so
none of them would have been touched by the pre-fix `_PATH_RE`-only sanitizer.

_safe_error is tested directly (unit-level, no TestClient) because it's a pure
function and the failure mode is "the bad text is still in the string" — a
precise `in`/`not in` check on its return value is a stronger, faster assertion
than parsing a full HTTP response body for the same thing.
"""
from db.database import Database
from backend.routers.parts import _safe_error, _SAFE_ERROR_MAX_LEN


def test_bare_jwt_is_redacted():
    jwt = "eyJhbGciOiJFZERTQSJ9.eyJhIjoicncifQ.sig"
    out = _safe_error(f"auth failed: {jwt}")
    assert jwt not in out
    assert "eyJ" not in out


def test_sql_fragment_is_redacted():
    out = _safe_error("SELECT price_pkr FROM parts WHERE id = 42")
    assert "SELECT" not in out
    assert "price_pkr" not in out
    assert "42" not in out


def test_url_with_query_token_is_redacted():
    # The bare host in a libsql:// URL happens to get mangled by the old
    # slash-based path regex as a side effect (it looks like "/host.tld"), but
    # a query string has no slash at all, so an auth token appended to a
    # connection string sailed straight through pre-fix — a realistic and
    # more dangerous leak than the hostname itself.
    token = "super-secret-auth-token-0123456789"
    out = _safe_error(f"could not connect to libsql://ppc-xyz.turso.io?authToken={token}")
    assert token not in out
    assert "turso.io" not in out


def test_windows_path_is_redacted():
    out = _safe_error(r"failed to read C:\Users\azaan\secrets.txt")
    assert r"C:\Users\azaan\secrets.txt" not in out
    assert "secrets.txt" not in out


def test_unix_path_still_redacted():
    # Existing behaviour must not regress.
    out = _safe_error("Traceback (most recent call last): File /home/azaan/secret/path.py")
    assert "Traceback" not in out
    assert "/home/azaan" not in out


def test_long_run_of_secret_shaped_characters_is_redacted():
    # Catch-all for anything token/key-shaped that isn't a named pattern above.
    out = _safe_error("api key rejected: synthetic-token-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789")
    assert "synthetic-token-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789" not in out


def test_empty_exception_message_produces_generic_non_empty_output():
    # e.g. `error=str(exc)` where `str(ValueError()) == ""` — the run still
    # failed, so the field must not silently look like "no error happened".
    out = _safe_error("")
    assert out is not None
    assert out.strip() != ""

    out_ws = _safe_error("   ")
    assert out_ws is not None
    assert out_ws.strip() != ""


def test_none_still_means_no_error():
    # A genuinely absent error (DB column NULL / run succeeded) must stay None,
    # not get turned into a generic message — that would fabricate a failure.
    assert _safe_error(None) is None


def test_very_long_error_is_truncated():
    # Plain words, no redactable pattern, so the length cap itself is what's
    # under test rather than a redaction collapsing the string incidentally.
    msg = "connection reset by peer while reading response " * 20
    assert len(msg) > _SAFE_ERROR_MAX_LEN
    out = _safe_error(msg)
    assert len(out) <= _SAFE_ERROR_MAX_LEN


def test_full_detail_still_reaches_storage_layer(tmp_path):
    """
    _safe_error only guards the API response. The scrape_runs row itself — what
    Database.record_scrape_run writes and Database.source_health reads back —
    must still carry the complete, unredacted error text, otherwise there is
    nothing left for anyone debugging a real failure to look at.
    """
    jwt = "eyJhbGciOiJFZERTQSJ9.eyJhIjoicncifQ.sig"
    full_error = f"SELECT * FROM parts WHERE token = {jwt} -- /home/azaan/secret/path.py"

    db = Database(tmp_path / "redaction.db")
    db.record_scrape_run(
        "czone",
        started_at="2026-08-14T00:00:00Z",
        finished_at="2026-08-14T00:05:00Z",
        ok=False,
        error=full_error,
    )
    health = db.source_health("parts")
    db.close()

    assert health["czone"]["last_error"] == full_error
