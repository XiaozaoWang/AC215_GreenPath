"""English-only offline keyword parser for the explicit legacy demo."""
import json
import re
KEYWORDS = {'water': ['river', 'water', 'waterfront', 'lake', 'pond', 'coast'], 'tree_canopy': ['tree', 'trees', 'shade', 'shady', 'canopy'], 'park': ['park', 'parks', 'lawn', 'green'], 'open_field': ['field', 'meadow', 'open'], 'farmland': ['farm', 'farmland', 'orchard', 'crops'], 'forest': ['forest', 'woods'], 'hills': ['hill', 'hills', 'slope'], 'sky_view': ['sky'], 'gardens': ['garden', 'gardens', 'flower', 'flowers'], 'historic_buildings': ['historic', 'history', 'old buildings'], 'cultural_street': ['cultural', 'culture', 'shops', 'cafe', 'cafes', 'street life'], 'modern_architecture': ['modern', 'skyline'], 'public_art': ['art', 'mural', 'murals', 'sculpture'], 'landmark': ['landmark', 'landmarks', 'famous'], 'quiet_residential': ['quiet', 'calm', 'peaceful', 'residential'], 'industrial': ['industrial', 'factory', 'factories', 'warehouse', 'rail'], 'traffic': ['traffic', 'car', 'cars', 'busy roads'], 'bike_lane': ['bike lane', 'bike lanes', 'bike path', 'cycle lane'], 'crowds': ['crowd', 'crowds', 'crowded', 'people'], 'cleanliness': ['clean', 'tidy']}
NEGATIONS = ['avoid', 'no', 'not', 'without', 'dislike', 'hate', 'less', 'away from']
SOFTENERS = ['if possible', 'a bit', 'somewhat', 'maybe', 'prefer', 'would be nice']
MUSTS = ['must', 'have to', 'only if', 'required']

def _has(clause: str, word: str) -> bool:
    if word.isascii():
        return re.search(f'\\b{re.escape(word)}\\b', clause) is not None
    return word in clause

def _user_text(user_prompt: str) -> str:
    text = user_prompt.split('User description:\n', 1)[-1]
    return text.split('\n\nYour previous answer was rejected', 1)[0]

def fake_ask(user_prompt: str, system_instruction: str='', response_schema=None) -> str:
    text = _user_text(user_prompt).lower()
    (weights, must_have) = ({}, [])
    for clause in re.split('[,，.。;；!！?？]|\\bbut\\b', text):
        negative = any((_has(clause, w) for w in NEGATIONS))
        soft = any((_has(clause, w) for w in SOFTENERS))
        must = any((_has(clause, w) for w in MUSTS))
        for (dim, words) in KEYWORDS.items():
            hit = next((w for w in words if _has(clause, w)), None)
            if hit is None or dim in weights:
                continue
            weight = -1.0 if negative else 0.6 if soft else 1.0
            weights[dim] = {'dimension': dim, 'reason': f"mock rule: '{hit}'", 'weight': weight}
            if must and weight > 0:
                must_have.append(dim)
    return json.dumps({'weights': list(weights.values()), 'must_have': must_have}, ensure_ascii=False)
