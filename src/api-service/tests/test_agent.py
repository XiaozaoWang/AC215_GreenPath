# agent.py（harness）和 fake_llm.py 的单元测试：全部用假的 LLM，不调 Gemini、不需要 key、不花钱。
# 测试思路来自朱同学版本的 test_agent.py（坏输入、重试、问满次数），改成我们的格式：
# 我们的 harness 失败时不抛错，而是全 0 保底（used_fallback=True）。
import json

import pytest
from pydantic import ValidationError

from api import agent, fake_llm, llm
from api.schemas import Preference


# 造一条 Gemini 可能回的 JSON：一个维度、一个权重
def reply(dimension="water_access", weight=1.0, reason="test"):
    return json.dumps({"weights": [{"dimension": dimension, "reason": reason, "weight": weight}], "must_have": []})


# 假 LLM：按顺序回一串预先准备好的答案，并记下每次收到的 prompt
class ScriptedLLM:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []

    def __call__(self, user_prompt, system_instruction, response_schema):
        self.prompts.append(user_prompt)
        return self.answers.pop(0)


# ---------- harness 的循环 ----------
# 第一次就合格：只问 1 次，不用保底
def test_valid_first_try():
    fake = ScriptedLLM(reply("water_access", 1.0))
    result = agent.get_preference("想看水", ask=fake)
    assert len(result.attempts) == 1 and not result.used_fallback
    assert result.preference.as_vector()["water_access"] == 1.0


# 第一次不合格：第二次重问时 prompt 里要带上错误信息，然后合格
def test_retry_carries_error():
    fake = ScriptedLLM(reply("beach", 1.0), reply("water_access", 1.0))
    result = agent.get_preference("想看海", ask=fake)
    assert len(result.attempts) == 2 and not result.used_fallback
    assert result.attempts[0].error is not None and result.attempts[1].error is None
    assert "rejected" in fake.prompts[1] and "rejected" not in fake.prompts[0]


# 问满 MAX_ATTEMPTS 次都不合格：停下来，用全 0 保底（不会无限重试）
def test_fallback_after_max_attempts():
    fake = ScriptedLLM(*["not json"] * agent.MAX_ATTEMPTS)
    result = agent.get_preference("随便", ask=fake)
    assert len(result.attempts) == agent.MAX_ATTEMPTS and result.used_fallback
    assert not any(result.preference.as_vector().values())


# LLM 调用本身出错（断网、配额）：也算一次失败，最后保底，不让整个请求崩掉
def test_llm_exception_counts_as_failed_attempt():
    def broken(*args):
        raise RuntimeError("quota exceeded")
    result = agent.get_preference("随便", ask=broken)
    assert result.used_fallback
    assert all("LLM call failed" in a.error for a in result.attempts)


# ---------- 坏输入会被 pydantic 拦下 ----------
@pytest.mark.parametrize("raw", [
    reply("water_access", 1.2),                # 超出 [-1, 1]
    reply("water_access", -1.2),
    reply("beach", 1.0),                       # 不在维度表里
    reply("water_access", float("nan")),       # NaN（json.dumps 写成 NaN）
    reply("water_access", float("inf")),       # 无穷大（写成 Infinity）
    '{"weights": [{"dimension": "water_access", "weight": 1.0}]}',  # 缺 reason
    '{"weights": [{"dimension": "water_access", "reason": "a", "weight": 1}, '
    '{"dimension": "water_access", "reason": "b", "weight": -1}]}',  # 同一维度出现两次
    "{bad json",                               # 不是合法 JSON
])
def test_invalid_replies_rejected(raw):
    with pytest.raises(ValidationError):
        Preference.model_validate_json(raw)


# ---------- 离线替身 fake_llm ----------
# 替身的输出必须能通过同一套校验，而且正负、软硬判断正确
@pytest.mark.parametrize("text, expected", [
    ("想沿着河骑，最好有树荫，别有太多车", {"water_access": 1.0, "tree_canopy": 0.6, "road_width": -1.0}),
    ("Ride along the river, shade if possible, avoid traffic", {"water_access": 1.0, "tree_canopy": 0.6, "road_width": -1.0}),
    ("我想走自行车道", {}),   # 没有自行车道这个维度；"自行车道"也不能被当成车流
])
def test_fake_llm_rules(text, expected):
    preference = Preference.model_validate_json(fake_llm.fake_ask(f"User description:\n{text}"))
    vector = preference.as_vector()
    assert {k: v for k, v in vector.items() if v} == expected


# "一定要"之类的词 → 放进 must_have
def test_fake_llm_must_have():
    preference = Preference.model_validate_json(fake_llm.fake_ask("User description:\n一定要有水"))
    assert preference.must_have == ["water_access"]


# 开关：LLM_PROVIDER=mock 时 llm.ask() 走替身，不建 Gemini client
def test_llm_switch_to_mock(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    raw = llm.ask("User description:\n想看水", "", Preference)
    assert Preference.model_validate_json(raw).as_vector()["water_access"] == 1.0
    assert llm._client is None
