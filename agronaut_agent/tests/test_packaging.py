"""Packaging: `pip install -e .` must put a real `agronaut` command on PATH, and that
command must work from any directory.

The second half is not cosmetic. The knowledge base and the URL corpus were addressed
relative to the current directory, which is fine for `python -m` from the repo root and
silently wrong for an installed command run from anywhere else: RAG degrades to
KNOWLEDGE_UNAVAILABLE with only a log line to show for it.
"""

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tomllib
import venv
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
    for pkg in ("agronaut_agent*", "agent*", "aqua_model*", "agro_model*", "srcs*", "skills*", "scripts*"):
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
    wheel = _build_wheel(tmp_path)
    names = wheel.namelist()
    md = [n for n in names if n.endswith(".md") and "knowledge" in n]
    assert len(md) >= 20, f"knowledge corpus missing from the wheel (found {len(md)} docs)"
    for topic in ("water_needs", "choose_method", "drip", "sprinkler", "surface", "crop_scouting", "soil_sampling"):
        assert any(n.endswith(f"knowledge/field_{topic}.md") for n in md)
    assert any(n.endswith("urls.txt") for n in names), "urls.txt missing from the wheel"
    for language in ("fr", "zh-Hant"):
        assert any(n.endswith(f"field_guides/{language}.json") for n in names)
    assert any(n.endswith("field_guides/planning_questions.json") for n in names)
    # The installed safety runner reads fixtures as well as the probe manifest.
    field_set = next(n for n in names if n.endswith("eval/safety_eval/field_set.json"))
    for probe in json.loads(wheel.read(field_set))["probes"]:
        if fixture := probe.get("fixture"):
            assert any(n.endswith(f"/examples/{fixture}") for n in names)
    assert any(n.endswith("eval/retrieval_eval/baseline_all_field_guides_2026_10.json")
               for n in names)
    assert not any(n.endswith(("VISION.md", "AGRICULTURE_PLATFORM_PLAN.md", "AGRICULTURE_CONTRIBUTING.md")) for n in names)
    # Exercise the wheel's modules and data fallback, not the checkout's copies.
    installed = tmp_path/"installed"
    wheel.extractall(installed)
    data_root = next(installed.glob("*.data"))/"data"
    code = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
sys.prefix = sys.argv[2]
from agronaut_agent.field_guides import guide_catalog, read_field_guide
from agronaut_agent.field_planning_language import translate
from agronaut_agent.paths import corpus_root
assert corpus_root() == Path(sys.prefix)/'share/agronaut'
assert 'culture' in translate('Which crop will this calculation describe?', 'fr')
assert '作物' in translate('Which crop will this calculation describe?', 'zh-Hant')
for language in ('en', 'fr', 'zh-Hant'):
    for guide in guide_catalog(language):
        assert read_field_guide(guide['topic'], language)['language'] == language
"""
    subprocess.run([sys.executable, "-S", "-c", code, str(installed), str(data_root)],
                   cwd=tmp_path, check=True, capture_output=True, text=True, timeout=30)


def test_installed_wheel_completes_field_cycle_without_checkout_imports(tmp_path):
    with _build_wheel(tmp_path) as wheel:
        wheel_path = wheel.filename
    environment = tmp_path/'isolated venv'
    venv.EnvBuilder(with_pip=False).create(environment)
    python = environment/('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    subprocess.run([sys.executable, '-m', 'pip', '--python', str(python), 'install',
        '--no-deps', '--no-index', '--no-cache-dir', wheel_path], check=True,
        capture_output=True, text=True, timeout=60)
    # This is an actual dependency-free installation, not wheel extraction or an editable install.
    check = subprocess.run([str(python), '-I', '-c',
        'import importlib.metadata as m; print(sorted(d.metadata["Name"] for d in m.distributions()))'],
        check=True, capture_output=True, text=True, timeout=30)
    assert check.stdout.strip() == "['agronaut']"
    default_db = tmp_path/'existing.sqlite3'
    default_db.write_bytes(b'keep existing data')
    output = tmp_path/'installed rehearsal'
    env = dict(os.environ, AGRONAUT_DB=str(default_db), PYTHONPATH=str(ROOT),
        AGRONAUT_CORPUS_DIR=str(ROOT), AGRONAUT_DATA_DIR=str(tmp_path/'do-not-use'),
        HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
    result = subprocess.run([sys.executable, '-S', str(ROOT/'scripts/rehearse_field_cycle.py'),
        '--output-dir', str(output), '--installed-python', str(python)], cwd=tmp_path,
        env=env, text=True, capture_output=True, timeout=180)
    assert result.returncode == 0, result.stdout+result.stderr
    report = json.loads((output/'rehearsal.json').read_text())
    assert report['status'] == 'passed'
    assert report['runtime']['mode'] == 'installed'
    assert pathlib.Path(report['runtime']['prefix']) == environment.resolve()
    assert all(pathlib.Path(path).is_relative_to(environment.resolve())
        for path in report['runtime']['modules'].values())
    assert pathlib.Path(report['runtime']['corpus']) == environment/'share/agronaut'
    assert len(report['summary']['checks']) == 24
    assert all(report['summary']['checks'].values())
    assert len(report['steps']) == 121
    for step in report['steps']:
        assert step['argv'][:4] == [str(python), '-I', '-m', 'agronaut_agent.cli']
        assert step['argv'][step['argv'].index('--db')+1] == str(output/'synthetic.sqlite3')
    assert report['field_validation_performed'] is False
    final_step = next(step for step in report['steps'] if step['label'] == 'season-review')
    final = json.loads((output/final_step['output']).read_text())
    assert final['water']['coverage']['recorded_days'] == 1
    assert final['water']['coverage']['missing_days'] == 100
    assert final['journal']['calendar']['tasks'][0]['trigger_review']['state'] == 'review_changed'
    assert default_db.read_bytes() == b'keep existing data'
    assert not (tmp_path/'do-not-use').exists()


def test_wheel_does_not_ship_the_test_suite(tmp_path):
    names = _build_wheel(tmp_path).namelist()
    tests = [n for n in names if "/tests/" in n]
    assert tests == [], f"wheel ships {len(tests)} test files"
    for module in ("agro_model/water.py", "agro_model/radiation.py", "agro_model/farm.py", "agro_model/drip.py", "agro_model/sprinkler.py",
                   "agro_model/resources.py", "agronaut_agent/farm_resources.py", "agronaut_agent/resource_cli.py", "agent/resource_ui.py",
                   "agro_model/resource_timing.py", "agent/resource_timing_ui.py",
                   "agro_model/resource_schedule.py", "agent/resource_schedule_ui.py",
                   "agronaut_agent/resource_designs.py", "agent/resource_design_ui.py",
                   "agronaut_agent/resource_outcomes.py", "agent/resource_outcome_ui.py",
                   "agro_model/catch_can.py", "agronaut_agent/catch_io.py", "agent/catch_ui.py",
                   "agro_model/emitter_test.py", "agronaut_agent/emitter_io.py", "agent/emitter_ui.py",
                   "agro_model/soil_profile.py", "agro_model/soil_storage.py", "agro_model/soil_drainage.py", "agro_model/soil_infiltration.py", "agronaut_agent/soil_profile_io.py", "agent/soil_profile_ui.py",
                   "agro_model/soil_evaporation.py", "agro_model/soil_water.py",
                   "agro_model/soil_roots.py",
                   "agro_model/soil_grid.py", "agro_model/soil_replay.py",
                   "agro_model/soil_snapshots.py",
                   "agronaut_agent/coupled_water_io.py",
                   "agronaut_agent/coupled_soil_evaluation.py", "agent/coupled_soil_evaluation_ui.py",
                   "agronaut_agent/surface_water_records.py", "agent/surface_water_ui.py",
                   "agro_model/soil_reconciliation.py", "agronaut_agent/coupled_state_workflow.py", "agent/coupled_state_ui.py",
                   "agronaut_agent/field_guides.py", "agent/field_guides_ui.py",
                   "agent/coupled_water_ui.py",
                   "agro_model/evaporation.py", "agronaut_agent/evaporation_io.py", "agent/evaporation_ui.py",
                   "agronaut_agent/soil_reports.py", "agent/soil_report_ui.py",
                   "agronaut_agent/harvest_reviews.py", "agent/harvest_review_ui.py",
                   "agronaut_agent/resource_ledger.py", "agent/resource_ledger_ui.py",
                   "agronaut_agent/resource_settlement.py", "agent/resource_settlement_ui.py",
                   "agronaut_agent/equipment_calendar.py", "agent/equipment_calendar_ui.py",
                   "agronaut_agent/equipment_schedule.py", "agro_model/equipment_schedule.py",
                   "agronaut_agent/irrigation_zones.py",
                   "agro_model/hydraulics.py", "agronaut_agent/irrigation_io.py",
                   "agro_model/drip_lateral.py", "agronaut_agent/drip_lateral_io.py", "agent/drip_lateral_ui.py",
                   "agro_model/sprinkler_lateral.py", "agronaut_agent/sprinkler_lateral_io.py", "agent/sprinkler_lateral_ui.py",
                   "agro_model/sprinkler_diagnostics.py", "agronaut_agent/sprinkler_diagnostics.py", "agent/sprinkler_diagnostics_ui.py",
                   "agro_model/drip_manifold.py", "agronaut_agent/drip_manifold_io.py",
                   "agro_model/drip_zone.py", "agronaut_agent/drip_zone_io.py", "agent/drip_zone_ui.py",
                   "agro_model/surface.py", "agronaut_agent/surface_io.py",
                   "agro_model/surface_advance.py", "agronaut_agent/surface_advance_io.py", "agent/surface_advance_ui.py",
                   "agro_model/surface_diagnostics.py", "agronaut_agent/surface_diagnostics.py", "agent/surface_diagnostics_ui.py",
                   "agro_model/drip_diagnostics.py", "agronaut_agent/drip_diagnostics.py", "agent/drip_diagnostics_ui.py",
                   "agronaut_agent/field_cli.py", "agronaut_agent/domains/registry.py",
                   "agronaut_agent/domains/adapters.py", "agronaut_agent/farm_cli.py",
                   "agronaut_agent/unit_context.py", "agent/farm_ui.py", "agent/field_ui.py",
                   "agronaut_agent/farm_overview.py", "agronaut_agent/farm_overview_text.py", "agent/farm_overview_ui.py",
                   "agronaut_agent/field_guide_text.py",
                   "agronaut_agent/field_workflow.py", "agronaut_agent/field_workflow_cli.py",
                   "agronaut_agent/scouting_consultation.py", "agronaut_agent/scouting_language.py", "agronaut_agent/unit_setup.py", "agronaut_agent/crop_records.py", "agronaut_agent/cycle_lifecycle.py", "agronaut_agent/crop_establishment.py", "agronaut_agent/cash_records.py",
                   "agronaut_agent/water_log_consultation.py", "agronaut_agent/water_log_language.py", "agronaut_agent/measurement_consultation.py", "agronaut_agent/measurement_language.py", "agronaut_agent/measurement_import.py", "agronaut_agent/planting_consultation.py", "agronaut_agent/planting_language.py",
                   "agronaut_agent/crop_calendar.py", "agent/calendar_ui.py", "agent/cash_ui.py", "agent/season_comparison_ui.py",
                   "agronaut_agent/observation_quality.py", "agronaut_agent/season_review.py",
                   "agro_model/nutrition.py", "agronaut_agent/nutrition_io.py", "agent/nutrition_ui.py", "agent/fertilizer_ui.py", "agronaut_agent/fertilizer_records.py",
                   "agronaut_agent/crop_cli.py", "agent/crop_ui.py", "agent/field_intake.py",
                   "agronaut_agent/weather_provider.py", "agent/weather_ui.py",
                   "agronaut_agent/weather_review.py", "agent/weather_review_ui.py",
                   "agronaut_agent/task_consultation.py", "agronaut_agent/task_language.py",
                   "agronaut_agent/task_triggers.py",
                   "agronaut_agent/harvest_consultation.py", "agronaut_agent/harvest_language.py",
                   "agro_model/crop_coefficients.py", "agent/crop_coefficient_ui.py",
                   "agro_model/soil_state.py", "agronaut_agent/soil_workflow.py", "agent/soil_state_ui.py",
                   "agro_model/soil_evaluation.py", "agronaut_agent/soil_evaluation.py", "agent/soil_evaluation_ui.py",
                   "agent/measurement_ui.py", "agro_model/root_growth.py", "agent/root_growth_ui.py",
                   "agronaut_agent/field_consultation.py", "agronaut_agent/meteorology_consultation.py", "agronaut_agent/equipment_consultation.py", "agronaut_agent/equipment_targets.py", "agronaut_agent/surface_consultation.py",
                   "agronaut_agent/equipment_revisions.py", "agent/equipment_revision_ui.py",
                   "agronaut_agent/field_planning_language.py"):
        assert module in names, f"wheel missing {module}"


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
