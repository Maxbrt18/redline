"""La porte après push couvre le SHA exact et tous ses workflows, y compris le vault."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def run(workflow="Code checks", *, run_id=1, status="completed", conclusion="success"):
    return {"workflowName": workflow, "databaseId": run_id, "status": status,
            "conclusion": conclusion, "url": f"https://github.test/actions/runs/{run_id}"}


@pytest.fixture
def gate(tmp_path):
    jq = shutil.which("jq")
    assert jq, "jq est requis pour vérifier le filtre GitHub réellement utilisé"
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    gh = tmp_path / "gh"
    gh.write_text("""#!/usr/bin/env python3
import json, os, subprocess, sys
from pathlib import Path
root = Path(os.environ['FAKE_GH_ROOT'])
args = sys.argv[1:]
if args[:2] == ['run', 'list']:
    assert args[args.index('--commit') + 1] == os.environ['EXPECTED_SHA']
    assert '--workflow' not in args
    if (root / 'fail-api').exists():
        print('API indisponible', file=sys.stderr)
        sys.exit(7)
    counter = root / 'counter'
    index = int(counter.read_text()) if counter.exists() else 0
    counter.write_text(str(index + 1))
    snapshots = json.loads((root / 'runs.json').read_text())
    result = subprocess.run([os.environ['REAL_JQ'], '-r', args[args.index('--jq') + 1]],
                            input=json.dumps(snapshots[min(index, len(snapshots)-1)]),
                            text=True)
    sys.exit(result.returncode)
if args[:2] == ['run', 'view']:
    with (root / 'logs-read').open('a') as f: f.write(args[2] + '\\n')
    print('journal du contrôle rouge')
    sys.exit(0)
sys.exit('appel gh inattendu')
""")
    gh.chmod(0o755)

    def invoke(snapshots, *required, api_error=False, wait="1"):
        (tmp_path / "runs.json").write_text(json.dumps(snapshots))
        if api_error:
            (tmp_path / "fail-api").touch()
        env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
               "FAKE_GH_ROOT": str(tmp_path), "EXPECTED_SHA": sha, "REAL_JQ": jq,
               "REDLINE_CI_WAIT_SECONDS": wait}
        return subprocess.run(["bash", "scripts/ci-gate.sh", sha, *required],
                              cwd=ROOT, env=env, text=True, capture_output=True, timeout=10)

    return invoke, tmp_path


def test_code_vert_et_vault_rouge_font_echouer_la_porte(gate):
    invoke, path = gate
    result = invoke([[run(), run("Vault content", run_id=2, conclusion="failure")]])
    assert result.returncode == 1
    assert "Vault content failure" in result.stderr
    assert (path / "logs-read").read_text() == "2\n"
    assert "Tous les workflows déclenchés sont verts" not in result.stdout


def test_tous_les_workflows_comptent_y_compris_la_sauvegarde(gate):
    invoke, _ = gate
    result = invoke([[run(), run("Vault content", run_id=2),
                      run("Sauvegarde PostgreSQL hors VPS", run_id=3, conclusion="failure")]])
    assert result.returncode == 1
    assert "Sauvegarde PostgreSQL hors VPS failure" in result.stderr


def test_la_derniere_relance_verte_remplace_un_ancien_echec(gate):
    invoke, path = gate
    result = invoke([[run(), run("Vault content", run_id=2, conclusion="failure"),
                      run("Vault content", run_id=3)]])
    assert result.returncode == 0
    assert not (path / "logs-read").exists()


def test_une_relance_en_cours_ne_peut_pas_utiliser_l_ancien_vert(gate):
    invoke, _ = gate
    result = invoke([[run(), run(run_id=2, status="in_progress", conclusion="")]])
    assert result.returncode == 1
    assert "délai dépassé" in result.stderr


def test_un_workflow_requis_absent_ne_vaut_pas_succes(gate):
    invoke, _ = gate
    result = invoke([[run()]], "Vault content")
    assert result.returncode == 1
    assert "workflow requis absent : Vault content" in result.stderr


def test_un_workflow_non_declenche_n_est_pas_exige(gate):
    invoke, _ = gate
    result = invoke([[run()]])
    assert result.returncode == 0


def test_les_runs_crees_apres_le_push_sont_attendus(gate):
    invoke, _ = gate
    # Le délai doit couvrir un second sondage (2 s plus tard). À 1 s, `SECONDS` en bash
    # pouvait franchir sa borne pendant le premier appel au faux gh : rouge au hasard.
    result = invoke([[], [run(), run("Vault content", run_id=2)]], "Vault content",
                    wait="8")
    assert result.returncode == 0


def test_code_checks_reste_requis_meme_si_le_vault_est_vert(gate):
    invoke, _ = gate
    result = invoke([[run("Vault content")]])
    assert result.returncode == 1
    assert "workflow requis absent : Code checks" in result.stderr


@pytest.mark.parametrize("conclusion", ["cancelled", "skipped", "timed_out", "neutral"])
def test_seul_un_verdict_success_est_vert(gate, conclusion):
    invoke, _ = gate
    result = invoke([[run(conclusion=conclusion)]])
    assert result.returncode == 1


def test_une_erreur_de_l_api_ne_vaut_pas_absence_ou_succes(gate):
    invoke, _ = gate
    result = invoke([[]], api_error=True)
    assert result.returncode == 7
    assert "API indisponible" in result.stderr
