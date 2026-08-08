"""M5 tests: Claude Agent SDK ranking.

The model call itself is the only thing needing live credentials. Everything
between our prompt and research.ranked — construction, parsing, grounding — is
covered here without network access.
"""
import json

import pytest

from app import llm_ranker
from app.agent import ScriptedAgent
from app.state import SessionStore


def _state_and_shortlist(tmp_path):
    store = SessionStore(tmp_path / "r.sqlite")
    agent = ScriptedAgent(store)
    for answer in ["", "rent", "goa trip with friends", "SUV", "4000 per day",
                   "2026-09-10 to 2026-09-15"]:
        agent.respond("r1", answer)
    state = store.get("r1")
    return store, state, state["research"]["shortlist"]


# ---- configuration gate ------------------------------------------------------

def test_ranking_off_by_configuration(monkeypatch):
    monkeypatch.setenv("CARMATCH_AGENT_RANKING", "off")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-whatever")
    assert llm_ranker.is_enabled() is False


def test_ranking_needs_credentials_or_explicit_optin(monkeypatch):
    monkeypatch.delenv("CARMATCH_AGENT_RANKING", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert llm_ranker.is_enabled() is False
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert llm_ranker.is_enabled() is llm_ranker._sdk_importable()


# ---- prompt construction -----------------------------------------------------

def test_prompt_carries_the_users_own_words(tmp_path):
    _, state, shortlist = _state_and_shortlist(tmp_path)
    state["intent"]["constraints"] = ["automatic", "seats>=7"]
    prompt = llm_ranker.build_prompt(state, shortlist)

    assert "goa trip with friends" in prompt        # their use case, verbatim
    assert "automatic" in prompt and "seats>=7" in prompt
    assert "2026-09-10 to 2026-09-15" in prompt
    for s in shortlist:
        assert s["listing_id"] in prompt            # every candidate offered
    assert "budget fit" in prompt                   # deterministic scores shown


def test_prompt_excludes_personal_details(tmp_path):
    store, state, shortlist = _state_and_shortlist(tmp_path)
    state["checkout"]["form_data"] = {"name": "Riya Menon", "phone": "9876500000"}
    prompt = llm_ranker.build_prompt(state, shortlist)
    assert "Riya" not in prompt and "9876500000" not in prompt


# ---- grounding checks --------------------------------------------------------

def _valid_payload(shortlist):
    return json.dumps([
        {"listing_id": s["listing_id"], "rank": i + 1,
         "reasoning": f"Reason {i + 1}"}
        for i, s in enumerate(shortlist)
    ])


def test_valid_ranking_is_accepted(tmp_path):
    _, _, shortlist = _state_and_shortlist(tmp_path)
    out = llm_ranker.validate_ranking(_valid_payload(shortlist), shortlist)
    assert out is not None
    assert [r["rank"] for r in out] == list(range(1, len(shortlist) + 1))
    assert {r["listing_id"] for r in out} == {s["listing_id"] for s in shortlist}


def test_reordering_is_respected(tmp_path):
    _, _, shortlist = _state_and_shortlist(tmp_path)
    reversed_ids = [s["listing_id"] for s in shortlist][::-1]
    payload = json.dumps([{"listing_id": lid, "rank": i + 1, "reasoning": "r"}
                          for i, lid in enumerate(reversed_ids)])
    out = llm_ranker.validate_ranking(payload, shortlist)
    assert [r["listing_id"] for r in out] == reversed_ids


def test_hallucinated_listing_is_rejected(tmp_path):
    _, _, shortlist = _state_and_shortlist(tmp_path)
    payload = json.dumps([{"listing_id": "l-999999", "rank": 1,
                           "reasoning": "a car that does not exist"}])
    assert llm_ranker.validate_ranking(payload, shortlist) is None


def test_dropped_candidates_are_rejected(tmp_path):
    _, _, shortlist = _state_and_shortlist(tmp_path)
    payload = json.dumps([{"listing_id": shortlist[0]["listing_id"],
                           "rank": 1, "reasoning": "only mentioned one"}])
    assert llm_ranker.validate_ranking(payload, shortlist) is None


def test_duplicate_listing_is_rejected(tmp_path):
    _, _, shortlist = _state_and_shortlist(tmp_path)
    lid = shortlist[0]["listing_id"]
    payload = json.dumps([{"listing_id": lid, "rank": 1, "reasoning": "a"},
                          {"listing_id": lid, "rank": 2, "reasoning": "b"}])
    assert llm_ranker.validate_ranking(payload, shortlist) is None


def test_empty_reasoning_is_rejected(tmp_path):
    _, _, shortlist = _state_and_shortlist(tmp_path)
    payload = json.dumps([{"listing_id": s["listing_id"], "rank": i + 1,
                           "reasoning": ""} for i, s in enumerate(shortlist)])
    assert llm_ranker.validate_ranking(payload, shortlist) is None


def test_non_json_is_rejected(tmp_path):
    _, _, shortlist = _state_and_shortlist(tmp_path)
    assert llm_ranker.validate_ranking("Sure! Here are my picks:", shortlist) is None


def test_markdown_fences_are_tolerated(tmp_path):
    _, _, shortlist = _state_and_shortlist(tmp_path)
    fenced = "```json\n" + _valid_payload(shortlist) + "\n```"
    assert llm_ranker.validate_ranking(fenced, shortlist) is not None


def test_reasoning_is_length_capped(tmp_path):
    _, _, shortlist = _state_and_shortlist(tmp_path)
    payload = json.dumps([{"listing_id": s["listing_id"], "rank": i + 1,
                           "reasoning": "x" * 5000}
                          for i, s in enumerate(shortlist)])
    out = llm_ranker.validate_ranking(payload, shortlist)
    assert all(len(r["reasoning"]) <= llm_ranker.MAX_REASONING_CHARS for r in out)


# ---- reply parsing (real SDK message objects, no subprocess) -------------------

@pytest.mark.skipif(not llm_ranker._sdk_importable(),
                    reason="claude-agent-sdk not installed")
def test_extract_text_concatenates_assistant_blocks():
    from claude_agent_sdk import AssistantMessage, TextBlock

    msgs = [
        AssistantMessage(content=[TextBlock(text='[{"listing_id":'),
                                  TextBlock(text=' "l-1"}]')],
                         model="claude-sonnet-4-5"),
    ]
    assert llm_ranker.extract_text(msgs) == '[{"listing_id": "l-1"}]'


@pytest.mark.skipif(not llm_ranker._sdk_importable(),
                    reason="claude-agent-sdk not installed")
def test_extract_text_ignores_non_assistant_messages():
    from claude_agent_sdk import AssistantMessage, SystemMessage, TextBlock

    msgs = [
        SystemMessage(subtype="init", data={}),
        AssistantMessage(content=[TextBlock(text="kept")],
                         model="claude-sonnet-4-5"),
    ]
    assert llm_ranker.extract_text(msgs) == "kept"


@pytest.mark.skipif(not llm_ranker._sdk_importable(),
                    reason="claude-agent-sdk not installed")
def test_extract_then_validate_is_the_whole_reply_path(tmp_path):
    """The parse->validate pipeline, end to end, without the subprocess.

    Everything between the model's bytes and research.ranked is covered here;
    only the transport itself needs live credentials.
    """
    from claude_agent_sdk import AssistantMessage, TextBlock

    _, _, shortlist = _state_and_shortlist(tmp_path)
    ids = [s["listing_id"] for s in shortlist][::-1]
    payload = json.dumps([{"listing_id": lid, "rank": i + 1,
                           "reasoning": "seats seven for the Goa group"}
                          for i, lid in enumerate(ids)])
    msgs = [AssistantMessage(content=[TextBlock(text=payload)],
                             model="claude-sonnet-4-5")]

    out = llm_ranker.validate_ranking(llm_ranker.extract_text(msgs), shortlist)
    assert [r["listing_id"] for r in out] == ids


# ---- the agent keeps working when ranking is unavailable ----------------------

def test_research_falls_back_cleanly(tmp_path, monkeypatch):
    monkeypatch.setenv("CARMATCH_AGENT_RANKING", "off")
    store = SessionStore(tmp_path / "fb.sqlite")
    agent = ScriptedAgent(store)
    reply = None
    for answer in ["", "rent", "goa trip", "SUV", "4000 per day",
                   "2026-09-10 to 2026-09-15"]:
        reply = agent.respond("fb1", answer)

    state = store.get("fb1")
    assert state["research"]["shortlist"], "deterministic half still runs"
    assert state["research"]["ranked"] == [], "no model ranking recorded"
    comps = reply.a2ui_messages[-1]["updateComponents"]["components"]
    whys = [c["text"] for c in comps if c["id"].endswith("-why")]
    assert whys and all(w.startswith("Why:") for w in whys)
