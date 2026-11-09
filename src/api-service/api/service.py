# FastAPI 大门：建 app、开 CORS、挂上各组接口。uvicorn 启动的就是这里的 app。
# uvicorn的找app对象请求会直接运行这里整个python文件
from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from api.routers import score, source

# 建 app（/docs 页面上显示的标题和说明）
app = FastAPI(title="SmartPath API", description="Scenery preference parsing + tile scoring", version="v1")

# CORS：允许别的端口上的网页（前端 localhost:3000）在浏览器里调这个 API
app.add_middleware(
    CORSMiddleware,
    allow_credentials=False,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


#健康检查：打开 localhost:9000/ 能看到这句话，说明服务活着
@app.get("/")
def get_index():
    return {"message": "Welcome to the SmartPath API Service"}


#挂上打分相关的接口（/preference、/score）
app.include_router(score.router)

#挂上代码展示接口（/source），给页面展示后端代码用
app.include_router(source.router)
