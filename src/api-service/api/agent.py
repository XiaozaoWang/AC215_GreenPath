# Harness wrapped around Gemini: ask, check, if it fails ask again with the error message, done once we get a valid Preference

from pydantic import ValidationError
from api import llm, prompts
from api.schemas import AgentResult, Attempt, Preference

# Max number of times to ask
MAX_ATTEMPTS = 3

# input is the user's text str, llm.ask gets the preference (json), output is a checked AgentResult (Preference + a record of every ask)
# If it is still invalid after MAX_ATTEMPTS asks, fall back to all 0
# ask can be swapped for a fake one, so tests don't call Gemini and cost nothing
def get_preference(text: str, ask=llm.ask) -> AgentResult:
    attempts = []
    error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        user_prompt = prompts.build_user_prompt(text, error)

        # Ask once. A failed call (network, quota and so on) also counts as a failed attempt
        try:
            raw = ask(user_prompt, prompts.SYSTEM_INSTRUCTION, Preference)
        except Exception as e:
            attempts.append(Attempt(prompt=user_prompt, raw="", error=f"LLM call failed: {e}"))
            print(f"[agent] attempt {attempt}/{MAX_ATTEMPTS} LLM call failed: {e}")
            continue

        # Trace, log every ask and answer
        print(f"[agent] attempt {attempt}/{MAX_ATTEMPTS} raw={raw!r}")

        # Verification: pydantic checks it, if valid return right away, if not save the error and send it back next round
        try:
            preference = Preference.model_validate_json(raw) #Preference is the class defined in schemas, model_validate_json comes from the pydantic library, it builds a Preference object from the json string and checks it with the rules defined in Preference (no dimension twice, dimensions <=20, -1<weight<1, reason is required)
            attempts.append(Attempt(prompt=user_prompt, raw=raw))
            return AgentResult(preference=preference, attempts=attempts, used_fallback=False)
        except ValidationError as e:
            error = str(e)
            attempts.append(Attempt(prompt=user_prompt, raw=raw, error=error))
            print(f"[agent] rejected: {error}")

    # Fallback: out of attempts, return all 0 weights
    print("[agent] budget used up, using fallback (all weights 0)")
    return AgentResult(preference=Preference(), attempts=attempts, used_fallback=True)
