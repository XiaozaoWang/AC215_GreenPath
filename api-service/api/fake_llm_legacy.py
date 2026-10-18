# ⚠️ This whole file is fake (FAKE): an "offline stand-in" for Gemini; it calls no model, needs no key, costs nothing.
# Use: teammates without a GCP key can still run the system; unit tests and CI don't need to connect to Gemini.
# Switch: when env variable LLM_PROVIDER=mock, llm.ask() hands the request to fake_ask() here.
# How: copies the idea of MockLLMProvider from Zhu's version (give points by keywords), changed to our 20 dimensions + Chinese and English keywords.
# Pretty dumb: only knows keywords, doesn't understand meaning, no associated weights; the real preference parsing still relies on Gemini.
import json
import re

# Dimension → keywords (English is matched as whole words, Chinese by contains). Just a demo; wording it can't match counts as not mentioned
KEYWORDS = {
    "water": ["river", "water", "waterfront", "lake", "pond", "coast", "河", "水", "湖", "江", "海"],
    "tree_canopy": ["tree", "trees", "shade", "shady", "canopy", "树荫", "林荫", "树"],
    "park": ["park", "parks", "lawn", "green", "公园", "草坪", "绿地"],
    "open_field": ["field", "meadow", "open", "草地", "开阔", "田野"],
    "farmland": ["farm", "farmland", "orchard", "crops", "农田", "果园"],
    "forest": ["forest", "woods", "森林", "树林"],
    "hills": ["hill", "hills", "slope", "山", "坡"],
    "sky_view": ["sky", "天空"],
    "gardens": ["garden", "gardens", "flower", "flowers", "花园", "花"],
    "historic_buildings": ["historic", "history", "old buildings", "历史", "古"],
    "cultural_street": ["cultural", "culture", "shops", "cafe", "cafes", "street life", "人文", "文化", "街道", "咖啡", "商店"],
    "modern_architecture": ["modern", "skyline", "现代", "高楼"],
    "public_art": ["art", "mural", "murals", "sculpture", "艺术", "壁画", "雕塑"],
    "landmark": ["landmark", "landmarks", "famous", "地标", "景点"],
    "quiet_residential": ["quiet", "calm", "peaceful", "residential", "安静", "宁静", "住宅"],
    "industrial": ["industrial", "factory", "factories", "warehouse", "rail", "工业", "工厂", "铁路", "工地"],
    # Don't use a single "车" (car): otherwise "自行车道" (bike lane) would also be taken as traffic
    "traffic": ["traffic", "car", "cars", "busy roads", "车流", "汽车", "交通", "堵车", "车多", "太多车", "有车", "车辆"],
    "bike_lane": ["bike lane", "bike lanes", "bike path", "cycle lane", "自行车道", "骑行道"],
    "crowds": ["crowd", "crowds", "crowded", "people", "人多", "拥挤", "人群"],
    "cleanliness": ["clean", "tidy", "干净", "整洁"],
}

# Negation words: if one shows up in the same clause, it counts as "don't want" (weight −1)
NEGATIONS = ["avoid", "no", "not", "without", "dislike", "hate", "less", "away from",
             "别", "不要", "避开", "讨厌", "没有", "不想", "少点", "远离"]
# Softening words: if one shows up in the same clause, the weight drops to 0.6
SOFTENERS = ["if possible", "a bit", "somewhat", "maybe", "prefer", "would be nice",
             "最好", "有点", "稍微", "可以的话", "尽量"]
# Must words: if one shows up in the same clause and it's a "want", put it into must_have
MUSTS = ["must", "have to", "only if", "required", "一定要", "必须", "一定"]


# Whether a word appears in a clause: English by whole word (so "car" doesn't match "carefully"), Chinese just checks contains
def _has(clause: str, word: str) -> bool:
    if word.isascii():
        return re.search(rf"\b{re.escape(word)}\b", clause) is not None
    return word in clause


# Pull the user's original words out of the prompt from llm.ask (the format built by prompts.build_user_prompt: "User description:\n<original words>")
def _user_text(user_prompt: str) -> str:
    text = user_prompt.split("User description:\n", 1)[-1]
    return text.split("\n\nYour previous answer was rejected", 1)[0]


# The stand-in itself: same parameters as llm.ask(), returns JSON text in the same format (it still has to pass the pydantic checks)
# system_instruction and response_schema aren't used; they're only there to match llm.ask()'s signature, so it can be swapped in directly
def fake_ask(user_prompt: str, system_instruction: str = "", response_schema=None) -> str:
    text = _user_text(user_prompt).lower()
    weights, must_have = {}, []

    # Split into clauses by punctuation and "but"; judge positive/negative and soft/hard for each clause separately
    for clause in re.split(r"[,，.。;；!！?？]|\bbut\b|但是|但", text):
        negative = any(_has(clause, w) for w in NEGATIONS)
        soft = any(_has(clause, w) for w in SOFTENERS)
        must = any(_has(clause, w) for w in MUSTS)
        for dim, words in KEYWORDS.items():
            hit = next((w for w in words if _has(clause, w)), None)
            if hit is None or dim in weights:
                continue
            weight = -1.0 if negative else (0.6 if soft else 1.0)
            weights[dim] = {"dimension": dim, "reason": f"mock rule: '{hit}'", "weight": weight}
            if must and weight > 0:
                must_have.append(dim)

    return json.dumps({"weights": list(weights.values()), "must_have": must_have}, ensure_ascii=False)
