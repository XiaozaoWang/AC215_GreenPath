"""Generate SmartPath architecture + request-flow diagrams as SVG (editable in Figma).
Run: python3 docs/diagrams/make_diagrams.py docs/diagrams   (PNG export: open the SVG in a browser, or headless Chrome)
"""
import sys
from html import escape
from pathlib import Path

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
LANG = sys.argv[2] if len(sys.argv) > 2 else "en"  # "en" or "zh"
SUFFIX = "" if LANG == "en" else f"-{LANG}"

CJK = "'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', 'Noto Sans CJK SC'"
SANS = f"Inter, 'Segoe UI', Helvetica, Arial, {CJK}, sans-serif"
MONO = f"Menlo, Consolas, 'DejaVu Sans Mono', {CJK}, monospace"

# palette (one color per module, reused in both diagrams)
C = {
    "ink": "#1e1b2e", "muted": "#6b6880", "line": "#8b88a0", "frame": "#cfcbe0",
    "page": "#0f9f78", "page_bg": "#e7f6f1",
    "route": "#475569", "route_bg": "#eef0f4",
    "harness": "#7c4dff", "harness_bg": "#f0ebff",
    "gemini": "#2563eb", "gemini_bg": "#e8efff",
    "tiles": "#0d9488", "tiles_bg": "#e3f5f3",
    "model": "#d97706", "model_bg": "#fdf2e2",
    "score": "#db2777", "score_bg": "#fdeaf3",
    "shared": "#64748b", "shared_bg": "#f3f4f7",
    "api_band": "#f6f3ff", "violet": "#7c4dff", "pink": "#ec4899",
}


# Estimate text width: CJK characters are about 1 font size wide, others about 0.56
def text_width(s, size):
    return sum(size * (1.0 if ord(ch) > 0x2E80 else 0.56) for ch in s)


# Chinese version: English text → Chinese (function names, file names and formulas aren't translated)
ZH = {
    # ---- architecture diagram ----
    "SmartPath · Milestone 2 architecture": "SmartPath · Milestone 2 架构图",
    "Everything runs locally with one command (docker compose up). Only Gemini (and later the shared bucket) is in the cloud.":
        "所有东西用一条命令（docker compose up）在本地跑起来；只有 Gemini（以后再加组里共享的 bucket）在云上。",
    "Your Mac": "你的 Mac",
    "Browser": "浏览器",
    "1) loads the page": "① 先从 frontend",
    "    from the frontend": "    拿到页面",
    "2) the page's JS then": "② 页面里的 JS 再去",
    "    calls the API on :9000": "    调 :9000 的 API",
    "Docker Desktop · Linux VM": "Docker Desktop · 隐藏的 Linux VM",
    "docker network: smartpath-network": "docker 网络：smartpath-network",
    "api-service container": "api-service 容器",
    "image smartpath-api-service · python:3.12-slim · uv venv at /.venv (pinned by uv.lock)":
        "镜像 smartpath-api-service · python:3.12-slim · uv 虚拟环境装在 /.venv（版本锁在 uv.lock）",
    "volumes: ./api-service → /app (live code) · ../secrets → /secrets (read-only)":
        "挂载：./api-service → /app（代码，改了就生效）· ../secrets → /secrets（只读）",
    "activates /.venv": "激活 /.venv",
    "starts uvicorn": "启动 uvicorn",
    "CORSMiddleware: lets JS from :3000 call :9000": "CORSMiddleware：允许 :3000 页面里的 JS 调 :9000",
    "route table": "路由表",
    "post_score(): calls the modules in order": "post_score()：按顺序调用各模块",
    "read-only view of api/*.py (whitelist)": "只读展示 api/*.py（白名单）",
    "harness": "harness（把门）",
    "real interface": "真接口，以后不变",
    "loads .npz once": "只读一次 .npz",
    "made offline by scripts/": "由 scripts/ 离线生成：",
    "schemas.py (pydantic data contracts)  ·  dimensions.py (the 20 scenery dimensions)  →  imported by every module":
        "schemas.py（pydantic 数据契约）  ·  dimensions.py（20 个风景维度）  →  所有模块都 import 它们",
    "frontend container": "frontend 容器",
    "image smartpath-frontend · node:20-alpine (Dockerfile.dev) · volume ./frontend → /app":
        "镜像 smartpath-frontend · node:20-alpine（Dockerfile.dev）· 挂载 ./frontend → /app",
    "entrypoint": "启动命令",
    "Next.js dev server": "Next.js 开发服务器",
    "listens on :3000": "守在 :3000",
    "On the first visit it sends": "第一次访问时，",
    "HTML + JS to the browser.": "把 HTML + JS 发给浏览器；",
    "From then on the code runs": "之后代码在浏览器里跑，",
    "in the browser, not here.": "不在这个容器里。",
    "page code (frontend/src)": "页面代码（frontend/src）",
    "app/page.jsx · 3-column layout, submit()": "app/page.jsx · 三栏布局、submit()",
    "① GET page": "① 取页面",
    "container": "容器",
    "Docker port forwarding": "Docker 端口转发",
    "② page JS → API": "② 页面 JS → API",
    "GET /  (health)": "GET /（健康检查）",
    "project ac215-507902": "项目 ac215-507902",
    "structured output = Preference": "结构化输出 = Preference",
    "called only by llm.ask()": "只有 llm.ask() 会调它",
    "GCS bucket (shared, team)": "GCS bucket（全组共享）",
    "written by teammates' jobs": "队友的 job 往里写",
    "not read by the app in M2": "M2 的 app 还不读它",
    "Teammates' containers (jobs)": "队友的容器（job）",
    "Later: a model service that": "以后：真模型服务",
    "replaces fake_model.py behind": "替换 fake_model.py，",
    "model_client.predict()": "接口仍是 model_client.predict()",
    "outside the repo · never in git or an image": "在仓库外面 · 不进 git、不进镜像",
    "mounted read-only into api-service": "只读挂进 api-service",
    "GOOGLE_APPLICATION_CREDENTIALS": "环境变量 GOOGLE_APPLICATION_CREDENTIALS 指向它",
    "Legend": "图例",
    "frontend / browser": "前端 / 浏览器",
    "FastAPI app & routers (gray arrows = router calls)": "FastAPI app 和路由（灰箭头 = 路由在调用）",
    "harness (agent, prompts, llm)": "harness（agent、prompts、llm）",
    "tiles": "切格子",
    "model (fake in M2)": "模型（M2 是假的）",
    "score math": "打分数学",
    "solid = runtime call · dashed = config / future · FAKE = M2 stand-in": "实线 = 运行时调用 · 虚线 = 配置 / 以后 · FAKE = M2 的替身",
    # ---- request-flow diagram ----
    "SmartPath · what happens after you press Enter": "SmartPath · 按下回车之后发生了什么",
    'Example prompt: "Ride along the river, shade if possible, avoid traffic"  ·  POST /score':
        '示例输入："Ride along the river, shade if possible, avoid traffic"（沿河骑、最好有树荫、避开车流）  ·  POST /score',
    "BROWSER (code from frontend :3000)": "浏览器（代码来自 frontend :3000）",
    "api-service CONTAINER  ·  smartpath-api-service  ·  :9000": "api-service 容器  ·  smartpath-api-service  ·  :9000",
    "Page JS": "页面 JS",
    "FastAPI route": "FastAPI 路由",
    "Harness": "Harness（把门）",
    "Tiles": "切格子",
    "Model": "模型",
    "Score math": "打分数学",
    "Vertex AI · called by llm.py": "Vertex AI · 由 llm.py 调用",
    "you press Enter": "你按下回车",
    "② request arrives": "② 请求到达",
    "pydantic checks ScoreRequest": "pydantic 检查 ScoreRequest",
    "empty text → 422 error": "空文字 → 返回 422 错误",
    "loop · up to MAX_ATTEMPTS = 3": "循环 · 最多 MAX_ATTEMPTS = 3 次",
    "④ build the prompt": "④ 拼 prompt",
    "error = None on the 1st try": "第一次 error = None",
    "⑥ raw JSON text": "⑥ 原样的 JSON 文本",
    "⑦ verify (pydantic)": "⑦ 校验（pydantic）",
    "✓ valid  → leave the loop": "✓ 合格 → 跳出循环",
    "✗ ValidationError → back to ④": "✗ ValidationError → 回到 ④",
    "    with the error in the prompt": "    把错误写进 prompt 重问",
    "all 3 fail → Preference() with all weights 0 (fallback)": "3 次都不合格 → Preference()，权重全 0（保底）",
    "→ 20 weights, unmentioned = 0": "→ 20 维权重，没提到的 = 0",
    "10,000 Tile": "10,000 个 Tile",
    "FAKE in M2": "M2 里是假的",
    "  charles_river_scores.npz (cached)": "  charles_river_scores.npz（缓存）",
    "→ 20 maps of 100 × 100": "→ 20 张 100 × 100 的分数图",
    "10,000 TileScore": "10,000 个 TileScore",
    "20 raw scores each (0–100)": "每个 20 维原始分（0–100）",
    "validated by schemas.TileScore": "由 schemas.TileScore 校验",
    "the math": "打分数学",
    "standardize(): μ per dimension,": "standardize()：每维算区域均值 μ，",
    "t = Z · w   (one total per tile)": "t = Z · w（每格一个总分）",
    "must_have gate: raw ≥ 10, else None": "must_have 门槛：原始分 ≥ 10，否则 None",
    "heatmap 100 × 100 + ScoreStats": "热图 100 × 100 + ScoreStats",
    "t per tile · μ, σ per dimension · t range": "每格 t · 每维 μ、σ · t 的范围",
    "lap() timings per step": "lap() 记录每步耗时",
    "⑫ JSON (~66 KB)": "⑫ JSON（约 66 KB）",
    "⑬ draw": "⑬ 画出来",
    "→ Heatmap: canvas, colorFor()": "→ Heatmap 用 colorFor() 上色",
    "→ Legend · Inspector tabs": "→ 图例 · 右侧 Inspector 标签页",
    "Typical time:  ②–⑧ harness 1.4–2.0 s (Gemini)  ·  ⑨ tiles ~15 ms  ·  ⑩ model ~80 ms  ·  ⑪ score math ~12 ms":
        "典型耗时：  ②–⑧ harness 1.4–2.0 秒（Gemini）  ·  ⑨ 切格子约 15 ms  ·  ⑩ 模型约 80 ms  ·  ⑪ 打分数学约 12 ms",
    "Other calls from the page:  Sidebar → GET / every 15 s → get_index() (service.py)  ·  Code tab → GET /source/{path} → get_source() (routers/source.py)":
        "页面的其他请求：  侧栏每 15 秒 GET / → get_index()（service.py）  ·  Code 标签页 GET /source/{path} → get_source()（routers/source.py）",
}


# Translate a piece of text into the current language; things not in the table (code, file names) are returned as is
def tr(s):
    return ZH.get(s, s) if LANG == "zh" else s


class Svg:
    def __init__(self, w, h):
        self.w, self.h, self.parts = w, h, []

    def add(self, s):
        self.parts.append(s)

    def rect(self, x, y, w, h, fill="#fff", stroke=None, rx=10, dash=False, sw=1.5):
        st = f' stroke="{stroke}" stroke-width="{sw}"' if stroke else ""
        da = ' stroke-dasharray="6 5"' if dash else ""
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}"{st}{da}/>')

    def text(self, x, y, s, size=14, weight=400, fill=None, anchor="start", mono=False, italic=False):
        s = tr(s)
        fam = MONO if mono else SANS
        it = ' font-style="italic"' if italic else ""
        self.add(f'<text x="{x}" y="{y}" font-family="{fam}" font-size="{size}" font-weight="{weight}" '
                 f'fill="{fill or C["ink"]}" text-anchor="{anchor}" xml:space="preserve"{it}>{escape(s)}</text>')

    def lines(self, x, y, items, size=13, gap=None, **kw):
        gap = gap or size * 1.45
        for i, s in enumerate(items):
            self.text(x, y + i * gap, s, size=size, **kw)

    def arrow(self, pts, color=None, dash=False, sw=1.8, head=True):
        """Polyline + an explicit triangle head (works in every renderer and imports cleanly into Figma)."""
        import math
        color = color or C["line"]
        (x0, y0), (x1, y1) = pts[-2], pts[-1]
        ang = math.atan2(y1 - y0, x1 - x0)
        hl, hw = 11, 5.5
        # stop the line at the base of the head so it does not poke through
        bx, by = x1 - hl * math.cos(ang), y1 - hl * math.sin(ang)
        line_pts = pts[:-1] + [(bx, by)] if head else pts
        d = "M " + " L ".join(f"{x:.1f} {y:.1f}" for x, y in line_pts)
        da = ' stroke-dasharray="10 7"' if dash else ""
        self.add(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{sw}" stroke-linejoin="round"{da}/>')
        if head:
            lx, ly = bx + hw * math.sin(ang), by - hw * math.cos(ang)
            rx, ry = bx - hw * math.sin(ang), by + hw * math.cos(ang)
            self.add(f'<polygon points="{x1:.1f},{y1:.1f} {lx:.1f},{ly:.1f} {rx:.1f},{ry:.1f}" fill="{color}"/>')

    def pill(self, cx, cy, label, color, bg, size=15, w=None):
        w = w or text_width(tr(label), size) + 34
        self.rect(cx - w / 2, cy - 15, w, 30, fill=bg, stroke=color, rx=15, sw=1.4)
        self.text(cx, cy + 5, label, size=size, weight=600, fill=color, anchor="middle")

    def render(self):
        markers = ""
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" viewBox="0 0 {self.w} {self.h}">'
                f'<defs>{markers}</defs><rect width="100%" height="100%" fill="#ffffff"/>' + "".join(self.parts) + "</svg>")



def box(s, x, y, w, h, title, sub=(), color="route", mono_sub=True, fake=False):
    """A module box: colored left accent, bold title, small mono lines."""
    s.rect(x, y, w, h, fill="#fff", stroke=C[color], rx=10, sw=1.4, dash=fake)
    s.rect(x, y, 6, h, fill=C[color], rx=3)
    s.text(x + 16, y + 24, title, size=15, weight=650, fill=C["ink"])
    if fake:
        s.rect(x + w - 54, y + 9, 44, 18, fill=C["model_bg"], stroke=C["model"], rx=9, sw=1)
        s.text(x + w - 32, y + 22, "FAKE", size=10, weight=700, fill=C["model"], anchor="middle")
    s.lines(x + 16, y + 44, list(sub), size=12, mono=mono_sub, fill=C["muted"])



def port_pair(s, y, port, color, note_above=True):
    """Mac port → container port: Docker Desktop forwards the Mac's 127.0.0.1:port to the container's port inside the VM."""
    # Mac port (sitting across the VM border)
    s.rect(304, y - 15, 60, 30, fill="#fff", stroke=color, rx=6, sw=1.6)
    s.text(334, y + 5, f":{port}", size=13.5, weight=700, fill=color, anchor="middle")
    s.text(334, y - 21, "Mac", size=10.5, weight=650, fill=C["muted"], anchor="middle")
    # Container port (sitting across the container border)
    s.rect(374, y - 15, 60, 30, fill="#fff", stroke=color, rx=6, sw=1.6)
    s.text(404, y + 5, f":{port}", size=13.5, weight=700, fill=color, anchor="middle")
    s.text(404, y - 21, "container", size=10.5, weight=650, fill=C["muted"], anchor="middle")
    s.add(f'<line x1="364" y1="{y}" x2="374" y2="{y}" stroke="{color}" stroke-width="2"/>')
    # Label text: put it on the side the arrow doesn't go through
    ny = y - 58 if note_above else y + 42
    s.text(296, ny, "Docker port forwarding", size=11.5, weight=650, fill=color, anchor="end")
    s.text(296, ny + 16, f"127.0.0.1:{port} → {port}", size=11.5, mono=True, fill=C["muted"], anchor="end")


# =====================================================================
# 1) ARCHITECTURE
# =====================================================================
def architecture():
    s = Svg(1900, 1510)
    s.text(40, 46, "SmartPath · Milestone 2 architecture", size=26, weight=700)
    s.text(40, 72, "Everything runs locally with one command (docker compose up). Only Gemini (and later the shared bucket) is in the cloud.",
           size=14, fill=C["muted"])

    # Mac frame
    s.rect(20, 95, 1860, 1395, fill="#fbfbfd", stroke=C["frame"], rx=18)
    s.text(44, 126, "Your Mac", size=18, weight=700, fill=C["muted"])

    # Browser
    s.rect(50, 680, 240, 260, fill="#fff", stroke=C["page"], rx=14, sw=1.6)
    s.text(70, 714, "Browser", size=20, weight=700)
    s.lines(70, 744, ["localhost:3000", "", "1) loads the page", "    from the frontend", "", "2) the page's JS then", "    calls the API on :9000"], size=13, fill=C["muted"])

    # Docker VM
    s.rect(340, 140, 1070, 1330, fill="#ffffff", stroke=C["frame"], rx=16)
    s.text(362, 170, "Docker Desktop · Linux VM", size=17, weight=700, fill=C["muted"])
    s.rect(365, 185, 1020, 1270, fill="none", stroke=C["frame"], rx=12, dash=True, sw=1.2)
    s.text(1370, 206, "docker network: smartpath-network", size=12, fill=C["muted"], anchor="end")

    # ---------- api-service container ----------
    ax, ay, aw, ah = 390, 220, 975, 860
    s.rect(ax, ay, aw, ah, fill=C["api_band"], stroke=C["violet"], rx=14, sw=1.8)
    s.text(ax + 22, ay + 32, "api-service container", size=20, weight=700, fill=C["violet"])
    s.text(ax + 22, ay + 54, "image smartpath-api-service · python:3.12-slim · uv venv at /.venv (pinned by uv.lock)", size=12.5, fill=C["muted"])
    s.text(ax + 22, ay + 72, "volumes: ./api-service → /app (live code) · ../secrets → /secrets (read-only)", size=12.5, fill=C["muted"])

    # port 9000: Mac port → container port
    port_pair(s, 333, 9000, C["violet"], note_above=True)

    # row 1: entrypoint → uvicorn → app
    box(s, 430, 306, 220, 74, "docker-entrypoint.sh", ["activates /.venv", "starts uvicorn"], "route")
    box(s, 675, 306, 255, 74, "uvicorn", ["api.service:app", "--host 0.0.0.0 --port 9000"], "route")
    s.arrow([(650, 343), (673, 343)], C["route"])
    s.arrow([(930, 343), (968, 343)], C["route"])
    s.rect(970, 296, 375, 196, fill="#fff", stroke=C["route"], rx=10, sw=1.4)
    s.text(986, 320, "app = FastAPI()  ·  service.py", size=15, weight=650)
    s.text(986, 342, "CORSMiddleware: lets JS from :3000 call :9000", size=12, fill=C["muted"])
    s.text(986, 368, "route table", size=12.5, weight=650, fill=C["route"])
    s.lines(986, 390, ["GET  /              → get_index()",
                       "POST /preference    → post_preference()",
                       "POST /score         → post_score()",
                       "GET  /source[/path] → get_source*()"], size=12, mono=True, fill=C["ink"], gap=21)

    # row 2: routers
    box(s, 430, 522, 470, 72, "routers/score.py",
        ["post_score(): calls the modules in order", "agent → tiles → model_client → score_math"], "route")
    box(s, 930, 522, 415, 72, "routers/source.py",
        ["get_source_list() · get_source(path)", "read-only view of api/*.py (whitelist)"], "route")
    s.arrow([(1060, 492), (1060, 506), (665, 506), (665, 520)], C["route"])
    s.arrow([(1200, 492), (1200, 520)], C["route"])

    # row 3: harness
    s.rect(420, 628, 626, 108, fill="none", stroke=C["harness"], rx=12, dash=True, sw=1.2)
    s.text(1040, 622, "harness", size=12, weight=650, fill=C["harness"], anchor="end")
    box(s, 430, 640, 190, 86, "agent.py", ["get_preference()", "MAX_ATTEMPTS = 3"], "harness")
    box(s, 645, 640, 185, 86, "prompts.py", ["SYSTEM_INSTRUCTION", "build_user_prompt()"], "harness")
    box(s, 850, 640, 190, 86, "llm.py", ["ask() →", "generate_content()"], "harness")
    s.arrow([(620, 683), (643, 683)], C["harness"])
    s.arrow([(830, 683), (848, 683)], C["harness"])

    # row 4: tiles / model
    box(s, 430, 770, 190, 86, "tiles.py", ["get_tiles()", "FAKE_REGIONS"], "tiles", fake=True)
    box(s, 645, 770, 185, 86, "model_client.py", ["predict()", "real interface"], "model")
    box(s, 850, 770, 190, 86, "fake_model.py", ["fake_score_maps()", "loads .npz once"], "model", fake=True)
    box(s, 1060, 770, 285, 86, "data/fake/", ["charles_river_scores.npz", "made offline by scripts/", "fake_scores_from_image.py"], "model", fake=True)
    s.arrow([(830, 813), (848, 813)], C["model"])
    s.arrow([(1040, 813), (1058, 813)], C["model"])

    # row 5: score math
    box(s, 430, 890, 260, 86, "score_math.py", ["combine() · standardize()", "t = Z · w"], "score")

    # routers/score.py fans out to the modules (gray = called by the router)
    s.arrow([(500, 594), (500, 638)], C["route"])
    s.arrow([(450, 594), (450, 608), (410, 608), (410, 933), (428, 933)], C["route"])
    s.arrow([(410, 813), (428, 813)], C["route"])
    s.arrow([(632, 594), (632, 756), (737, 756), (737, 768)], C["route"])

    # shared modules
    s.rect(430, 1000, 915, 50, fill=C["shared_bg"], stroke=C["shared"], rx=10, sw=1.2)
    s.text(450, 1031, "schemas.py (pydantic data contracts)  ·  dimensions.py (the 20 scenery dimensions)  →  imported by every module",
           size=13.5, fill=C["ink"])

    # ---------- frontend container ----------
    fx, fy, fw, fh = 390, 1110, 975, 330
    s.rect(fx, fy, fw, fh, fill="#f3fbf8", stroke=C["page"], rx=14, sw=1.8)
    s.text(fx + 22, fy + 32, "frontend container", size=20, weight=700, fill=C["page"])
    s.text(fx + 22, fy + 54, "image smartpath-frontend · node:20-alpine (Dockerfile.dev) · volume ./frontend → /app", size=12.5, fill=C["muted"])

    # port 3000: Mac port → container port
    port_pair(s, fy + 103, 3000, C["page"], note_above=False)

    box(s, 430, fy + 80, 230, 66, "entrypoint", ["npm install && npm run dev"], "page")
    box(s, 690, fy + 80, 230, 66, "Next.js dev server", ["listens on :3000"], "page")
    s.arrow([(660, fy + 113), (688, fy + 113)], C["page"])
    s.lines(690, fy + 176, ["On the first visit it sends", "HTML + JS to the browser.", "From then on the code runs", "in the browser, not here."],
            size=12.5, fill=C["muted"])
    s.rect(950, fy + 66, 395, 220, fill="#fff", stroke=C["page"], rx=10, sw=1.4)
    s.text(966, fy + 90, "page code (frontend/src)", size=15, weight=650)
    s.lines(966, fy + 116, [
        "app/page.jsx · 3-column layout, submit()",
        "components/layout/Sidebar.jsx",
        "components/heatmap/Heatmap.jsx · Legend.jsx",
        "components/pipeline/Inspector.jsx",
        "services/DataService.js",
        "  Score() · Source() · Health() → :9000",
    ], size=12, mono=True, fill=C["ink"], gap=26)
    s.arrow([(920, fy + 113), (948, fy + 113)], C["page"])

    # browser arrows
    s.arrow([(230, 940), (230, fy + 103), (302, fy + 103)], C["page"], sw=2)
    s.text(222, 1080, "① GET page", size=13, weight=650, fill=C["page"], anchor="end")
    s.arrow([(230, 680), (230, 333), (302, 333)], C["violet"], sw=2)
    s.lines(222, 470, ["② page JS → API", "POST /score", "GET /source/…", "GET /  (health)"], size=13, fill=C["violet"], weight=600, anchor="end")

    # ---------- right column: cloud, teammates, secrets, legend ----------
    s.rect(1450, 220, 410, 400, fill="#f4f7ff", stroke=C["gemini"], rx=14, sw=1.6)
    s.text(1472, 252, "Google Cloud", size=20, weight=700, fill=C["gemini"])
    s.text(1472, 274, "project ac215-507902", size=12.5, fill=C["muted"])
    box(s, 1475, 300, 360, 92, "Vertex AI · Gemini", ["model gemini-3.1-flash-lite", "structured output = Preference", "called only by llm.ask()"], "gemini")
    s.rect(1475, 420, 360, 170, fill="#fff", stroke=C["shared"], rx=10, dash=True, sw=1.3)
    s.text(1491, 446, "GCS bucket (shared, team)", size=15, weight=650)
    s.lines(1491, 470, ["raw/  · labels/  · models/", "written by teammates' jobs", "", "not read by the app in M2"], size=12, mono=True, fill=C["muted"])
    # llm.py → Gemini: out of the right side of row 3, up the gap between VM and cloud
    s.arrow([(1040, 683), (1428, 683), (1428, 346), (1473, 346)], C["gemini"], sw=2)
    s.text(1240, 672, "HTTPS · generate_content()", size=12.5, weight=600, fill=C["gemini"], anchor="middle")

    s.rect(1450, 650, 410, 250, fill="#fff", stroke=C["shared"], rx=12, dash=True, sw=1.3)
    s.text(1472, 680, "Teammates' containers (jobs)", size=15, weight=650)
    s.lines(1472, 706, ["data-collector  → bucket raw/", "data-labeler    → bucket labels/", "model-training  → bucket models/", "",
                        "Later: a model service that", "replaces fake_model.py behind", "model_client.predict()"], size=12, mono=True, fill=C["muted"])
    s.arrow([(1655, 650), (1655, 593)], C["shared"], dash=True)

    s.rect(1450, 930, 410, 120, fill="#fff8f0", stroke=C["model"], rx=12, sw=1.4)
    s.text(1472, 960, "../secrets/llm-service-account.json", size=14, weight=650, mono=True)
    s.lines(1472, 984, ["outside the repo · never in git or an image", "mounted read-only into api-service", "GOOGLE_APPLICATION_CREDENTIALS"], size=12, fill=C["muted"])
    s.arrow([(1450, 990), (1368, 990)], C["model"], dash=True)

    s.rect(1450, 1090, 410, 240, fill="#fff", stroke=C["frame"], rx=12)
    s.text(1472, 1120, "Legend", size=15, weight=700)
    items = [("page", "frontend / browser"), ("route", "FastAPI app & routers (gray arrows = router calls)"),
             ("harness", "harness (agent, prompts, llm)"), ("tiles", "tiles"), ("model", "model (fake in M2)"),
             ("score", "score math"), ("gemini", "Google Cloud")]
    for i, (k, label) in enumerate(items):
        yy = 1146 + i * 24
        s.rect(1472, yy - 12, 16, 16, fill=C[k + "_bg"], stroke=C[k], rx=4, sw=1.4)
        s.text(1498, yy + 1, label, size=13)
    s.text(1472, 1322, "solid = runtime call · dashed = config / future · FAKE = M2 stand-in", size=12, fill=C["muted"])

    (OUT / f"smartpath-architecture{SUFFIX}.svg").write_text(s.render(), encoding="utf-8")


# =====================================================================
# 2) REQUEST FLOW (sequence diagram)
# =====================================================================
def request_flow():
    s = Svg(1940, 2420)
    s.text(40, 46, "SmartPath · what happens after you press Enter", size=26, weight=700)
    s.text(40, 72, 'Example prompt: "Ride along the river, shade if possible, avoid traffic"  ·  POST /score', size=14, fill=C["muted"])

    L = {  # lifelines: x, key, title, subtitle, container
        "page": (150, "page", "Page JS", "page.jsx · DataService.js", "runs in the browser"),
        "route": (440, "route", "FastAPI route", "service.py → routers/score.py", "api-service"),
        "harness": (720, "harness", "Harness", "agent.py · prompts.py · schemas.py", "api-service"),
        "tiles": (985, "tiles", "Tiles", "tiles.py", "api-service"),
        "model": (1235, "model", "Model", "model_client.py → fake_model.py", "api-service"),
        "score": (1490, "score", "Score math", "score_math.py", "api-service"),
        "gemini": (1790, "gemini", "Gemini", "Vertex AI · called by llm.py", "Google Cloud"),
    }
    top, bottom = 110, 2290

    # container bands
    s.rect(30, top, 240, bottom - top + 20, fill="#f3fbf8", rx=14)
    s.text(150, top + 22, "BROWSER (code from frontend :3000)", size=11.5, weight=700, fill=C["page"], anchor="middle")
    s.rect(305, top, 1310, bottom - top + 20, fill=C["api_band"], rx=14)
    s.text(960, top + 22, "api-service CONTAINER  ·  smartpath-api-service  ·  :9000", size=12, weight=700, fill=C["violet"], anchor="middle")
    s.rect(1650, top, 280, bottom - top + 20, fill="#f4f7ff", rx=14)
    s.text(1790, top + 22, "GOOGLE CLOUD", size=11.5, weight=700, fill=C["gemini"], anchor="middle")

    # headers + lifelines
    for x, k, title, sub, cont in L.values():
        s.rect(x - 116, top + 36, 232, 76, fill="#fff", stroke=C[k], rx=12, sw=1.6)
        s.text(x, top + 66, title, size=18, weight=700, anchor="middle")
        s.text(x, top + 90, sub, size=10.5, fill=C["muted"], anchor="middle", mono=True)
        s.add(f'<line x1="{x}" y1="{top + 112}" x2="{x}" y2="{bottom}" stroke="{C[k]}" stroke-width="1.4" stroke-dasharray="3 5" opacity="0.7"/>')

    X = {k: v[0] for k, v in L.items()}

    def msg(y, a, b, label, color, notes=(), ret=False, pill_x=None):
        x1, x2 = X[a], X[b]
        d = 1 if x2 > x1 else -1
        s.arrow([(x1 + d * 4, y), (x2 - d * 6, y)], C[color], dash=ret, sw=2)
        cx = pill_x or (x1 + x2) / 2
        s.pill(cx, y - 24, label, C[color], C[color + "_bg"], size=14)
        for i, n in enumerate(notes):
            s.text(cx, y + 20 + i * 17, n, size=12, mono=True, fill=C["muted"], anchor="middle")

    def note(x, y, w, lines_, color, title=None):
        h = 18 + 18 * len(lines_) + (22 if title else 0)
        s.rect(x - w / 2, y, w, h, fill="#fff", stroke=C[color], rx=10, sw=1.3)
        yy = y + 22
        if title:
            s.text(x - w / 2 + 12, yy, title, size=13, weight=700, fill=C[color])
            yy += 22
        for i, t in enumerate(lines_):
            s.text(x - w / 2 + 12, yy + i * 18, t, size=12, mono=True, fill=C["ink"])
        return y + h

    y = 300
    # ① page → route
    s.text(X["page"] - 70, y - 52, "you press Enter", size=12.5, fill=C["page"], anchor="middle", italic=True)
    msg(y, "page", "route", "① POST /score", "page",
        ["submit() → DataService.Score(text)", "axios · { text, region_id: 'demo' }"])
    # ② route self
    note(X["route"], y + 60, 250, ["uvicorn → app (service.py)", "→ CORS → route table", "→ post_score(request)", "", "pydantic checks ScoreRequest", "empty text → 422 error"], "route", "② request arrives")

    # ③ route → harness
    y = 560
    msg(y, "route", "harness", "③ get_preference(text)", "harness", ["agent.py"])

    # loop frame
    ly0, ly1 = 600, 1030
    s.rect(560, ly0, 1355, ly1 - ly0, fill="none", stroke=C["harness"], rx=12, dash=True, sw=1.4)
    s.rect(560, ly0, 250, 26, fill=C["harness"], rx=12)
    s.text(574, ly0 + 18, "loop · up to MAX_ATTEMPTS = 3", size=12.5, weight=700, fill="#fff")

    note(X["harness"], 640, 270, ["prompts.build_user_prompt(", "    text, error)", "error = None on the 1st try"], "harness", "④ build the prompt")

    y = 790
    msg(y, "harness", "gemini", "⑤ llm.ask(prompt, SYSTEM_INSTRUCTION, Preference)", "gemini",
        ["llm_client.models.generate_content(model='gemini-3.1-flash-lite',",
         "temperature=0, response_mime_type='application/json', response_schema=Preference)"], pill_x=1250)
    y = 880
    msg(y, "gemini", "harness", "⑥ raw JSON text", "gemini",
        ['{"weights": [{"dimension": "water", "reason": "…", "weight": 1.0}, …], "must_have": []}'], ret=True, pill_x=1250)
    note(X["harness"], 920, 300, ["Preference.model_validate_json(raw)", "✓ valid  → leave the loop", "✗ ValidationError → back to ④", "    with the error in the prompt"], "harness", "⑦ verify (pydantic)")
    s.text(1900, ly1 - 12, "all 3 fail → Preference() with all weights 0 (fallback)", size=12, fill=C["harness"], anchor="end", italic=True)

    # ⑧ harness → route
    y = 1090
    msg(y, "harness", "route", "⑧ AgentResult", "harness", ["preference · attempts · used_fallback"], ret=True)
    note(X["route"], 1140, 260, ["w = preference.as_vector()", "→ 20 weights, unmentioned = 0"], "route")

    # ⑨ tiles
    y = 1290
    msg(y, "route", "tiles", "⑨ tiles.get_tiles('demo')", "tiles", ["FAKE_REGIONS → fake_grid_tiles()"], pill_x=760)
    y = 1360
    msg(y, "tiles", "route", "10,000 Tile", "tiles", ["{tile_id, row, col}"], ret=True, pill_x=760)

    # ⑩ model
    y = 1460
    msg(y, "route", "model", "⑩ model_client.predict(tile_list)", "model", [], pill_x=900)
    note(X["model"], 1490, 300, ["fake_model.fake_score_maps(100, 100)", "loads data/fake/", "  charles_river_scores.npz (cached)", "→ 20 maps of 100 × 100"], "model", "FAKE in M2")
    y = 1640
    msg(y, "model", "route", "10,000 TileScore", "model", ["20 raw scores each (0–100)", "validated by schemas.TileScore"], ret=True, pill_x=900)

    # ⑪ score
    y = 1760
    msg(y, "route", "score", "⑪ score_math.combine(tile_scores, w, must_have)", "score", [], pill_x=1000)
    note(X["score"], 1790, 330, ["to_matrix()  → X (10000 × 20)", "standardize(): μ per dimension,", "  σ = max(std, PLACEHOLDER_RMSE=5)", "  Z = clip((X − μ) / σ, −3, 3)", "t = Z · w   (one total per tile)", "must_have gate: raw ≥ 10, else None"], "score", "the math")
    y = 1990
    msg(y, "score", "route", "heatmap 100 × 100 + ScoreStats", "score", ["t per tile · μ, σ per dimension · t range"], ret=True, pill_x=1000)

    # ⑫ response
    note(X["route"], 2030, 250, ["lap() timings per step", "→ ScoreResponse (schemas.py)"], "route")
    y = 2150
    msg(y, "route", "page", "⑫ JSON (~66 KB)", "page", ["agent · weights · heatmap", "stats · timings_ms"], ret=True)
    note(X["page"], 2195, 230, ["setResult(data)", "→ Heatmap: canvas, colorFor()", "→ Legend · Inspector tabs"], "page", "⑬ draw")

    # footer
    s.rect(30, 2330, 1880, 70, fill="#fafafc", stroke=C["frame"], rx=12)
    s.text(50, 2358, "Typical time:  ②–⑧ harness 1.4–2.0 s (Gemini)  ·  ⑨ tiles ~15 ms  ·  ⑩ model ~80 ms  ·  ⑪ score math ~12 ms",
           size=13.5, weight=600)
    s.text(50, 2382, "Other calls from the page:  Sidebar → GET / every 15 s → get_index() (service.py)  ·  Code tab → GET /source/{path} → get_source() (routers/source.py)",
           size=12.5, fill=C["muted"])

    (OUT / f"smartpath-request-flow{SUFFIX}.svg").write_text(s.render(), encoding="utf-8")


architecture()
request_flow()
print("ok")
