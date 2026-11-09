# 建立client，让Gemini对prompt进行一次翻译，打20维度的偏好权重分（此时不知道风景是什么）
# 以后换模型（比如 fine-tune后的endpoint）只改这个文件。
# 同时我设置了开关：环境变量 LLM_PROVIDER=gemini（默认，真的 Gemini）或者 = mock（本地替身 fake_llm.py，不要 key）
import os
from google import genai
from google.genai import types
from api import fake_llm

# 配置（和 tutorial 的 rag.py 一样）
# 如果我们后面打算fintune的话，就FINE_TUNE_GENERATIVE_MODEL = "projects/.../endpoints/..." 
GENERATIVE_MODEL = "gemini-3.1-flash-lite"


# Gemini client：先不建，第一次真要问 Gemini 时才建（见 _get_client）
# 这样没有 GCP_PROJECT / key 的时候，只要不走 Gemini，import 这个文件也不会报错
_client = None


# 第一次用到时才建 client，vertexai=True 表示用 service account 认证
def _get_client():
    global _client
    if _client is None:
        _client = genai.Client(vertexai=True, project=os.environ["GCP_PROJECT"], location="global")
    return _client


# 问一次 LLM，传入prompt,instruction,schema
# 原样带回它按 response_schema 格式写的 JSON 文本
def ask(user_prompt: str, system_instruction: str, response_schema) -> str:

    # LLM_PROVIDER=mock 时转给本地替身，不调 Gemini、不需要 key、不花钱
    if os.environ.get("LLM_PROVIDER", "gemini") == "mock": #在yml文件里面定义了
        return fake_llm.fake_ask(user_prompt, system_instruction, response_schema)

    # 默认：问真的 Gemini
    response = _get_client().models.generate_content(
        model=GENERATIVE_MODEL,
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0,  # 尽量不随机：同一句话问两次，答案几乎一样
            response_mime_type="application/json",
            response_schema=response_schema,  # structured output：按这个格式填空
        ),
    )
    return response.text or ""
