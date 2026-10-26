"""Tests for evals/check_regressions.py retrieval gate (synthetic fixtures, no DB or API)."""

import importlib.util
import json
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "check_regressions",
    Path(__file__).resolve().parents[2] / "evals" / "check_regressions.py",
)
cr = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cr)

V = cr.GATED_VARIANT
GOOD = {"recall_at_10": 0.80, "mrr": 0.80, "ndcg_at_10": 0.70}


def _write(path: Path, metrics=None, variant=V):
    path.write_text(json.dumps({variant: metrics if metrics is not None else GOOD}))
    return path


@pytest.fixture
def files(tmp_path):
    return _write(tmp_path / "baselines.json"), _write(tmp_path / "accepted.json")


def test_unchanged_passes(files):
    hard, soft = cr.check_retrieval(*files)
    assert hard == [] and soft == []


def test_drop_within_tolerance_passes(files, tmp_path):
    accepted = _write(tmp_path / "a2.json", {**GOOD, "recall_at_10": 0.78})
    assert cr.check_retrieval(files[0], accepted) == ([], [])


@pytest.mark.parametrize("metric", ["recall_at_10", "mrr"])
def test_hard_metric_drop_beyond_tolerance_fails(files, tmp_path, metric):
    accepted = _write(tmp_path / "a2.json", {**GOOD, metric: 0.70})
    hard, soft = cr.check_retrieval(files[0], accepted)
    assert len(hard) == 1 and metric in hard[0] and soft == []


def test_soft_metric_drop_only_warns(files, tmp_path):
    accepted = _write(tmp_path / "a2.json", {**GOOD, "ndcg_at_10": 0.50})
    hard, soft = cr.check_retrieval(files[0], accepted)
    assert hard == [] and len(soft) == 1 and "ndcg_at_10" in soft[0]


def test_missing_metric_fails_clearly(files, tmp_path):
    accepted = _write(tmp_path / "a2.json", {"recall_at_10": 0.8, "ndcg_at_10": 0.7})
    hard, _ = cr.check_retrieval(files[0], accepted)
    assert any("mrr" in m and "missing" in m for m in hard)


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[]",
        json.dumps({"other_variant": GOOD}),
        json.dumps({V: "x"}),
        json.dumps({V: {**GOOD, "mrr": "0.8"}}),
    ],
)
def test_malformed_shape_fails_loudly(files, tmp_path, content):
    bad = tmp_path / "bad.json"
    bad.write_text(content)
    hard, _ = cr.check_retrieval(files[0], bad)
    assert hard


def test_missing_file_fails(files, tmp_path):
    hard, _ = cr.check_retrieval(files[0], tmp_path / "nope.json")
    assert any("missing" in m for m in hard)


def test_lowering_baselines_vs_base_branch_fails(tmp_path):
    base = _write(tmp_path / "base.json")
    lowered = {**GOOD, "mrr": 0.60}
    baselines = _write(tmp_path / "baselines.json", lowered)
    accepted = _write(tmp_path / "accepted.json", lowered)
    assert cr.check_retrieval(baselines, accepted) == ([], [])  # self-consistent
    hard, _ = cr.check_retrieval(baselines, accepted, base)
    assert any("lowered" in m and "mrr" in m for m in hard)


def test_accept_writes_gated_shape(tmp_path):
    latest = tmp_path / "latest.json"
    latest.write_text(
        json.dumps({"_run_at": "t", "variants": {"B": {"scores": {**GOOD, "n": 45}}}})
    )
    out = tmp_path / "accepted.json"
    cr.accept_latest(latest, out)
    assert json.loads(out.read_text())[V] == GOOD


def test_committed_files_pass():
    hard, _ = cr.check_retrieval()
    assert hard == []
