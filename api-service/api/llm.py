# Build the client and let Gemini translate the prompt once into preference weights for the active model dimensions (at this point it doesn't know what the scenery looks like)
# If we switch models later (e.g. a fine-tuned endpoint), only change this file.
# I also set up a switch: env variable LLM_PROVIDER=gemini (default, the real Gemini) or = mock (local stand-in fake_llm.py, no key needed)
import os
from google import genai
from google.genai import types
from api import fake_llm

# Config (same as rag.py in the tutorial)
# If we plan to fine-tune later, then FINE_TUNE_GENERATIVE_MODEL = "projects/.../endpoints/..."
GENERATIVE_MODEL = "gemini-3.1-flash-lite"


# Gemini client: don't build it yet; only build it the first time we really ask Gemini (see _get_client)
# This way, with no GCP_PROJECT / key, importing this file won't fail as long as we don't use Gemini
_client = None


# Build the client only the first time it's needed; vertexai=True means log in with the service account
def _get_client():
    global _client
    if _client is None:
        _client = genai.Client(vertexai=True, project=os.environ["GCP_PROJECT"], location="global")
    return _client


# Ask the LLM once, passing in prompt, instruction, schema
# Return, as is, the JSON text it wrote in the response_schema format
def ask(user_prompt: str, system_instruction: str, response_schema) -> str:

    # When LLM_PROVIDER=mock, hand it to the local stand-in: no Gemini call, no key, no cost
    if os.environ.get("LLM_PROVIDER", "gemini") == "mock": #defined in the yml file
        return fake_llm.fake_ask(user_prompt, system_instruction, response_schema)

    # Default: ask the real Gemini
    response = _get_client().models.generate_content(
        model=GENERATIVE_MODEL,
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0,  # as little randomness as possible: ask the same sentence twice, get almost the same answer
            response_mime_type="application/json",
            response_schema=response_schema,  # structured output: fill in the blanks in this format
        ),
    )
    return response.text or ""
