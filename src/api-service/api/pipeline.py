# score呼叫 return pipeline.run_score(request.text, request.region_id) 时流程编排，按顺序调用各个模块，把一句话和视觉模型结果 变成六边形热图。
# 单独从score拆出来是为了不开服务器也能测整条流程

import time
from api import agent, model_client, score_math, tiles
from api.schemas import CellValue, ScoreResponse

# 区域编号不认识时抛这个错，router 接住后转成 HTTP 404
# 此处单独定义一个错误类型，免得把别的 ValueError（比如数据校验失败）也误当成 404
class UnknownRegionError(Exception):
    pass


# 完整流程：用户的话 + 视觉模型处理好的区域分数 → 热图 + 每一步的中间结果
# get_preference 可以换成假的：测试时不调 Gemini、不花钱
def run_score(text: str, region_id: str, get_preference=agent.get_preference) -> ScoreResponse:
    timings = {}

    # 计时小工具：记下当前步骤花了多少毫秒放进timing字典，用在后面每一个步骤
    clock = [time.perf_counter()]
    def lap(name):
        now = time.perf_counter()
        timings[name] = round((now - clock[0]) * 1000, 1)
        clock[0] = now

    # 1.用户的话 → agent.get_preference 做harness包裹 llm.ask，问Gemini问题并检查回复
    agent_result = get_preference(text)
    weights = agent_result.preference.as_vector() #preference中定义的方法，只抽出权重部分转化为字典，缺少的项补为0
    lap("agent")

    # 2. 根据区域名称拿到 H3 格子清单（不认识的区域 → UnknownRegionError）
    try:
        tile_list = tiles.get_tiles(region_id)
    except ValueError as e:
        raise UnknownRegionError(str(e)) from e
    lap("tiles")

    # 3. 将视觉模型打的分数填入上一步的格子清单中
    tile_scores = model_client.predict(tile_list)
    lap("model")

    # 4. 原始分 + 权重 → 每个格子的总分
    values, stats = score_math.combine(tile_scores, weights, agent_result.preference.must_have)
    lap("score")

    # 5. 返回给score.py
    return ScoreResponse(
        region_id=region_id,
        agent=agent_result,
        weights=weights,
        cells=[CellValue(h3_index=ts.h3_index, value=v) for ts, v in zip(tile_scores, values)],
        stats=stats,
        timings_ms=timings,
    )
