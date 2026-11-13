# Unit tests for agent.py (the harness) and fake_llm.py: all use a fake LLM, so no Gemini calls, no key, no cost.
# The test ideas come from teammate Zhu's version of test_agent.py (bad input, retries, running out of attempts), changed to our format:
# our harness does not raise on failure, it falls back to all 0 instead (used_fallback=True).
import json

import pytest
from pydantic import ValidationError

from api import agent, fake_llm, llm
from api.schemas import Preference


# Build a JSON reply Gemini might send: one dimension, one weight
def reply(dimension="water_access", weight=1.0, reason="test"):
    return json.dumps({"weights": [{"dimension": dimension, "reason": reason, "weight": weight}], "must_have": []})


# Fake LLM: returns prepared answers in order, and records every prompt it gets
class ScriptedLLM:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []

    def __call__(self, user_prompt, system_instruction, response_schema):
        self.prompts.append(user_prompt)
        return self.answers.pop(0)


# ---------- harness loop ----------
# Valid on the first try: only 1 ask, no fallback
def test_valid_first_try():
    fake = ScriptedLLM(reply("water_access", 1.0))
    result = agent.get_preference("想看水", ask=fake)
    assert len(result.attempts) == 1 and not result.used_fallback
    assert result.preference.as_vector()["water_access"] == 1.0


# First try invalid: the second ask must carry the error in the prompt, then it passes
def test_retry_carries_error():
    fake = ScriptedLLM(reply("beach", 1.0), reply("water_access", 1.0))
    result = agent.get_preference("想看海", ask=fake)
    assert len(result.attempts) == 2 and not result.used_fallback
    assert result.attempts[0].error is not None and result.attempts[1].error is None
    assert "rejected" in fake.prompts[1] and "rejected" not in fake.prompts[0]


# Invalid for all MAX_ATTEMPTS asks: stop and fall back to all 0 (no endless retries)
def test_fallback_after_max_attempts():
    fake = ScriptedLLM(*["not json"] * agent.MAX_ATTEMPTS)
    result = agent.get_preference("随便", ask=fake)
    assert len(result.attempts) == agent.MAX_ATTEMPTS and result.used_fallback
    assert not any(result.preference.as_vector().values())


# The LLM call itself fails (no network, quota): counts as a failed attempt, ends in the fallback, so the whole request doesn't crash
def test_llm_exception_counts_as_failed_attempt():
    def broken(*args):
        raise RuntimeError("quota exceeded")
    result = agent.get_preference("随便", ask=broken)
    assert result.used_fallback
    assert all("LLM call failed" in a.error for a in result.attempts)


# ---------- bad input gets stopped by pydantic ----------
@pytest.mark.parametrize("raw", [
    reply("water_access", 1.2),                # outside [-1, 1]
    reply("water_access", -1.2),
    reply("beach", 1.0),                       # not in the dimension list
    reply("water_access", float("nan")),       # NaN (json.dumps writes it as NaN)
    reply("water_access", float("inf")),       # infinity (written as Infinity)
    '{"weights": [{"dimension": "water_access", "weight": 1.0}]}',  # missing reason
    '{"weights": [{"dimension": "water_access", "reason": "a", "weight": 1}, '
    '{"dimension": "water_access", "reason": "b", "weight": -1}]}',  # same dimension twice
    "{bad json",                               # not valid JSON
])
def test_invalid_replies_rejected(raw):
    with pytest.raises(ValidationError):
        Preference.model_validate_json(raw)


# ---------- offline stand-in fake_llm ----------
# The stand-in's output must pass the same validation, and get positive/negative and soft/hard right
@pytest.mark.parametrize("text, expected", [
    ("想沿着河骑，最好有树荫，别有太多车", {"water_access": 1.0, "tree_canopy": 0.6, "road_width": -1.0}),
    ("Ride along the river, shade if possible, avoid traffic", {"water_access": 1.0, "tree_canopy": 0.6, "road_width": -1.0}),
    ("我想走自行车道", {}),   # there is no bike lane dimension, and "bike lane" must not be read as traffic either
])
def test_fake_llm_rules(text, expected):
    preference = Preference.model_validate_json(fake_llm.fake_ask(f"User description:\n{text}"))
    vector = preference.as_vector()
    assert {k: v for k, v in vector.items() if v} == expected


# Words like "must" put the dimension into must_have
def test_fake_llm_must_have():
    preference = Preference.model_validate_json(fake_llm.fake_ask("User description:\n一定要有水"))
    assert preference.must_have == ["water_access"]


# Switch: with LLM_PROVIDER=mock, llm.ask() uses the stand-in and doesn't create a Gemini client
def test_llm_switch_to_mock(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    raw = llm.ask("User description:\n想看水", "", Preference)
    assert Preference.model_validate_json(raw).as_vector()["water_access"] == 1.0
    assert llm._client is None
