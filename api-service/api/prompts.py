"""Build preference parsing instructions from the active model contract."""
from api.dimensions import DIMENSIONS

_dimension_lines = '\n'.join(f'- {name}: {description}' for name, description in DIMENSIONS.items())
SYSTEM_INSTRUCTION = f'''Translate the cyclist's description into preferences for this model.
Supported dimensions (use ONLY these exact names):
{_dimension_lines}
Rules:
- Weight is in [-1,1]. Positive means the user wants a HIGHER raw value of that dimension;
  negative means the user wants a LOWER value. Read each dimension's meaning carefully.
- Direct preferences may use +/-1; softer preferences may use smaller magnitudes.
- Do not invent missing capabilities or equate different concepts. Naturalness is not water.
- Put requested concepts that this model cannot score in the unsupported list.
- If a requirement is unsupported, keep it in unsupported, never map it to another dimension.
- Omit unrelated dimensions (their weight is zero); never repeat a dimension.
- must_have contains supported dimensions explicitly required to have a high value. Do not
  put negatively weighted requirements in must_have (the current gate only supports minima).
- Give a short reason in English. Always return the supplied JSON schema.
'''


def build_user_prompt(text: str, error: str | None = None) -> str:
    result = f'User description:\n{text}'
    if error:
        result += f'\n\nYour previous answer was rejected by the validator:\n{error}\nFix it and answer again.'
    return result
