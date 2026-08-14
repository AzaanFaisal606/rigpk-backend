"""
A junk source name must fail loudly.

Before this guard, `python run_all.py rm -rf` scraped nothing, exited 0, and
the orchestrator treated the empty result as a normal run. Combined with the
shell interpolation in scrape.yml this was also an injection point.
"""
import pytest

from run_all import parse_sources


def test_known_sources_pass_through():
    assert parse_sources(["czone", "junaid"]) == ["czone", "junaid"]


def test_empty_means_all_sources():
    result = parse_sources([])
    assert len(result) >= 9, "empty input must expand to every registered source"


def test_unknown_source_exits_nonzero():
    with pytest.raises(SystemExit) as exc:
        parse_sources(["czone", "definitely-not-a-retailer"])
    assert exc.value.code != 0


def test_shell_metacharacters_are_rejected():
    with pytest.raises(SystemExit):
        parse_sources(["czone; rm -rf /"])
