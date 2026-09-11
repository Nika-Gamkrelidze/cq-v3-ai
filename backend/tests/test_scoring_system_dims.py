"""System rubric dimensions: scored by code from the analyser that measures them.

Two criteria that tenants kept writing by hand as prose dimensions are now measured instead of
re-judged from the transcript:

  kb_factcheck    from the KB fact-check's `accuracy_score` — a model asked "was this correct?"
                  from the transcript alone has no documents to check against.
  agent_courtesy  from the tone analyser's per-speaker `politeness` for role == "agent" — so a
                  furious customer does not lower the agent's score, which is exactly what an
                  overall-call-sentiment number would have done.

The rules these tests exist to hold:
  * the model never sees them (no tokens spent re-judging a measurement),
  * an absent signal is NOT a zero — it drops out of the weighting and the rest renormalise,
  * a reviewer cannot hand-edit a measured number into an opinion,
  * a tenant owns the WEIGHT and nothing else about them.
"""
import pytest

from app.services import scoring, scoring_store

FC = scoring.SYSTEM_DIMENSIONS["factcheck"]["key"]
CO = scoring.SYSTEM_DIMENSIONS["sentiment"]["key"]


def _dims(*, fc_weight=30.0, co_weight=20.0, model_weight=50.0):
    return [
        {"key": "greeting", "name": "Greeting", "weight": model_weight},
        scoring.system_dimension("factcheck", fc_weight),
        scoring.system_dimension("sentiment", co_weight),
    ]


def _by_key(model_score=80, **kw):
    return {"greeting": {"score": model_score, "rationale": "r", "evidence": []},
            **scoring.system_scores(**kw)}


# --------------------------------------------------------------------------- signals
def test_factcheck_accuracy_is_read_but_never_invented():
    assert scoring.factcheck_accuracy({"accuracy_score": 83}) == 83
    assert scoring.factcheck_accuracy({"accuracy_score": 0}) == 0       # a real, bad score
    assert scoring.factcheck_accuracy({"accuracy_score": None}) is None  # every claim NOT_IN_KB
    assert scoring.factcheck_accuracy({}) is None                        # ran, nothing checkable
    assert scoring.factcheck_accuracy(None) is None                      # never ran / no KB
    assert scoring.factcheck_accuracy({"accuracy_score": "nope"}) is None
    assert scoring.factcheck_accuracy({"accuracy_score": 140}) == 100     # clamped


def test_courtesy_reads_the_agent_not_the_customer():
    """THE POINT OF THE DIMENSION. A call where the customer is furious and the agent is
    faultless must score the AGENT's wording."""
    tone = {"speakers": [
        {"speaker": "speaker_1", "role": "customer", "politeness": 8},
        {"speaker": "speaker_0", "role": "agent", "politeness": 94},
    ]}
    assert scoring.agent_politeness(tone) == 94


def test_courtesy_is_none_when_there_is_no_agent_or_no_pass():
    assert scoring.agent_politeness(None) is None
    assert scoring.agent_politeness({"speakers": []}) is None
    assert scoring.agent_politeness({"speakers": [{"role": "customer", "politeness": 90}]}) is None
    assert scoring.agent_politeness({"speakers": [{"role": "agent", "politeness": None}]}) is None


# --------------------------------------------------------------------------- normalisation
def test_a_system_dimension_keeps_its_source_and_cannot_be_renamed():
    """A tenant owns the weight. The name and guidance are the product's, because the whole
    value of the number is knowing where it came from."""
    dims = scoring.normalize_dimensions([
        {"key": FC, "name": "Something else entirely", "weight": 40,
         "guidance": "score it however you like", "source": "factcheck"}])
    assert len(dims) == 1
    assert dims[0]["name"] == scoring.SYSTEM_DIMENSIONS["factcheck"]["name"]
    assert dims[0]["guidance"] == scoring.SYSTEM_DIMENSIONS["factcheck"]["guidance"]
    assert dims[0]["weight"] == 40.0 and dims[0]["source"] == "factcheck"


def test_an_unknown_source_is_stripped_so_it_stays_an_ordinary_dimension():
    dims = scoring.normalize_dimensions([{"name": "Mine", "weight": 10, "source": "wishful"}])
    assert dims[0].get("source") is None and dims[0]["name"] == "Mine"


# --------------------------------------------------------------------------- weighting
def test_a_measured_dimension_is_weighted_like_any_other():
    res = scoring.build_result(
        _dims(), _by_key(kb_check={"accuracy_score": 60, "counts": {"supported": 3}},
                         semantic={"speakers": [{"role": "agent", "politeness": 100}]}),
        1, "speaker_0")
    got = {d["key"]: d for d in res["dimensions"]}
    assert got[FC]["score"] == 60 and got[CO]["score"] == 100
    # 0.5*80 + 0.3*60 + 0.2*100 = 40 + 18 + 20
    assert res["weighted_total"] == 78.0
    assert res["scored_weight"] == 100.0 and res["unscored"] == []


def test_an_unmeasured_dimension_drops_out_and_the_rest_renormalise():
    """NOT the same as scoring zero. No KB, no checkable claim, no tone pass — none of those
    is the agent's doing, and leaving the weight in the denominator would deduct for it."""
    res = scoring.build_result(_dims(), _by_key(kb_check=None, semantic=None), 1, "speaker_0")
    got = {d["key"]: d for d in res["dimensions"]}
    assert got[FC]["score"] is None and got[CO]["score"] is None
    # Only the 50-weight model dimension scored, so the total is its own score.
    assert res["weighted_total"] == 80.0
    assert res["scored_weight"] == 50.0
    assert sorted(res["unscored"]) == sorted([FC, CO])
    # The displayed weight is still the share of the WHOLE rubric the tenant configured.
    assert got[FC]["weight"] == 30.0
    # ...and it says why in words, rather than leaving a bare dash.
    assert "no knowledge base" in got[FC]["rationale"] or "has not run" in got[FC]["rationale"]


def test_zero_is_a_real_score_and_is_not_treated_as_absent():
    res = scoring.build_result(
        _dims(), _by_key(kb_check={"accuracy_score": 0, "counts": {"contradicted": 2}},
                         semantic={"speakers": [{"role": "agent", "politeness": 0}]}),
        1, "speaker_0")
    assert res["unscored"] == [] and res["scored_weight"] == 100.0
    assert res["weighted_total"] == 40.0          # only the model dimension contributes


def test_the_rationale_counts_only_checkable_claims():
    res = scoring.build_result(
        _dims(), _by_key(kb_check={"accuracy_score": 75, "counts": {
            "supported": 2, "partially_supported": 1, "contradicted": 1, "not_in_kb": 9}}),
        1, "s")
    got = {d["key"]: d for d in res["dimensions"]}
    assert "4 checkable claim" in got[FC]["rationale"]       # 2 + 1 + 1, not 13


# --------------------------------------------------------------------------- manual edits
def test_a_reviewer_cannot_overwrite_a_measured_dimension():
    """Disagreeing with the knowledge base is a reason to fix the knowledge base, not to type
    over the number and leave the scorecard asserting a fact-check that never happened."""
    res = scoring.build_result(
        _dims(), _by_key(kb_check={"accuracy_score": 40, "counts": {"supported": 2}}),
        1, "s")
    edited = scoring.apply_manual_scores(res, {FC: 100, "greeting": 90}, edited_by="qa@x")
    got = {d["key"]: d for d in edited["dimensions"]}
    assert got[FC]["score"] == 40 and "edited" not in got[FC]
    assert got["greeting"]["score"] == 90 and got["greeting"]["edited"] is True


def test_clearing_a_score_by_hand_renormalises_rather_than_deducting():
    res = scoring.build_result(
        _dims(), _by_key(model_score=80,
                         kb_check={"accuracy_score": 60, "counts": {"supported": 1}},
                         semantic={"speakers": [{"role": "agent", "politeness": 100}]}),
        1, "s")
    edited = scoring.apply_manual_scores(res, {"greeting": None}, edited_by="qa@x")
    # 0.3*60 + 0.2*100 over the 0.5 that remains scored = (18 + 20) / 0.5
    assert edited["weighted_total"] == 76.0
    assert edited["unscored"] == ["greeting"] and edited["scored_weight"] == 50.0


# --------------------------------------------------------------------------- the default
def test_the_default_rubric_measures_instead_of_re_judging():
    dims = scoring.normalize_dimensions(scoring_store.BUILTIN_DEFAULT["dimensions"])
    by_source = {d.get("source"): d for d in dims}
    assert "factcheck" in by_source and "sentiment" in by_source
    names = {d["name"].lower() for d in dims}
    # The two prose criteria these replaced are gone: they duplicated the analysers.
    assert not any("correctness of information" in n for n in names)
    assert sum(d["weight"] for d in dims) == 100.0


@pytest.mark.asyncio
async def test_the_model_never_sees_a_system_dimension(monkeypatch):
    """It cannot check a knowledge base it has no access to, and a second quiet opinion about
    tone would only disagree with the measured one on the same scorecard."""
    sent = {}

    async def _call_tool(**kw):
        sent.update(kw)
        return {"operator_speaker": "speaker_0",
                "scores": [{"key": "greeting", "score": 70, "rationale": "r", "evidence": []}]}
    monkeypatch.setattr(scoring.llm, "call_tool", _call_tool)

    res = await scoring.run_scoring(
        "agent: hello", {"dimensions": _dims(), "version": 3}, "k", "m",
        kb_check={"accuracy_score": 90, "counts": {"supported": 5}},
        semantic={"speakers": [{"role": "agent", "politeness": 50}]})
    assert FC not in sent["system"] and CO not in sent["system"]
    assert "Greeting" in sent["system"]
    got = {d["key"]: d["score"] for d in res["dimensions"]}
    assert got == {"greeting": 70, FC: 90, CO: 50}


@pytest.mark.asyncio
async def test_a_rubric_of_only_system_dimensions_costs_zero_tokens(monkeypatch):
    async def _boom(**kw):
        raise AssertionError("the model must not be called")
    monkeypatch.setattr(scoring.llm, "call_tool", _boom)

    res = await scoring.run_scoring(
        "agent: hello",
        {"dimensions": [scoring.system_dimension("factcheck", 100.0)], "version": 1}, "k", "m",
        kb_check={"accuracy_score": 64, "counts": {"supported": 4, "contradicted": 2}})
    assert res["weighted_total"] == 64.0
    assert res["dimensions"][0]["score"] == 64


# --------------------------------------------------------------------------- auto-insert
def test_an_existing_tenant_rubric_gains_the_measured_dimensions():
    """THE HALF THAT WAS MISSING. Putting them in BUILTIN_DEFAULT reached almost nobody: an
    owner who has ever saved a rubric has their own row, returned verbatim. The workspaces most
    in need of the measured versions are exactly the ones that typed the prose ones by hand."""
    typed_by_hand = [
        {"key": "greeting", "name": "Greeting & Identification", "weight": 20.0},
        {"key": "courtesy", "name": "Courtesy & Empathy", "weight": 15.0},
        {"key": "resolution", "name": "Resolution", "weight": 65.0},
    ]
    dims = scoring_store.with_system_dimensions(typed_by_hand)
    by_source = {d.get("source"): d for d in dims if isinstance(d, dict) and d.get("source")}
    assert set(by_source) == {"factcheck", "sentiment"}
    # Weight 0: no existing score moves until a person decides it should.
    assert [d["weight"] for d in by_source.values()] == [0.0, 0.0]
    # ...and the owner's own numbers are untouched, so the rubric still totals 100.
    assert sum(d["weight"] for d in dims) == 100.0
    assert [d["name"] for d in dims[:3]] == [d["name"] for d in typed_by_hand]


def test_auto_insert_is_idempotent_and_keeps_a_chosen_weight():
    once = scoring_store.with_system_dimensions([{"name": "Greeting", "weight": 100.0}])
    weighted = [{**d, "weight": 25.0} if d.get("source") == "factcheck" else d for d in once]
    twice = scoring_store.with_system_dimensions(weighted)
    assert len(twice) == len(once)
    fc = next(d for d in twice if d.get("source") == "factcheck")
    assert fc["weight"] == 25.0      # a weight the tenant chose is never reset


def test_a_rubric_saved_without_them_gets_them_back():
    """An older UI, an API caller or a hand-built import cannot drop a measured criterion off a
    workspace's scorecard by omitting it."""
    dims, weights = scoring_store._validated(
        [{"name": "Only this", "weight": 100.0}])
    assert {d.get("source") for d in dims if d.get("source")} == {"factcheck", "sentiment"}
    assert sum(d["weight"] for d in dims) == 100.0
    assert set(weights) == {d["key"] for d in dims}


def test_saving_a_rubric_twice_does_not_grow_it():
    """THE SAVE BUG. `routers/scoring.py::Dimension` had no `source` field, so `model_dump()`
    dropped the marker on every PUT: the store saw two ordinary dimensions, appended a fresh
    pair of system ones beside them, and the rubric gained two rows per press of Save while
    the measured rows quietly became prose ones."""
    saved = scoring_store.with_system_dimensions(
        [{"key": "greeting", "name": "Greeting", "weight": 100.0}])
    # What the editor posts back after a round trip through the API's pydantic model, in the
    # broken world: same keys, marker gone.
    posted = [{k: v for k, v in d.items() if k != "source"} for d in saved]
    again = scoring_store.with_system_dimensions(posted)
    assert len(again) == len(saved) == 3
    assert {d.get("source") for d in again} == {None, "factcheck", "sentiment"}


def test_a_rubric_already_damaged_by_that_bug_heals():
    """Anyone who pressed Save while it was broken has duplicate, markerless rows stored. The
    next read collapses them, keeps the weight its owner chose, and restores the marker —
    no migration, no lost configuration."""
    damaged = [
        {"key": "greeting", "name": "Greeting", "weight": 70.0},
        {"key": FC, "name": "Knowledge Base fact check", "weight": 30.0},      # marker lost
        {"key": CO, "name": "Courtesy & empathy", "weight": 0.0},              # marker lost
        {"key": FC, "name": "Knowledge Base fact check", "weight": 0.0, "source": "factcheck"},
        {"key": CO, "name": "Courtesy & empathy", "weight": 0.0, "source": "sentiment"},
    ]
    healed = scoring_store.with_system_dimensions(damaged)
    assert len(healed) == 3
    by_key = {d["key"]: d for d in healed}
    assert by_key[FC]["source"] == "factcheck"
    assert by_key[FC]["weight"] == 30.0      # the weight the owner chose, not the duplicate's 0
    assert by_key[CO]["source"] == "sentiment"
    assert sum(d["weight"] for d in healed) == 100.0
