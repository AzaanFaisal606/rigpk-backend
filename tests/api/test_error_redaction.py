"""
Adversarial coverage for backend.routers.parts._safe_error (fix round 2 —
deny-by-default rewrite).

Round 1 was still architecturally a strip-list: named patterns redacted
things that "looked like" a path/URL/JWT/SQL fragment, and anything shaped
differently sailed through untouched, capped at 120 chars. The round-2
review broke it by executing it directly:

  'connection to ppc-azaan-faisal606.aws-ap-northeast-1.turso.io failed'
      -> UNREDACTED (the char class in the old catch-all excluded '.', so a
         hostname broken into short dot-separated runs passed every pattern)
  'failed to open %2Fhome%2Fazaan%2Fsecret%2Fpath.py'
      -> UNREDACTED (same gap, this time via '%' from percent-encoding)

Both exploited the same structural hole: the old function was built to pass
input through by default and subtract known-bad shapes from it, so a shape
one pattern didn't anticipate always got through — no amount of adding more
patterns closes that, since the *next* untested shape (a unicode homoglyph,
a differently-delimited secret) always exists.

The round-2 rewrite (`_safe_error`, `backend/routers/parts.py`) never passes
input through. It classifies the leading exception-type name (or a known
literal phrase) against `_KNOWN_ERROR_PREFIXES` and returns ONLY the mapped
constant string — or, for RuntimeError, a template filled with an HTTP
status code that was itself validated with a strict pattern before being
re-emitted. Nothing unrecognised, however it's shaped, ever contributes a
single byte to the response; it falls straight through to
`_SAFE_ERROR_FALLBACK`. This file's tests are built to prove that
*architecture*, not to chase the next pattern gap.

`_safe_error` is tested directly (unit-level, no TestClient) because it's a
pure function and the property under test — "the output is always one of a
fixed, known set of strings" — is best expressed as a membership check
against `_FIXED_SAFE_ERROR_MESSAGES` / `_HTTP_STATUS_PHRASE_RE`, not by
parsing an HTTP response body for the same thing.
"""
from db.database import Database
from backend.routers.parts import (
    _safe_error,
    _FIXED_SAFE_ERROR_MESSAGES,
    _HTTP_STATUS_PHRASE_RE,
    _SAFE_ERROR_FALLBACK,
    _SAFE_ERROR_MAX_LEN,
)


def _is_allowlisted(out: str) -> bool:
    return out in _FIXED_SAFE_ERROR_MESSAGES or bool(_HTTP_STATUS_PHRASE_RE.match(out))


# --- The exact two strings the round-2 reviewer broke round 1 with --------

def test_bare_turso_hostname_never_appears_in_output():
    out = _safe_error(
        "connection to ppc-azaan-faisal606.aws-ap-northeast-1.turso.io failed"
    )
    assert "ppc-azaan-faisal606.aws-ap-northeast-1.turso.io" not in out
    assert "turso.io" not in out
    assert _is_allowlisted(out)


def test_percent_encoded_path_never_appears_in_output():
    out = _safe_error("failed to open %2Fhome%2Fazaan%2Fsecret%2Fpath.py")
    assert "%2Fhome%2Fazaan%2Fsecret%2Fpath.py" not in out
    assert "secret" not in out
    assert _is_allowlisted(out)


# --- Recognised failure classes yield a specific, useful phrase -----------

def test_scrape_incomplete_yields_expected_phrase():
    out = _safe_error("ScrapeIncomplete: czone returned 0 products")
    assert out == "Scrape did not finish — some pages could not be read"


def test_host_blocked_yields_expected_phrase():
    out = _safe_error(
        "HostBlocked: junaidtech.pk returned 429 on 5 consecutive fetches "
        "— giving up on this host for the rest of the run"
    )
    assert out == "Site blocked automated requests during this run"


def test_host_blocked_mid_run_literal_yields_expected_phrase():
    out = _safe_error("host blocked mid-run: amdhouse.pk")
    assert out == "Site blocked automated requests during this run"


def test_returned_0_products_yields_expected_phrase():
    out = _safe_error("returned 0 products")
    assert out == "Scraper completed but found no listings"


def test_returned_0_prebuilts_yields_expected_phrase():
    out = _safe_error("returned 0 prebuilts")
    assert out == "Scraper completed but found no listings"


def test_timeout_error_yields_expected_phrase():
    out = _safe_error("TimeoutError: read of https://czone.com.pk/gpu exceeded READ_DEADLINE=30s")
    assert out == "Site did not respond in time"


def test_runtime_error_with_http_status_yields_templated_status():
    out = _safe_error("RuntimeError: Failed to fetch https://czone.com.pk/gpu: HTTP 403")
    assert out == "Fetch failed — site returned HTTP 403"
    # The URL that carried the status code must never survive alongside it.
    assert "czone.com.pk" not in out


def test_runtime_error_without_http_status_yields_generic_fetch_phrase():
    out = _safe_error(
        "RuntimeError: Failed to fetch https://zestro.pk/x after 3 attempts: "
        "<urlopen error [Errno 110] Connection timed out>"
    )
    assert out == "Repeated fetch failures against this site"
    assert "zestro.pk" not in out
    assert "Errno 110" not in out


# --- Unrecognised input: generic message, none of the original words ------

def test_unrecognised_error_yields_generic_message_with_no_original_words():
    out = _safe_error("ValueError: unexpected token 'garbanzo' in JSON at position 42")
    assert out == _SAFE_ERROR_FALLBACK
    for word in ("ValueError", "garbanzo", "JSON", "42"):
        assert word not in out


# --- Property test: output is drawn ONLY from the allowlisted set ---------

_ADVERSARIAL_INPUTS = [
    # The two exact reviewer-broke-it strings.
    "connection to ppc-azaan-faisal606.aws-ap-northeast-1.turso.io failed",
    "failed to open %2Fhome%2Fazaan%2Fsecret%2Fpath.py",
    # Unicode homoglyphs standing in for ASCII letters/digits — evades any
    # ASCII-only path/SQL/token pattern by construction.
    "ѕeсret ассess: /home/аzааn/.env",  # Cyrillic look-alikes
    "ЅЕLЕCT * FRОM parts WHERE token = 'хyz'",  # Cyrillic SELECT/FROM
    "раss=hunter2 at 127．0．0．1",  # fullwidth dot U+FF0E instead of '.'
    # A bare JWT and a long secret-shaped token, no path/URL wrapper at all.
    "eyJhbGciOiJFZERTQSJ9.eyJhIjoicncifQ.sig-secret-payload-follows",
    "api key rejected: synthetic-token-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789",
    # A 5,000-char string with no recognisable structure.
    "x" * 5000,
    # A 5,000-char string built from a repeated "looks safe" phrase, to make
    # sure length alone can't smuggle something recognisable-but-wrong past
    # the allowlist.
    ("ScrapeIncomplete but not really " * 200),
]


def test_adversarial_inputs_are_all_drawn_from_the_allowlist():
    for raw in _ADVERSARIAL_INPUTS:
        out = _safe_error(raw)
        assert out is not None
        assert _is_allowlisted(out), f"input {raw!r} produced non-allowlisted output {out!r}"
        # Belt-and-suspenders: none of the adversarial payload's distinctive
        # substrings should appear in what actually got returned.
        assert "turso.io" not in out
        assert "secret" not in out
        assert "hunter2" not in out
        assert "eyJ" not in out
        assert "sk_live_" not in out


# --- Edge cases carried over from round 1 (still true under the rewrite) --

def test_empty_exception_message_produces_generic_non_empty_output():
    out = _safe_error("")
    assert out == _SAFE_ERROR_FALLBACK

    out_ws = _safe_error("   ")
    assert out_ws == _SAFE_ERROR_FALLBACK


def test_none_still_means_no_error():
    # A genuinely absent error (DB column NULL / run succeeded) must stay
    # None, not get turned into a generic message — that would fabricate a
    # failure.
    assert _safe_error(None) is None


def test_very_long_error_is_capped():
    msg = "connection reset by peer while reading response " * 20
    assert len(msg) > _SAFE_ERROR_MAX_LEN
    out = _safe_error(msg)
    assert len(out) <= _SAFE_ERROR_MAX_LEN
    # Unrecognised shape either way, so it must be the generic fallback.
    assert out == _SAFE_ERROR_FALLBACK


def test_full_detail_still_reaches_storage_layer(tmp_path):
    """
    _safe_error only guards the /api/stats response. The scrape_runs row
    itself — what Database.record_scrape_run writes and Database.source_health
    reads back — must still carry the complete, unredacted error text,
    otherwise there is nothing left for anyone debugging a real failure to
    look at. (Full detail additionally reaches the server-side log via
    logger.error inside _safe_error — not re-verified here since that
    requires a log-capture fixture; this test covers the DB-storage claim.)
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


def test_full_detail_reaches_server_log(caplog):
    """
    Companion to test_full_detail_still_reaches_storage_layer: verifies the
    other half of the docstring's claim — that the untruncated detail is
    also logged server-side, not just written to scrape_runs.
    """
    import logging

    jwt = "eyJhbGciOiJFZERTQSJ9.eyJhIjoicncifQ.sig"
    full_error = f"SELECT * FROM parts WHERE token = {jwt} -- /home/azaan/secret/path.py"

    with caplog.at_level(logging.ERROR, logger="backend.routers.parts"):
        _safe_error(full_error)

    assert any(full_error in record.getMessage() for record in caplog.records)
