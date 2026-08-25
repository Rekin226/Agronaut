"""Packaging: `pip install -e .` must put a real `agronaut` command on PATH, and that
command must work from any directory.

The second half is not cosmetic. The knowledge base and the URL corpus were addressed
relative to the current directory, which is fine for `python -m` from the repo root and
silently wrong for an installed command run from anywhere else: RAG degrades to
KNOWLEDGE_UNAVAILABLE with only a log line to show for it.
"""

import pathlib
import shutil
import subprocess
import sys
import tomllib
import zipfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def pyproject():
    path = ROOT / "pyproject.toml"
    assert path.is_file(), "pyproject.toml is what makes `agronaut` installable"
    return tomllib.loads(path.read_text())


def test_declares_the_agronaut_console_script(pyproject):
    scripts = pyproject["project"]["scripts"]
    assert scripts["agronaut"] == "agronaut_agent.cli:main"


def test_ships_every_package_the_command_imports(pyproject):
    packages = set(pyproject["tool"]["setuptools"]["packages"]["find"]["include"])
    for pkg in ("agronaut_agent*", "agent*", "aqua_model*", "srcs*", "skills*", "scripts*"):
        assert pkg in packages, f"{pkg} missing — an installed `agronaut` would fail to import"


def test_ships_the_bot_and_app_entry_modules(pyproject):
    modules = set(pyproject["tool"]["setuptools"]["py-modules"])
    assert {"bot", "app"} <= modules


def test_knowledge_corpus_resolves_from_any_directory(tmp_path, monkeypatch):
    from srcs import chatbot
    monkeypatch.chdir(tmp_path)
    assert pathlib.Path(chatbot.URL_FILE).is_file()
    assert pathlib.Path(chatbot.KNOWLEDGE_DIR).is_dir()
    assert any(pathlib.Path(chatbot.KNOWLEDGE_DIR).glob("*.md"))


def test_web_cache_does_not_land_in_the_callers_directory(tmp_path, monkeypatch):
    from srcs import chatbot
    monkeypatch.chdir(tmp_path)
    assert pathlib.Path(chatbot.CACHE_NAME).is_absolute()


# --- the corpus has to survive a real install -------------------------------------------
# The tests above only prove the checkout works. A wheel is what `pip install .` produces,
# and it shipped no knowledge/*.md and no urls.txt at all: _PROJECT_ROOT resolved into
# site-packages, the corpus was not there, and every answer came back citing nothing while
# looking healthy. Build the artifact and assert on it.


_BUILD_NOISE = shutil.ignore_patterns(".git", ".venv", "build", "dist", "*.egg-info",
                                      "__pycache__", "*.sqlite", "*.sqlite3", "web_cache.sqlite")


def _build_wheel(tmp_path) -> zipfile.ZipFile:
    """Build a wheel from a clean copy of the tree, with pip's wheel cache disabled.

    Both precautions are load-bearing. A leftover `agronaut.egg-info` in the working tree
    feeds its SOURCES.txt into the build and silently puts the whole test suite back in the
    wheel, and pip will happily hand back a cached wheel built before the fix — either one
    turns this into a test that passes no matter what the packaging config says.
    """
    src = tmp_path / "src"
    shutil.copytree(ROOT, src, ignore=_BUILD_NOISE)
    out = tmp_path / "wheel"
    subprocess.run(
        [sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
         "--no-cache-dir", "-q", "-w", str(out), str(src)],
        check=True, capture_output=True,
    )
    wheels = list(out.glob("*.whl"))
    assert len(wheels) == 1, f"expected one wheel, got {wheels}"
    return zipfile.ZipFile(wheels[0])


def test_wheel_ships_the_cited_knowledge_corpus(tmp_path):
    names = _build_wheel(tmp_path).namelist()
    md = [n for n in names if n.endswith(".md") and "knowledge" in n]
    assert len(md) >= 20, f"knowledge corpus missing from the wheel (found {len(md)} docs)"
    assert any(n.endswith("urls.txt") for n in names), "urls.txt missing from the wheel"


def test_wheel_does_not_ship_the_test_suite(tmp_path):
    names = _build_wheel(tmp_path).namelist()
    tests = [n for n in names if "/tests/" in n]
    assert tests == [], f"wheel ships {len(tests)} test files"


# --- writes must not land in the install root -------------------------------------------

def test_cache_and_data_fall_back_to_user_dirs_when_the_root_is_read_only(tmp_path, monkeypatch):
    from agronaut_agent import paths

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("AGRONAUT_CACHE_DIR", raising=False)
    monkeypatch.setattr(paths, "PROJECT_ROOT", tmp_path / "not-writable")

    assert paths.cache_dir() == tmp_path / "cache" / "agronaut"
    assert paths.data_dir() == tmp_path / "data" / "agronaut"


def test_a_writable_site_packages_is_still_not_where_state_goes(tmp_path, monkeypatch):
    # The discriminator cannot be "is the root writable" — site-packages in a user-owned
    # venv is writable, and that is exactly where the memory DB and the fetched-page cache
    # must not go: wiped on uninstall, shared between users, bloating container layers.
    from agronaut_agent import paths

    fake_site_packages = tmp_path / "site-packages"
    (fake_site_packages / "agronaut_agent").mkdir(parents=True)   # installed, no pyproject.toml
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("AGRONAUT_CACHE_DIR", raising=False)
    monkeypatch.delenv("AGRONAUT_DATA_DIR", raising=False)
    monkeypatch.setattr(paths, "PROJECT_ROOT", fake_site_packages)

    assert paths.cache_dir() == tmp_path / "cache" / "agronaut"
    assert paths.data_dir() == tmp_path / "data" / "agronaut"


def test_a_checkout_keeps_its_state_beside_the_source(tmp_path, monkeypatch):
    from agronaut_agent import paths

    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "pyproject.toml").write_text("")
    monkeypatch.delenv("AGRONAUT_CACHE_DIR", raising=False)
    monkeypatch.delenv("AGRONAUT_DATA_DIR", raising=False)
    monkeypatch.setattr(paths, "PROJECT_ROOT", checkout)

    assert paths.cache_dir() == checkout
    assert paths.data_dir() == checkout / "data"


def test_corpus_falls_back_to_the_installed_share_dir(tmp_path, monkeypatch):
    from agronaut_agent import paths

    monkeypatch.delenv("AGRONAUT_CORPUS_DIR", raising=False)
    monkeypatch.setattr(paths, "PROJECT_ROOT", tmp_path / "no-corpus-here")
    monkeypatch.setattr(paths.sys, "prefix", str(tmp_path / "venv"))
    assert paths.corpus_root() == tmp_path / "venv" / "share" / "agronaut"


def test_corpus_env_override_wins(tmp_path, monkeypatch):
    from agronaut_agent import paths

    monkeypatch.setenv("AGRONAUT_CORPUS_DIR", str(tmp_path / "elsewhere"))
    assert paths.corpus_root() == tmp_path / "elsewhere"
