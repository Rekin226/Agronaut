"""Packaging: `pip install -e .` must put a real `agronaut` command on PATH, and that
command must work from any directory.

The second half is not cosmetic. The knowledge base and the URL corpus were addressed
relative to the current directory, which is fine for `python -m` from the repo root and
silently wrong for an installed command run from anywhere else: RAG degrades to
KNOWLEDGE_UNAVAILABLE with only a log line to show for it.
"""

import pathlib
import tomllib

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
