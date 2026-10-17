# Build the instruction
from api.dimensions import DIMENSIONS

# Join the dimension table into a "- name: description" list and put it into the instruction
_dimension_lines = "\n".join(f"- {name}: {desc}" for name, desc in DIMENSIONS.items())

# System instruction: tell Gemini what the dimensions are and how to give weights
SYSTEM_INSTRUCTION = f"""
You turn a cyclist's description of the scenery they want into preference weights.

Scenery dimensions (use these exact names, nothing else):
{_dimension_lines}

Rules:
1. Direct wishes: weight 1.0 for what the user clearly wants, -1.0 for what they clearly want to avoid.
   Softer wording ("if possible", "a bit", "最好", "有点") gets a smaller weight, e.g. 0.6.
2. Associations ("radiation"): also give smaller weights to dimensions that usually come together with
   what the user described, or that someone with this taste would likely enjoy or dislike.
   The closer the association, the bigger the weight; keep associated weights between 0.2 and 0.6.
   Example: "streets with a cultural atmosphere" ->
     cultural_street 1.0, historic_buildings 0.5, public_art 0.4, landmark 0.3
   Example: "a quiet, relaxing ride" ->
     traffic -1.0, crowds -0.6, quiet_residential 0.5, industrial -0.3
3. If a direct wish and an association disagree, the direct wish wins.
4. Leave out dimensions that have nothing to do with the description (they count as 0).
   Do not spread weight on everything: usually 2 to 8 dimensions.
5. List each dimension at most once. Use one decimal place for weights.
6. reason: a few words on why (direct wish or which association), in the user's language.
7. must_have: only dimensions the user says are required ("must", "only if", "has to", "一定要"). Usually empty.
8. The user may write in any language. Always answer with the JSON format you are given.
"""


# What the user said this time; if the last answer was rejected, add the error message at the end
def build_user_prompt(text: str, error: str | None = None) -> str:
    prompt = f"User description:\n{text}"
    if error:
        prompt += (
            "\n\nYour previous answer was rejected by the validator:\n"
            f"{error}\n"
            "Fix the problem and answer again."
        )
    return prompt
