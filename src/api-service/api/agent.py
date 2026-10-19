# A harness wrapped around Gemini: ask -- check -- if it fails, ask again with the error message; once we get a valid Preference, we're done

from pydantic import ValidationError
from api import llm, prompts
from api.schemas import AgentResult, Attempt, Preference

# Set the max number of times to ask
MAX_ATTEMPTS = 3

# input: the user's words (str), get the preference (JSON) through llm.ask; output: a checked, valid AgentResult (Preference + the record of every Q&A)
# If it's still not valid after MAX_ATTEMPTS tries, fall back to all-0 weights
# ask can be swapped for a fake one, so tests don't call Gemini and cost nothing
def get_preference(text: str, ask=llm.ask) -> AgentResult:
    attempts = []
    error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        user_prompt = prompts.build_user_prompt(text, error)

        # Ask once; a failed call (network, quota, etc.) also counts as one failed try
        try:
            raw = ask(user_prompt, prompts.SYSTEM_INSTRUCTION, Preference)
        except Exception as e:
            attempts.append(Attempt(prompt=user_prompt, raw="", error=f"LLM call failed: {e}"))
            print(f"[agent] attempt {attempt}/{MAX_ATTEMPTS} LLM call failed: {e}")
            continue

        # Trace: log every Q&A
        print(f"[agent] attempt {attempt}/{MAX_ATTEMPTS} raw={raw!r}")

        # Verification: pydantic checks it; if valid, return right away; if not, save the error and bring it to the next round
        try:
            preference = Preference.model_validate_json(raw) #Preference is the class defined in schemas; model_validate_json comes from the third-party pydantic library: it builds a Preference object from the JSON string and checks it against the rules defined in Preference (the same dimension can't appear twice, dimensions <= 20, -1<weight<1, reason can't be missing)
            attempts.append(Attempt(prompt=user_prompt, raw=raw))
            return AgentResult(preference=preference, attempts=attempts, used_fallback=False)
        except ValidationError as e:
            error = str(e)
            attempts.append(Attempt(prompt=user_prompt, raw=raw, error=error))
            print(f"[agent] rejected: {error}")

    # Fallback: out of tries, return all-0 weights
    print("[agent] budget used up, using fallback (all weights 0)")
    return AgentResult(preference=Preference(), attempts=attempts, used_fallback=True)
