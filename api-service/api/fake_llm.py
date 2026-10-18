"""Offline parser for contract tests/demos. Keywords live in the model schema."""
import json
import re
from api.dimensions import PROVIDER, SPEC


def _has(text, word):
    return re.search(rf'\b{re.escape(word.lower())}\b', text) is not None if word.isascii() else word in text


def fake_ask(user_prompt: str, system_instruction: str = '', response_schema=None) -> str:
    if PROVIDER == 'legacy':
        from api.fake_llm_legacy import fake_ask as legacy
        return legacy(user_prompt, system_instruction, response_schema)
    text = user_prompt.split('User description:\n', 1)[-1].split('\n\nYour previous answer was rejected', 1)[0].lower()
    weights, required = {}, []
    for clause in re.split(r'[,.;!?]|\bbut\b', text):
        negative = any(_has(clause, w) for w in ['avoid', 'no', 'not', 'without', 'less'])
        soft = any(_has(clause, w) for w in ['if possible', 'ideally', 'prefer'])
        must = any(_has(clause, w) for w in ['must', 'required', 'have to'])
        for name, detail in SPEC['dimensions'].items():
            hit = next((w for w in detail.get('keywords', []) if _has(clause, w)), None)
            if hit is None or name in weights:
                continue
            value = -1.0 if negative else 0.6 if soft else 1.0
            weights[name] = {'dimension': name, 'weight': value, 'reason': f'mock keyword: {hit}'}
            if must and value > 0:
                required.append(name)
    unsupported = [name for name, words in SPEC.get('unsupported_concepts', {}).items() if any(_has(text, w) for w in words)]
    return json.dumps({'weights': list(weights.values()), 'must_have': required, 'unsupported': unsupported}, ensure_ascii=False)
