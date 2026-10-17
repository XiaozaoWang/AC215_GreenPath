"""SmartPath module-level architecture diagram (Chinese): one box per .py file, showing the key functions and how they call each other.
The layout follows the hand-drawn version (Frame 8): Mac → Docker VM → api-service container on top / frontend container below, browser on the left, Gemini on the right.
Run: python3 docs/diagrams/make_module_diagram.py docs/diagrams
Export PNG: open the SVG in a browser, or take a screenshot with headless Chrome.
"""
import math
import sys
from html import escape
from pathlib import Path

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
OUT.mkdir(parents=True, exist_ok=True)

CJK = "'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', 'Noto Sans CJK SC'"
SANS = f"Inter, 'Segoe UI', Helvetica, Arial, {CJK}, sans-serif"
MONO = f"Menlo, Consolas, 'DejaVu Sans Mono', {CJK}, monospace"

# One color per module (same color idea as the hand-drawn version)
C = {
    "ink": "#1f1d2b", "muted": "#6b6880", "frame": "#c9c6d6",
    "route": "#333333", "pipeline": "#0b7a5a", "agent": "#0a93b8", "prompts": "#b13fd0",
    "llm": "#5b3fe0", "tiles": "#6f8d00", "model": "#d96500", "fake_model": "#a52fc0",
    "npz": "#13919e", "score": "#1a2fa8", "contract": "#a87900", "front": "#0f9f78",
    "gemini": "#2563eb", "secret": "#c2410c", "new": "#0b7a5a", "chg": "#b45309", "fake": "#d96500",
}


# ---------------- small SVG tools ----------------
class Svg:
    def __init__(self, w, h):
        self.w, self.h, self.parts = w, h, []

    def add(self, s):
        self.parts.append(s)

    def rect(self, x, y, w, h, stroke=None, fill="#fff", rx=8, sw=1.6, dash=False):
        st = f' stroke="{stroke}" stroke-width="{sw}"' if stroke else ""
        da = ' stroke-dasharray="7 5"' if dash else ""
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}"{st}{da}/>')

    def text(self, x, y, s, size=14, weight=400, fill=None, anchor="start", mono=False):
        fam = MONO if mono else SANS
        self.add(f'<text x="{x}" y="{y}" font-family="{fam}" font-size="{size}" font-weight="{weight}" '
                 f'fill="{fill or C["ink"]}" text-anchor="{anchor}" xml:space="preserve">{escape(s)}</text>')

    # Polyline arrow + solid triangle arrowhead
    def arrow(self, pts, color, dash=False, sw=1.8):
        (x0, y0), (x1, y1) = pts[-2], pts[-1]
        a = math.atan2(y1 - y0, x1 - x0)
        bx, by = x1 - 11 * math.cos(a), y1 - 11 * math.sin(a)
        d = "M " + " L ".join(f"{x:.1f} {y:.1f}" for x, y in pts[:-1] + [(bx, by)])
        da = ' stroke-dasharray="8 6"' if dash else ""
        self.add(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{sw}" stroke-linejoin="round"{da}/>')
        lx, ly = bx + 5.5 * math.sin(a), by - 5.5 * math.cos(a)
        rx, ry = bx - 5.5 * math.sin(a), by + 5.5 * math.cos(a)
        self.add(f'<polygon points="{x1:.1f},{y1:.1f} {lx:.1f},{ly:.1f} {rx:.1f},{ry:.1f}" fill="{color}"/>')

    # Small tag: NEW / 改 (changed) / FAKE
    def tag(self, x, y, label, color):
        w = 14 + len(label) * (13 if not label.isascii() else 8)
        self.rect(x, y, w, 20, stroke=color, fill="#fff", rx=10, sw=1.3)
        self.text(x + w / 2, y + 14.5, label, size=11, weight=700, fill=color, anchor="middle")
        return w

    def render(self):
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" viewBox="0 0 {self.w} {self.h}">'
                f'<rect width="100%" height="100%" fill="#ffffff"/>' + "".join(self.parts) + "</svg>")


# One module box: title (bold, module color) + some lines (code lines in monospace) + a tag in the top-right corner
def module(s, x, y, w, h, title, lines, color, tags=(), fake=False, size=13):
    s.rect(x, y, w, h, stroke=color, sw=2, dash=fake)
    s.text(x + 14, y + 26, title, size=17, weight=700, fill=color)
    tx = x + w - 10
    for label, tcolor in reversed(tags):
        tw = 14 + len(label) * (13 if not label.isascii() else 8)
        tx -= tw
        s.tag(tx, y + 9, label, tcolor)
        tx -= 6
    for i, (line, kind) in enumerate(lines):
        s.text(x + 14, y + 50 + i * 21, line, size=size, mono=(kind == "code"),
               fill=C["ink"] if kind == "code" else C["muted"])


# Port sign
def port(s, cx, cy, label, color, caption):
    s.rect(cx - 30, cy - 14, 60, 28, stroke=color, sw=1.8, rx=5)
    s.text(cx, cy + 5, label, size=13.5, weight=700, fill=color, anchor="middle")
    s.text(cx, cy - 20, caption, size=10.5, weight=650, fill=C["muted"], anchor="middle")


NEW, CHG, FAKE = ("NEW", C["new"]), ("改", C["chg"]), ("FAKE", C["fake"])


def build():
    s = Svg(2800, 1420)
    s.text(40, 44, "SmartPath 架构图 · 合并第 1 步之后", size=28, weight=700)
    s.text(40, 72, "每个框是一个文件，写着关键函数；箭头 = 谁调用谁（颜色 = 被调用的模块）。NEW = 这次新增，改 = 这次改过，虚线框 = FAKE（以后要换成真的）。",
           size=14, fill=C["muted"])

    # ---------- outer frames: Mac / Docker VM ----------
    s.rect(30, 90, 2390, 1305, stroke=C["frame"], fill="#fbfbfd", rx=16)
    s.text(52, 122, "我的 Mac", size=18, weight=700, fill=C["muted"])
    s.rect(380, 130, 2010, 1240, stroke=C["frame"], fill="#ffffff", rx=14)
    s.text(400, 160, "Docker Desktop · Linux VM", size=17, weight=700, fill=C["muted"])
    s.text(2370, 160, "docker-compose.yml：两个容器在 smartpath-network · 一条命令 docker compose up", size=12.5,
           fill=C["muted"], anchor="end")

    # ---------- the key outside the repo ----------
    s.rect(60, 160, 280, 130, stroke=C["secret"], fill="#fff8f1", rx=10, sw=1.6)
    s.text(76, 188, "~/secrets/", size=16, weight=700, fill=C["secret"])
    s.text(76, 210, "llm-service-account.json", size=12.5, mono=True)
    s.text(76, 234, "仓库外 · 不进 git、不进镜像", size=12.5, fill=C["muted"])
    s.text(76, 256, "只读挂进容器 /secrets", size=12.5, fill=C["muted"])
    s.text(76, 276, "GOOGLE_APPLICATION_CREDENTIALS", size=11, mono=True, fill=C["muted"])
    s.arrow([(340, 225), (468, 225)], C["secret"], dash=True)

    # ---------- browser ----------
    s.rect(60, 600, 210, 190, stroke=C["front"], rx=12, sw=1.8)
    s.text(78, 632, "浏览器", size=20, weight=700)
    s.text(78, 656, "localhost:3000", size=13, mono=True, fill=C["muted"])
    s.text(78, 684, "① 先从 :3000 拿页面", size=13, fill=C["muted"])
    s.text(78, 706, "② 页面 JS 再调 :9000", size=13, fill=C["muted"])
    s.text(78, 734, "（发请求用随机端口，", size=12, fill=C["muted"])
    s.text(78, 754, "  目标端口固定）", size=12, fill=C["muted"])
    s.arrow([(210, 600), (210, 372), (302, 372)], C["llm"], sw=2)
    s.text(200, 470, "② POST /score", size=13, weight=650, fill=C["llm"], anchor="end")
    s.text(200, 490, "GET /source/…", size=13, weight=650, fill=C["llm"], anchor="end")
    s.text(200, 510, "GET /（健康灯）", size=13, weight=650, fill=C["llm"], anchor="end")
    s.arrow([(210, 790), (210, 1255), (302, 1255)], C["front"], sw=2)
    s.text(200, 1030, "① 取页面", size=13, weight=650, fill=C["front"], anchor="end")

    # ---------- api-service container ----------
    s.rect(470, 195, 1890, 817, stroke="#7c4dff", fill="#f8f6ff", rx=14, sw=2)
    s.text(490, 225, "api-service container", size=20, weight=700, fill="#7c4dff")
    s.text(710, 225, "镜像 smartpath-api-service · python:3.12 + uv（/.venv）· 挂载 ./api-service → /app · healthcheck：每 10 秒 GET /",
           size=12.5, fill=C["muted"])

    # Port: Mac 9000 → container 9000, requests go into Uvicorn
    port(s, 334, 372, "9000", C["llm"], "Mac")
    port(s, 470, 372, "9000", C["llm"], "容器")
    s.add(f'<line x1="364" y1="372" x2="440" y2="372" stroke="{C["llm"]}" stroke-width="2"/>')
    s.arrow([(500, 372), (840, 372), (840, 337)], C["route"], dash=True)
    s.text(560, 392, "Docker 端口转发 · 请求进来交给 Uvicorn（监听 :9000）", size=12, fill=C["muted"])

    # Row 1: entrypoint → Uvicorn → service.py; top right: contracts
    module(s, 500, 262, 220, 75, "docker-entrypoint.sh", [("激活 /.venv", "text"), ("DEV≠1 → 直接启动服务", "text")], C["route"])
    module(s, 760, 262, 160, 75, "Uvicorn", [("api.service:app", "code"), ("--port 9000", "code")], C["route"])
    s.arrow([(720, 300), (758, 300)], C["route"])
    s.arrow([(920, 300), (958, 300)], C["route"])
    module(s, 960, 252, 560, 150, "service.py（大门）", [
        ("app = FastAPI()", "code"),
        ("→ add_middleware(CORS)：允许 :3000 的页面调 :9000", "code"),
        ("→ include_router(score.router) · include_router(source.router)", "code"),
        ("GET / → get_index()：健康检查用", "code"),
    ], C["route"])
    module(s, 1560, 252, 780, 150, "契约（所有模块都 import）", [
        ("dimensions.json：20 维 · schema_version · score_range [0, 1]", "code"),
        ("  → dimensions.py 读出 DIMENSIONS · DIMENSION_NAMES · SCORE_MIN / MAX", "code"),
        ("schemas.py：Preference · AgentResult · Tile · TileScore · ScoreResponse", "code"),
        ("维度说明 → prompts 发给 Gemini；维度白名单 + 0-1 量程 → schemas 校验", "text"),
    ], C["contract"], tags=(NEW,))

    # Row 2: routers → pipeline → score_math
    module(s, 500, 430, 520, 220, "routers/score.py（前台）", [
        ("@router.post /preference", "code"),
        ("  post_preference → agent.get_preference(text)", "code"),
        ("@router.post /score", "code"),
        ("  post_score → pipeline.run_score(text, region_id)", "code"),
        ("  UnknownRegionError → HTTP 404", "code"),
        ("", "text"),
        ("routers/source.py：GET /source 只读展示 api/*.py", "text"),
    ], C["route"], tags=(CHG,))
    s.arrow([(1000, 402), (1000, 428)], C["route"])
    module(s, 1060, 430, 700, 220, "pipeline.py（厨房）· run_score()", [
        ("① agent_result = get_preference(text) → weights = .as_vector()", "code"),
        ("② tile_list = tiles.get_tiles(region_id)", "code"),
        ("     不认识的区域 → UnknownRegionError", "code"),
        ("③ tile_scores = model_client.predict(tile_list)", "code"),
        ("④ heatmap, stats = score_math.combine(tile_scores, weights, must_have)", "code"),
        ("⑤ return ScoreResponse（每步用 lap() 计时）", "code"),
        ("只管先做什么后做什么，不知道 HTTP；不开服务器也能测整条流程", "text"),
    ], C["pipeline"], tags=(NEW,))
    s.arrow([(1020, 510), (1058, 510)], C["pipeline"])
    module(s, 1820, 430, 520, 200, "score_math.py", [
        ("combine(tile_scores, weights, must_have, rmse)", "code"),
        ("z-score：σ = max(区域 std, PLACEHOLDER_RMSE=0.05)", "code"),
        ("Z 截到 ±3 → t = Z·w（每格一个总分）", "code"),
        ("must_have 门槛：原始分 ≥ 0.1，否则 None", "code"),
        ("→ 100×100 热图 + ScoreStats", "code"),
    ], C["score"], tags=(CHG,))
    s.arrow([(1760, 510), (1818, 510)], C["score"])
    s.text(1789, 500, "④", size=13, weight=700, fill=C["score"], anchor="middle")

    # Row 3: agent / prompts · tiles · model_client · fake_model / npz · llm / fake_llm
    module(s, 500, 710, 340, 140, "agent.py（harness）", [
        ("get_preference(text, ask=llm.ask)", "code"),
        ("循环 ≤ 3 次：拼 prompt → ask → 校验", "text"),
        ("不合格带错误重问 · 失败用全 0 保底", "text"),
        ("→ AgentResult", "code"),
    ], C["agent"])
    s.arrow([(600, 650), (600, 708)], C["agent"])
    s.arrow([(1075, 650), (1075, 680), (760, 680), (760, 708)], C["agent"])
    s.text(790, 674, "① get_preference()", size=12.5, weight=650, fill=C["agent"])
    module(s, 500, 880, 340, 105, "prompts.py", [
        ("SYSTEM_INSTRUCTION（含 20 维说明）", "code"),
        ("build_user_prompt(text, error)", "code"),
    ], C["prompts"])
    s.arrow([(670, 850), (670, 878)], C["prompts"])

    module(s, 890, 710, 260, 180, "tiles.py", [
        ("get_tiles(region_id)", "code"),
        ("FAKE_REGIONS →", "code"),
        ("  fake_grid_tiles()", "code"),
        ("→ 1 万个 Tile", "text"),
        ("{tile_id, row, col}", "code"),
    ], C["tiles"], tags=(FAKE,), fake=True)
    s.arrow([(1125, 650), (1125, 708)], C["tiles"])
    s.text(1133, 690, "②", size=13, weight=700, fill=C["tiles"])

    module(s, 1190, 710, 340, 230, "model_client.py", [
        ("predict(tiles)", "code"),
        ("→ fake_model.fake_score_maps()", "code"),
        ("每格按 row/col 查 20 张表", "text"),
        ("→ TileScore（0-1，保留 3 位）", "code"),
        ("check_tile_ids()：门卫", "code"),
        ("少交 / 多交 / 重复 / 错位", "text"),
        ("  → TileMismatchError", "code"),
    ], C["model"], tags=(CHG,))
    s.arrow([(1360, 650), (1360, 708)], C["model"])
    s.text(1368, 690, "③", size=13, weight=700, fill=C["model"])

    module(s, 1570, 710, 300, 130, "fake_model.py", [
        ("fake_score_maps(rows, cols)", "code"),
        ("读 npz 一次，缓存在 _cache", "text"),
        ("真模型接上后整个删掉", "text"),
    ], C["fake_model"], tags=(FAKE,), fake=True)
    s.arrow([(1530, 770), (1568, 770)], C["fake_model"])
    module(s, 1570, 870, 300, 115, "charles_river_scores.npz", [
        ("20 张 100×100 分数表（0-1）", "text"),
        ("离线生成：scripts/", "code"),
        ("fake_scores_from_image.py", "code"),
    ], C["npz"], tags=(FAKE,), fake=True, size=12.5)
    s.arrow([(1720, 840), (1720, 868)], C["npz"])

    module(s, 1930, 710, 410, 140, "llm.py（电话线）", [
        ("ask(prompt, instruction, schema)", "code"),
        ("LLM_PROVIDER=mock → fake_llm", "code"),
        ("否则 _get_client()（第一次才建）", "code"),
        ("→ generate_content(response_schema=Preference)", "code"),
    ], C["llm"], tags=(CHG,), size=12.5)
    module(s, 1930, 880, 410, 105, "fake_llm.py", [
        ("fake_ask()：关键词规则 → 同格式 JSON", "code"),
        ("不要 key、不花钱（离线 / 测试用）", "text"),
    ], C["llm"], tags=(NEW, FAKE), fake=True)
    s.arrow([(2135, 850), (2135, 878)], C["llm"], dash=True)
    s.text(2145, 870, "mock", size=11.5, weight=650, fill=C["llm"])
    # agent → llm: take the bottom lane, going around the boxes in the middle
    s.arrow([(840, 800), (866, 800), (866, 997), (1905, 997), (1905, 780), (1928, 780)], C["llm"])
    s.text(1385, 992, "ask(prompt, SYSTEM_INSTRUCTION, Preference)", size=12, weight=650, fill=C["llm"], anchor="middle")

    # ---------- Gemini (in the cloud) ----------
    s.rect(2480, 690, 290, 140, stroke=C["gemini"], fill="#f3f6ff", rx=12, sw=2)
    s.text(2500, 722, "Gemini", size=22, weight=700, fill=C["gemini"])
    s.text(2500, 746, "Vertex AI · Google Cloud", size=13, fill=C["muted"])
    s.text(2500, 770, "gemini-3.1-flash-lite", size=12.5, mono=True)
    s.text(2500, 794, "用 ~/secrets 的 key 认证", size=12.5, fill=C["muted"])
    s.text(2500, 815, "（mock 模式不会走到这里）", size=12, fill=C["muted"])
    s.arrow([(2340, 760), (2478, 760)], C["gemini"], sw=2)
    s.text(2410, 752, "HTTPS", size=11.5, weight=650, fill=C["gemini"], anchor="middle")

    # ---------- between the two containers: health check ----------
    s.arrow([(2200, 1050), (2200, 1014)], C["front"], dash=True)
    s.text(2190, 1036, "depends_on: service_healthy —— 后端 healthy 了才启动前端", size=12.5, weight=650,
           fill=C["front"], anchor="end")

    # ---------- frontend container ----------
    s.rect(470, 1050, 1890, 305, stroke=C["front"], fill="#f3fbf8", rx=14, sw=2)
    s.text(490, 1080, "frontend container", size=20, weight=700, fill=C["front"])
    s.text(690, 1080, "镜像 smartpath-frontend · node:20-alpine（Dockerfile.dev）· 挂载 ./frontend → /app（改了页面自动刷新）",
           size=12.5, fill=C["muted"])
    port(s, 334, 1255, "3000", C["front"], "Mac")
    port(s, 470, 1255, "3000", C["front"], "容器")
    s.add(f'<line x1="364" y1="1255" x2="440" y2="1255" stroke="{C["front"]}" stroke-width="2"/>')
    module(s, 500, 1110, 300, 75, "启动命令", [("npm install && npm run dev", "code")], C["front"])
    module(s, 850, 1110, 260, 75, "Next.js 开发服务器", [("监听 :3000", "code")], C["front"])
    s.arrow([(800, 1148), (848, 1148)], C["front"])
    s.arrow([(500, 1255), (980, 1255), (980, 1187)], C["front"], dash=True)
    s.text(560, 1278, "请求进来交给 Next.js", size=12, fill=C["muted"])
    s.text(500, 1312, "第一次访问时把 HTML + JS 发给浏览器，之后代码在浏览器里跑，", size=12.5, fill=C["muted"])
    s.text(500, 1332, "所以页面调 API 是浏览器直接调 :9000，不经过这个容器。", size=12.5, fill=C["muted"])
    module(s, 1160, 1105, 1180, 235, "页面代码（frontend/src）", [
        ("app/page.jsx：三栏布局 · submit() 回车提交", "code"),
        ("components/layout/Sidebar.jsx：导航 · 每 15 秒健康灯", "code"),
        ("components/heatmap/Heatmap.jsx · Legend.jsx：canvas 画热图（青绿 = 好，珊瑚红 = 差）", "code"),
        ("components/pipeline/Inspector.jsx：右侧标签页（Overview / Harness / Weights / Stats / JSON / Code）", "code"),
        ("services/DataService.js：Score() → POST /score · Source() → GET /source · Health() → GET /", "code"),
    ], C["front"], size=13)
    s.arrow([(1110, 1148), (1158, 1148)], C["front"])

    # ---------- legend ----------
    s.rect(2460, 880, 320, 300, stroke=C["frame"], rx=12)
    s.text(2480, 910, "怎么读这张图", size=16, weight=700)
    s.arrow([(2482, 938), (2540, 938)], C["agent"])
    s.text(2552, 943, "调用（颜色 = 被调用的模块）", size=12.5)
    s.arrow([(2482, 966), (2540, 966)], C["route"], dash=True)
    s.text(2552, 971, "端口 / 挂载 / 条件", size=12.5)
    s.tag(2480, 986, "NEW", C["new"])
    s.text(2552, 1001, "这次新增", size=12.5)
    s.tag(2480, 1014, "改", C["chg"])
    s.text(2552, 1029, "这次改过", size=12.5)
    s.rect(2480, 1044, 58, 22, stroke=C["fake"], dash=True, rx=5)
    s.text(2552, 1060, "FAKE：以后换成真的", size=12.5)
    s.text(2480, 1092, "①–⑤ = pipeline 里的执行顺序", size=12.5, fill=C["muted"])
    s.text(2480, 1114, "LLM_PROVIDER=mock 时", size=12.5, fill=C["muted"])
    s.text(2480, 1134, "  llm.py → fake_llm.py，不连 Gemini", size=12.5, fill=C["muted"])
    s.text(2480, 1160, "分数量程全组统一 0-1", size=12.5, fill=C["muted"])

    (OUT / "smartpath-modules-zh.svg").write_text(s.render(), encoding="utf-8")
    print("ok")


build()
