"""Ask Gemini for structured preferences, or use the offline keyword substitute in tests."""
import os

from google import genai
from google.genai import types

from api import fake_llm

GENERATIVE_MODEL = "gemini-3.1-flash-lite"
_client = None


def ask(user_prompt: str, system_instruction: str, response_schema) -> str:
    if os.getenv("LLM_PROVIDER", "gemini") == "mock":
        return fake_llm.fake_ask(user_prompt, system_instruction, response_schema)

    # Create the client only when needed so offline tests do not require credentials.
    global _client
    if _client is None:
        _client = genai.Client(vertexai=True, project=os.environ["GCP_PROJECT"],
                               location=os.getenv("GEMINI_LOCATION", "global"),
                               http_options=types.HttpOptions(timeout=60000))
    response = _client.models.generate_content(
        model=os.getenv("GEMINI_MODEL", GENERATIVE_MODEL),
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0,
            response_mime_type="application/json",
            response_schema=response_schema,
        ),
    )
    return response.text or ""
