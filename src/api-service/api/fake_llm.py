"""Offline keyword-based preference parsing for LLM_PROVIDER=mock and unit tests."""
import json
import re

KEYWORDS = {
    "water_access": ["river", "water", "waterfront", "lake", "pond", "coast", "harbor", "河", "水", "湖", "江", "海"],
    "tree_canopy": ["tree", "trees", "shade", "shady", "canopy", "树荫", "林荫", "树"],
    "grassland": ["park", "parks", "lawn", "grass", "meadow", "公园", "草坪", "草地", "绿地"],
    "open_landscape": ["open", "view", "views", "sky", "开阔", "视野", "天空"],
    "farmland_pastoral": ["farm", "farmland", "orchard", "crops", "countryside", "农田", "果园", "乡村", "田野"],
    "forest_environment": ["forest", "woods", "森林", "树林"],
    "historic_character": ["historic", "history", "old town", "历史", "古", "老街"],
    "architectural_character": ["architecture", "buildings", "建筑"],
    "street_enclosure": ["skyscraper", "skyscrapers", "tall buildings", "高楼"],
    "urban_density": ["city", "downtown", "urban", "城市", "市中心"],
    "commercial_district": ["shop", "shops", "cafe", "cafes", "coffee", "store", "stores", "商店", "咖啡", "购物"],
    "road_width": ["traffic", "car", "cars", "busy roads", "车流", "汽车", "交通", "堵车", "车多", "太多车", "有车", "车辆"],
    "industrial_area": ["industrial", "factory", "factories", "warehouse", "rail", "工业", "工厂", "铁路"],
    "visual_clutter": ["wires", "billboard", "billboards", "电线", "广告牌"],
    "scenic_appeal": ["scenic", "beautiful", "pretty", "风景", "好看", "漂亮"],
}

NEGATIONS = ["avoid", "no", "not", "without", "dislike", "hate", "less", "away from",
             "别", "不要", "避开", "讨厌", "没有", "不想", "少点", "远离"]
SOFTENERS = ["if possible", "a bit", "somewhat", "maybe", "prefer", "would be nice",
             "最好", "有点", "稍微", "可以的话", "尽量"]
MUSTS = ["must", "have to", "only if", "required", "一定要", "必须", "一定"]


# Match whole English words so "car" does not match "carefully".
def _has(clause: str, word: str) -> bool:
    if word.isascii():
        return re.search(rf"\b{re.escape(word)}\b", clause) is not None
    return word in clause


def fake_ask(user_prompt: str, system_instruction: str = "", response_schema=None) -> str:
    text = user_prompt.split("User description:\n", 1)[-1]
    text = text.split("\n\nYour previous answer was rejected", 1)[0].lower()
    weights, must_have = {}, []

    # Apply negation and soft preferences within each clause.
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
