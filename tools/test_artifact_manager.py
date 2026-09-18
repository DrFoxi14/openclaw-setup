"""Tests for the pure, side-effect-free parts of artifact_manager.py.

Deliberately scoped: no forking, no sockets held open, no git, no HTTP.
Those paths are exercised by actually using the tool; what's covered here
is the logic that decides *where files land* and *what they're called* —
where a silent mistake would be hard to notice and easy to make.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import artifact_manager as am


# ---------------------------------------------------------------------------
# ext_name — extension normalization
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("py", "py"),
    ("PY", "py"),
    ("html", "html"),
    ("htm", "html"),      # normalized, so LANG_TO_MAIN resolves to index.html
    ("HTM", "html"),
    ("js", "js"),
    ("svg", "svg"),
])
def test_known_extensions_pass_through(raw, expected):
    assert am.ext_name(raw) == expected


@pytest.mark.parametrize("raw", ["exe", "sh", "htmlx", "htmx", "", None, "  "])
def test_unknown_extensions_fall_back_to_txt(raw):
    """Anything not explicitly known becomes .txt rather than being trusted.

    'htmlx' and 'htmx' are the regression cases: an earlier version tested
    `e[:3] == "htm"`, so any string starting with those three letters was
    accepted verbatim and produced a file like main.htmlx that no command
    could then find.
    """
    assert am.ext_name(raw) == "txt"


# ---------------------------------------------------------------------------
# to_extname — the file argument wins over --lang
# ---------------------------------------------------------------------------

def test_lang_used_when_no_file_given():
    assert am.to_extname("py") == "py"


def test_leading_dot_on_lang_is_tolerated():
    assert am.to_extname(".py") == "py"


def test_file_extension_overrides_lang():
    """A save of index.html with the default --lang py must not be saved
    as Python — the real file's extension is the stronger signal."""
    assert am.to_extname("py", "/tmp/index.html") == "html"


def test_file_extension_is_case_insensitive():
    assert am.to_extname(None, "/tmp/INDEX.HTML") == "html"


def test_extensionless_file_falls_back_to_lang():
    assert am.to_extname("md", "/tmp/README") == "md"


def test_every_lang_to_main_key_is_a_known_extension():
    """LANG_TO_MAIN is only ever looked up with an ext_name() result, so a
    key that ext_name can never return would be dead configuration."""
    for key in am.LANG_TO_MAIN:
        assert am.ext_name(key) == key, f"LANG_TO_MAIN key {key!r} is unreachable"


# ---------------------------------------------------------------------------
# artifact_dir — name sanitization and containment
# ---------------------------------------------------------------------------

@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(am, "ARTIFACTS_HOME", tmp_path / "artifacts")
    return tmp_path / "artifacts"


def test_plain_name_is_kept(workspace):
    d, safe = am.artifact_dir("color_slider")
    assert safe == "color_slider"
    assert d == workspace / "color_slider"


def test_alphanumeric_and_dash_underscore_survive(workspace):
    _, safe = am.artifact_dir("Test_1-2")
    assert safe == "Test_1-2"


@pytest.mark.parametrize("hostile", [
    "../../etc/passwd",
    "../secrets",
    "/absolute/path",
    "a/b/c",
])
def test_path_traversal_cannot_escape_the_workspace(workspace, hostile):
    """The whole safety story of `save` rests on this: a name is a folder
    inside the workspace, never a path out of it."""
    d, safe = am.artifact_dir(hostile)
    assert "/" not in safe and ".." not in safe
    assert workspace.resolve() in d.resolve().parents


def test_spaces_and_punctuation_are_stripped(workspace):
    _, safe = am.artifact_dir("my plot!! (final)")
    assert safe == "myplotfinal"


@pytest.mark.parametrize("empty_after_sanitizing", ["", "   ", "!!!", "---", "___", "..", "../.."])
def test_name_that_sanitizes_to_nothing_exits(workspace, empty_after_sanitizing):
    """Better to stop than to silently write into the workspace root."""
    with pytest.raises(SystemExit) as exc:
        am.artifact_dir(empty_after_sanitizing)
    assert exc.value.code == 1


# ---------------------------------------------------------------------------
# State file round-trip
# ---------------------------------------------------------------------------

@pytest.fixture
def state_file(tmp_path, monkeypatch):
    path = tmp_path / ".state.json"
    monkeypatch.setattr(am, "STATE_FILE", path)
    return path


def test_load_state_on_missing_file_returns_empty(state_file):
    assert am.load_state() == {}


def test_state_round_trip(state_file):
    am.save_state({"demo": {"port": 8765, "pid": 123}})
    assert am.load_state() == {"demo": {"port": 8765, "pid": 123}}


def test_corrupt_state_file_returns_empty_instead_of_raising(state_file):
    """A half-written state file must degrade to 'no known servers', not
    crash every subsequent command."""
    state_file.write_text("{not valid json")
    assert am.load_state() == {}


# ---------------------------------------------------------------------------
# pid_alive
# ---------------------------------------------------------------------------

def test_own_pid_is_alive():
    assert am.pid_alive(os.getpid()) is True


def test_implausible_pid_is_not_alive():
    assert am.pid_alive(999_999_999) is False
