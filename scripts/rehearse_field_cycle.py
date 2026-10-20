"""Exercise the public CLI from planting to season review using synthetic records.

Run from a checkout: python -S -m scripts.rehearse_field_cycle --output-dir NEW_PATH
No existing database, network, provider, chat model or test fixtures are used.
"""

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOTICE = ("SYNTHETIC SOFTWARE REHEARSAL. No real grower, field observations, local calibration, "
          "irrigation recommendation or field validation. Timezone selection does not localize agronomy.")


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


class Rehearsal:
    def __init__(self, directory, timezone, installed_python=None):
        self.directory = Path(directory).absolute()
        # Never resume into an existing folder: it may contain a real farm database.
        self.directory.mkdir(mode=0o700)
        self.timezone = timezone
        self.language = 'fr' if timezone == 'Africa/Ouagadougou' else 'zh-Hant'
        self.steps = []
        self.fixtures = {}
        self.task_runs = {}
        self.unit_id = None
        self.summary = {}
        self.installed_python = str(Path(installed_python).absolute()) if installed_python else None
        self.runtime = dict(mode='installed' if installed_python else 'checkout')
        self.save_manifest("running")

    def save_manifest(self, status, error=None):
        write_json(self.directory / "rehearsal.json", dict(
            schema_version=3, status=status, notice=NOTICE, timezone=self.timezone, task_language=self.language,
            runtime=self.runtime,
            field_validation_performed=False, input_files=self.fixtures,
            steps=self.steps, guided_tasks=self.task_runs, summary=self.summary, error=error))

    def example(self, name):
        path = ROOT / "examples" / name
        content = path.read_bytes()
        self.fixtures[name] = dict(sha256=hashlib.sha256(content).hexdigest(), path=str(path))
        return json.loads(content)

    def environment(self):
        env = dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
        if self.installed_python:
            for key in ('PYTHONPATH', 'PYTHONHOME', 'AGRONAUT_CORPUS_DIR', 'AGRONAUT_DATA_DIR', 'AGRONAUT_CACHE_DIR'):
                env.pop(key, None)
            env.update(XDG_DATA_HOME=str(self.directory/'runtime-data'), XDG_CACHE_HOME=str(self.directory/'runtime-cache'))
        else:
            env['PYTHONPATH'] = str(ROOT)
        return env

    def inspect_installation(self):
        """Reject editable/check-out imports before calling a claimed installed runtime."""
        code = '''
import json, sys
from pathlib import Path
import agronaut_agent.cli, agro_model.water
from agronaut_agent.paths import corpus_root
prefix = Path(sys.prefix).resolve()
modules = {module.__name__: str(Path(module.__file__).resolve())
           for module in (agronaut_agent.cli, agro_model.water)}
corpus = corpus_root().resolve()
assert sys.prefix != sys.base_prefix, 'Use a separate virtual environment'
assert all(Path(path).is_relative_to(prefix) for path in modules.values()), 'Modules are outside the installation'
assert corpus.is_relative_to(prefix) and (corpus/'field_guides/planning_questions.json').is_file(), 'Installed corpus missing'
print(json.dumps(dict(python=sys.executable, prefix=str(prefix), modules=modules, corpus=str(corpus))))
'''
        result = subprocess.run([self.installed_python, '-I', '-c', code], cwd=self.directory,
            env=self.environment(), text=True, capture_output=True, timeout=30)
        if result.returncode:
            raise RuntimeError('Installed runtime verification failed: '+(result.stderr or result.stdout))
        self.runtime.update(json.loads(result.stdout))
        self.save_manifest('running')

    def call(self, label, group, operation, data=None, *, expect_error=False):
        stem = f"{len(self.steps) + 1:03d}-{label}"
        args = [self.installed_python or sys.executable, "-I" if self.installed_python else "-S", "-m", "agronaut_agent.cli", group,
                "--db", str(self.directory / "synthetic.sqlite3"), "--user", "synthetic-rehearsal"]
        if self.unit_id and group in ("crop", "field"):
            args += ["--unit", self.unit_id]
        args.append(operation)
        if data is not None:
            input_path = self.directory / (stem + "-input.json")
            write_json(input_path, data)
            args += ["--input", str(input_path)]
        result = subprocess.run(args, cwd=self.directory if self.installed_python else ROOT,
            env=self.environment(), text=True, capture_output=True, timeout=60)
        output_name = stem + "-output.json"
        try:
            output = json.loads(result.stdout)
        except ValueError as exc:
            raise RuntimeError(f"{label}: CLI did not return JSON: {result.stderr or result.stdout}") from exc
        write_json(self.directory / output_name, output)
        self.steps.append(dict(label=label, argv=args, returncode=result.returncode, output=output_name))
        self.save_manifest("running")
        expected = 2 if expect_error else 0
        if result.returncode != expected:
            raise RuntimeError(f"{label}: expected exit {expected}, got {result.returncode}: {output}")
        return output

    def equipment(self, budget):
        supplied = self.example("drip_design.json")
        answers = dict(task="drip_design", cycle_id=budget["cycle_id"], application_input="budget",
            target_calculation=budget["id"], target_day="2026-07-06", target_accepted="use",
            target_reason="Synthetic 500 m2 full-layout sizing rehearsal; uniform soil bucket does not model localized drip wetting.")
        for section, fields in supplied.items():
            if not isinstance(fields, dict):
                continue
            for key, value in fields.items():
                name = f"{section}.{key}"
                if name in ("emitter.flow_tolerance_fraction", "application.wetted_fraction"):
                    value *= 100  # The documented wizard asks for percentages.
                answers[name] = "yes" if value is True else str(value)
        draft = self.call("equipment-start", "field", "start_intake")
        for _ in range(100):
            key = draft["next_field"]
            if key is None:
                break
            if key not in answers:
                raise RuntimeError(f"New equipment question needs an explicit rehearsal answer: {key}")
            draft = self.call("equipment-" + key.replace(".", "-"), "field", "answer_intake",
                dict(answer=answers[key], expected_revision=draft["revision"]))
        else:
            raise RuntimeError("Equipment consultation did not finish within 100 answers")
        return self.call("equipment-save", "field", "save_intake",
            dict(draft_id=draft["draft_id"], expected_revision=draft["revision"]))["calculation"]

    def guided_task(self, label, answers, selections):
        """Choose records from public numbered lists, resume, explicitly save and retry."""
        draft = self.call(label+'-start', 'crop', 'start_task', dict(language=self.language))
        selected = {}
        option_fields = dict(trigger='source_options', record_id='task_options', evidence='evidence_options')
        for _ in range(100):
            key = draft['next_field']
            if key is None:
                break
            if key in selections:
                target = selections[key]
                for _ in range(1000):
                    page = draft[option_fields[key]]
                    match = next((item for item in page['items'] if all(item.get(k) == v for k, v in target.items())), None)
                    if match is not None:
                        break
                    if page['page'] == page['pages']:
                        raise RuntimeError(f'{label}: expected record not offered for {key}: {target}')
                    draft = self.call(f'{label}-{key}-page-{page["page"]+1}', 'crop', 'answer_task',
                        dict(answer='next', expected_revision=draft['revision']))
                else:
                    raise RuntimeError(f'{label}: record selection exceeded 1000 pages')
                resumed = self.call(label+'-'+key+'-resume', 'crop', 'get_task')
                if resumed != draft:
                    raise RuntimeError(f'{label}: resuming changed the displayed {key} choices')
                selected[key] = target
                value = str(match['number'])
            else:
                if key not in answers:
                    raise RuntimeError(f'{label}: new task question needs an explicit rehearsal answer: {key}')
                value = answers[key]
            draft = self.call(label+'-'+key, 'crop', 'answer_task', dict(answer=value, expected_revision=draft['revision']))
        else:
            raise RuntimeError(f'{label}: task consultation did not finish within 100 answers')
        request = dict(draft_id=draft['draft_id'], expected_revision=draft['revision'])
        saved = self.call(label+'-save', 'crop', 'save_task', request)
        retry = self.call(label+'-retry', 'crop', 'save_task', request)
        if saved != retry:
            raise RuntimeError(f'{label}: task save retry changed the receipt')
        self.task_runs[label] = dict(language=saved['language'], selected_records=selected,
            record_id=saved['record']['id'], save_retry_identical=True)
        return saved['record']

    def run(self):
        if self.installed_python:
            self.inspect_installation()
        farm = self.example("soil_unit.json")
        farm["farm"] = dict(name="SYNTHETIC rehearsal farm", timezone=self.timezone)
        farm["unit"]["name"] = "SYNTHETIC 500 m2 tomato field"
        created = self.call("farm", "farm", "create", farm)
        self.unit_id = created["created"]["id"]
        planted = self.call("planting", "crop", "create", dict(crop="tomato", planted_on="2026-04-01",
            source="SYNTHETIC planting record for software rehearsal", request_id="planting"))
        cycle = planted["cycle"]["id"]

        inputs = self.example("field_water.json")
        inputs["area_m2"] = 500
        inputs["site"] = dict(name="SYNTHETIC field; FAO example inputs are not local weather", timezone=self.timezone)
        base = self.call("water-budget", "field", "save", dict(task="root_zone_budget", inputs=inputs,
            cycle_id=cycle, request_id="budget"))
        equipment = self.equipment(base)
        before_actions = self.call("after-design", "field", "list")

        reading = self.call('point-reading', 'crop', 'record_measurement', dict(cycle_id=cycle, request_id='reading',
            data=dict(quantity='soil_water_content', value=0.25, unit='m3/m3', observed_at='2026-07-05T12:00:00+00:00',
                source='SYNTHETIC point reading for decision trace; not a whole-root measurement', provenance='reported')))
        task = self.guided_task('plan-irrigation-record', dict(action='plan', cycle_id=cycle, entry='irrigation-record',
            title='Record the synthetic performed irrigation', activity='irrigation', basis='date',
            start='2026-07-06', end='2026-07-06', location='SYNTHETIC field', priority='normal',
            notes='Exercise record linkage; not an instruction to irrigate.', source='SYNTHETIC operator notebook',
            trigger_reason='SYNTHETIC operator links a prior point reading for audit; no threshold or irrigation recommendation.'),
            dict(trigger=dict(kind='measurement', id=reading['id'])))
        water_data = dict(day="2026-07-06", rainfall_mm=5, net_irrigation_mm=3, runoff_mm=1,
            source="SYNTHETIC completed-day totals; not measurements", provenance="reported", quality="unverified")
        actual = self.call("water-record", "field", "record_day", dict(cycle_id=cycle, data=water_data, request_id="water"))
        blocked = self.call("scenario-replay-blocked", "field", "replay",
            dict(calculation_id=base["id"], water_record_ids=[actual["id"]], request_id="blocked"), expect_error=True)
        # This is an observed-shaped TEST record, not a conversion of scenarios into real evidence.
        weather = dict(inputs["weather"][0], kind="observed",
            source="SYNTHETIC observed-shaped meteorology for software rehearsal; FAO example numbers, no station or field observation")
        observed = self.call("weather-replacement", "field", "revise_weather",
            dict(calculation_id=base["id"], weather=[weather], request_id="weather"))
        replay = self.call("water-replay", "field", "replay", dict(calculation_id=observed["id"],
            water_record_ids=[actual["id"]], request_id="replay"))
        completed = self.guided_task('complete-task', dict(action='completed', completed_on='2026-07-06',
            reason='SYNTHETIC operator confirms performed work', source='SYNTHETIC notebook'),
            dict(record_id=dict(id=task['id']), evidence=dict(kind='water_day', id=actual['id'])))
        corrected = self.call("correct-water", "field", "record_day", dict(cycle_id=cycle,
            data=dict(water_data, net_irrigation_mm=4, source="SYNTHETIC corrected notebook total"),
            supersedes_id=actual["id"], request_id="water-corrected"))
        reading_review = self.call('review-point-reading', 'crop', 'review_measurement', dict(observation_id=reading['id'],
            quality='checked', reason='SYNTHETIC notebook reviewed after task completion; no field verification',
            source='SYNTHETIC reviewer', request_id='reading-review', expected_revision=None))
        stale_review = self.call("review-before-adoption", "crop", "season_review",
            dict(cycle_id=cycle, calculation_id=replay["id"]))
        latest_args = dict(calculation_id=replay["id"], water_record_ids=[corrected["id"]], request_id="replay-corrected")
        latest = self.call("adopt-water-correction", "field", "replay", latest_args)
        retried = self.call("retry-water-correction", "field", "replay", latest_args)
        current_task = self.guided_task('refresh-task-evidence', dict(action='completed', completed_on='2026-07-06',
            reason='SYNTHETIC corrected daily total reviewed', source='SYNTHETIC notebook'),
            dict(record_id=dict(id=completed['id']), evidence=dict(kind='water_day', id=corrected['id'])))

        self.call("harvest", "crop", "record_harvest", dict(cycle_id=cycle, request_id="harvest",
            data=dict(batch="synthetic-batch", day="2026-07-10", quantity=12.5, unit="kg", product="tomato fruit",
                basis="fresh", grade="ungraded", provenance="reported", source="SYNTHETIC one-batch notebook; not full-season yield")))
        self.call("close-cycle", "crop", "close_cycle", dict(cycle_id=cycle, ended_on="2026-07-10",
            reason="SYNTHETIC operator closes cycle", source="SYNTHETIC notebook", request_id="close", expected_revision=None))
        final = self.call("season-review", "crop", "season_review", dict(cycle_id=cycle, calculation_id=latest["id"]))
        original = self.call("original-budget", "field", "get", dict(calculation_id=base["id"]))
        listing = self.call("final-field-records", "field", "list")

        def depletion(calculation):
            return calculation["result"]["result"]["days"][0]["balance"]["values"]["depletion_mm"]

        def actual_et(calculation):
            return calculation["result"]["result"]["days"][0]["balance"]["values"]["actual_et_mm"]

        checks = dict(
            design_created_no_water_records=before_actions["water_days"] == [],
            equipment_keeps_budget_snapshot=equipment["water_target"]["calculation_snapshot"] == base,
            limited_supply_design_is_infeasible=equipment["result"]["feasible"] is False,
            scenario_cannot_be_replayed="observed meteorology" in blocked.get("error", ""),
            original_budget_is_immutable=original == base,
            # 60 - (5 rain - 1 runoff) - 3/4 irrigation = 53/52 mm before ET.
            # Adding water relieves stress: ET need not stay at the base value.
            first_water_balance=math.isclose(depletion(replay) - actual_et(replay), 53, abs_tol=1e-9),
            corrected_water_balance=math.isclose(depletion(latest) - actual_et(latest), 52, abs_tol=1e-9),
            stress_relief_changes_et=actual_et(replay) > actual_et(base),
            retry_does_not_duplicate=latest == retried and len(listing["calculations"]) == 5,
            corrected_evidence_was_flagged=stale_review["journal"]["calendar"]["tasks"][0]["completion_evidence_state"] == "superseded",
            refreshed_evidence_is_current=final["journal"]["calendar"]["tasks"][0]["completion_evidence_state"] == "current",
            one_current_water_day=final["water"]["coverage"]["recorded_days"] == 1,
            missing_days_remain_unknown=final["water"]["coverage"]["missing_days"] == 100,
            corrected_total_not_added=final["water"]["totals"][0]["net_irrigation_mm"] == "4",
            one_harvest_batch=final["journal"]["harvest_totals"][0]["quantity"] == "12.5",
            closure_is_explicit=final["journal"]["lifecycle"]["status"] == "closed",
            guided_task_language_retained=all(run['language'] == self.language for run in self.task_runs.values()),
            guided_task_retries_are_identical=len(self.task_runs) == 3 and all(run['save_retry_identical'] for run in self.task_runs.values()),
            point_reading_link_is_explicit=task['data']['trigger']['id'] == reading['id'] and task['data']['trigger']['review_revision_id'] is None,
            original_point_quality_preserved=current_task['data']['trigger_snapshot']['effective_quality'] == 'unverified'
                and current_task['data']['trigger_snapshot']['quality_review'] is None,
            changed_source_review_was_flagged=stale_review['journal']['calendar']['tasks'][0]['trigger_review']['state'] == 'review_changed',
            completion_adoption_does_not_clear_source_review=final['journal']['calendar']['tasks'][0]['trigger_review']['needs_review']
                and final['journal']['calendar']['tasks'][0]['trigger_review']['state'] == 'review_changed',
            original_completion_snapshot_preserved=completed['data']['resolution']['completion_record']['snapshot'] == actual,
            corrected_completion_snapshot_is_explicit=current_task['data']['resolution']['completion_record']['snapshot'] == corrected,
        )
        self.summary = dict(checks=checks, unit_id=self.unit_id, cycle_id=cycle,
            base_depletion_mm=depletion(base), replay_depletion_mm=depletion(replay), corrected_depletion_mm=depletion(latest),
            equipment_feasible=equipment["result"]["feasible"], coverage=final["water"]["coverage"],
            harvest_totals=final["journal"]["harvest_totals"],
            task_trace=dict(source_record_id=reading['id'], source_review_id=reading_review['id'],
                original_task_id=task['id'], first_completion_id=completed['id'], current_task_id=current_task['id'],
                original_water_id=actual['id'], corrected_water_id=corrected['id'],
                source_review=final['journal']['calendar']['tasks'][0]['trigger_review']),
            limits=["No records for the other 100 days; no season water total, yield or savings claim.",
                "Drip design remains infeasible under the supplied limits; no hardware is controlled.",
                "The correction updates a past-day balance, not a new future irrigation schedule.",
                "The task's original point-reading review changed and remains flagged; adopting completion evidence does not adopt that source review.",
                "The point reading is not assimilated into the soil-water model. Task links do not establish an irrigation threshold or causal response.",
                "All meteorology, planting, performed actions and harvest records in this database are synthetic."])
        failed = [name for name, passed in checks.items() if not passed]
        if failed:
            raise RuntimeError("Rehearsal checks failed: " + ", ".join(failed))
        self.save_manifest("passed")
        return self.summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, help="new directory for a synthetic database, CLI inputs and outputs")
    parser.add_argument("--timezone", choices=("Africa/Ouagadougou", "Asia/Taipei"), default="Africa/Ouagadougou",
                        help="exercise farm-local dates only; no regional crop/weather assumptions")
    parser.add_argument('--installed-python', help='Python executable in a separate wheel-installed virtual environment; fixtures still come from this checkout')
    args = parser.parse_args(argv)
    rehearsal = None
    try:
        rehearsal = Rehearsal(args.output_dir, args.timezone, args.installed_python)
        summary = rehearsal.run()
        print(json.dumps(dict(status="passed", notice=NOTICE, report=str(rehearsal.directory / "rehearsal.json"),
                              checks=len(summary["checks"])), ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError) as exc:
        if rehearsal:
            rehearsal.save_manifest("failed", str(exc))
        print(json.dumps(dict(status="failed", error=str(exc))))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
