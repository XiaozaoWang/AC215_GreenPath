# ⚠️ 整个文件都是假的（FAKE）：Gemini 的"离线替身"，不调任何模型、不需要 key、不花钱。
# 用途：没有 GCP key 的队友也能把系统跑起来；单元测试和 CI 不用连 Gemini。
# 开关：环境变量 LLM_PROVIDER=mock 时，llm.ask() 会把请求转给这里的 fake_ask()。
# 做法：照搬朱同学版本 MockLLMProvider 的思路（看关键词给分），改成我们的 16 维 + 中英文关键词。
# 很笨：只认关键词，不懂语义，没有联想权重；真正的偏好解析还是靠 Gemini。
import json
import re

# 维度 → 关键词（英文按整词匹配，中文按包含匹配）。只是演示，命中不了的说法就当没提到
# 没有“车流”这个维度：车多、交通这类词按 road_width 处理（路越宽车越多），和真 prompt 的替代规则一致
KEYWORDS = {
    "water_access": ["river", "water", "waterfront", "lake", "pond", "coast", "harbor", "河", "水", "湖", "江", "海"],
    "tree_canopy": ["tree", "trees", "shade", "shady", "canopy", "树荫", "林荫", "树"],
    "grassland": ["park", "parks", "lawn", "grass", "meadow", "公园", "草坪", "草地", "绿地"],
    "open_landscape": ["open", "view", "views", "sky", "开阔", "视野", "天空"],
    "farmland_pastoral": ["farm", "farmland", "orchard", "crops", "countryside", "农田", "果园", "乡村", "田野"],
    "forest_environment": ["forest", "woods", "森林", "树林"],
    "hilly_terrain": ["hill", "hills", "mountain", "mountains", "山", "丘陵"],
    "historic_character": ["historic", "history", "old town", "历史", "古", "老街"],
    "architectural_character": ["architecture", "buildings", "建筑"],
    "street_enclosure": ["skyscraper", "skyscrapers", "tall buildings", "高楼"],
    "urban_density": ["city", "downtown", "urban", "城市", "市中心"],
    "commercial_district": ["shop", "shops", "cafe", "cafes", "coffee", "store", "stores", "商店", "咖啡", "购物"],
    # 不用单个"车"：否则"自行车道"也会被当成车流
    "road_width": ["traffic", "car", "cars", "busy roads", "车流", "汽车", "交通", "堵车", "车多", "太多车", "有车", "车辆"],
    "industrial_area": ["industrial", "factory", "factories", "warehouse", "rail", "工业", "工厂", "铁路"],
    "visual_clutter": ["wires", "billboard", "billboards", "电线", "广告牌"],
    "scenic_appeal": ["scenic", "beautiful", "pretty", "风景", "好看", "漂亮"],
}

# 否定词：同一小句里出现，就算"不想要"（权重 −1）
NEGATIONS = ["avoid", "no", "not", "without", "dislike", "hate", "less", "away from",
             "别", "不要", "避开", "讨厌", "没有", "不想", "少点", "远离"]
# 软化词：同一小句里出现，权重降到 0.6
SOFTENERS = ["if possible", "a bit", "somewhat", "maybe", "prefer", "would be nice",
             "最好", "有点", "稍微", "可以的话", "尽量"]
# 必须词：同一小句里出现，而且是"想要"，就放进 must_have
MUSTS = ["must", "have to", "only if", "required", "一定要", "必须", "一定"]


# 一个词是否出现在小句里：英文按整词（避免 "car" 命中 "carefully"），中文直接看包含
def _has(clause: str, word: str) -> bool:
    if word.isascii():
        return re.search(rf"\b{re.escape(word)}\b", clause) is not None
    return word in clause


# 从 llm.ask 收到的 prompt 里取出用户原话（prompts.build_user_prompt 拼的格式："User description:\n<原话>"）
def _user_text(user_prompt: str) -> str:
    text = user_prompt.split("User description:\n", 1)[-1]
    return text.split("\n\nYour previous answer was rejected", 1)[0]


# 替身本体：参数和 llm.ask() 完全一样，返回同样格式的 JSON 文本（照样要过 pydantic 校验）
# system_instruction、response_schema 不使用，只是为了和 llm.ask() 签名一致、可以直接替换
def fake_ask(user_prompt: str, system_instruction: str = "", response_schema=None) -> str:
    text = _user_text(user_prompt).lower()
    weights, must_have = {}, []

    # 按标点和 "but" 切成小句，每个小句单独判断正负、软硬
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
