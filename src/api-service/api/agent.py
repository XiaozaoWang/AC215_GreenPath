# 包在 Gemini 外面的 harness：问 -- 检验 -- 不合格就带着错误信息重问，拿到一个合格的 Preference 就下班

from pydantic import ValidationError
from api import llm, prompts
from api.schemas import AgentResult, Attempt, Preference

# 设置最多问几次
MAX_ATTEMPTS = 3

# input 用户的话str 经llm.ask拿到偏好(json) 输出经检查合格的AgentResult格式（Preference格式+每次问答记录）
# 问满 MAX_ATTEMPTS 次还不合格，就用全 0 保底
# ask 可以换成假的，测试时不调 Gemini、不花钱
def get_preference(text: str, ask=llm.ask) -> AgentResult:
    attempts = []
    error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        user_prompt = prompts.build_user_prompt(text, error)

        # 问一次，网络、配额之类的调用失败也算一次失败
        try:
            raw = ask(user_prompt, prompts.SYSTEM_INSTRUCTION, Preference)
        except Exception as e:
            attempts.append(Attempt(prompt=user_prompt, raw="", error=f"LLM call failed: {e}"))
            print(f"[agent] attempt {attempt}/{MAX_ATTEMPTS} LLM call failed: {e}")
            continue

        # Trace，每次问答都打日志
        print(f"[agent] attempt {attempt}/{MAX_ATTEMPTS} raw={raw!r}")

        # Verification：pydantic 验，合格直接返回，不合格记下错误、下一轮带回去
        try:
            preference = Preference.model_validate_json(raw) #Preference为schemas定义的类，model_validate_json为pydantic第三方库，从jsonz字符串生成Preference对象，并哪找Preference里定义的规则校验（同一个维度不能出现两次，维度<=20，-1<weight<1，reason不能缺）
            attempts.append(Attempt(prompt=user_prompt, raw=raw))
            return AgentResult(preference=preference, attempts=attempts, used_fallback=False)
        except ValidationError as e:
            error = str(e)
            attempts.append(Attempt(prompt=user_prompt, raw=raw, error=error))
            print(f"[agent] rejected: {error}")

    # 保底：次数用完，返回全 0 权重
    print("[agent] budget used up, using fallback (all weights 0)")
    return AgentResult(preference=Preference(), attempts=attempts, used_fallback=True)
