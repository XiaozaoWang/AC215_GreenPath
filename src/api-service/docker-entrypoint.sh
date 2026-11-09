#!/bin/bash

echo "Container is running!!!"

# 激活 uv 的虚拟环境（build 镜像时装在 /.venv）
#tutorial里面没有是因为当时使用的不是uv，但我看ml2要求uv，这里就需要激活
source /.venv/bin/activate

#在entrypoint末尾写了分叉，如果DEV!=1则直接uvicorn_server_production
#这里是不是production的区别为 开发版修改文件后会自动重启服务器，日志打印的很细，上线版有lifespan，强制运行 startup/shutdown 事件，不支持就直接报错
uvicorn_server() {
    uvicorn api.service:app --host 0.0.0.0 --port 9000 --log-level debug --reload --reload-dir api/ "$@"
    #uvicorn api.service:app --host 0.0.0.0 --port 9000 --log-level debug
}
uvicorn_server_production() {
    uvicorn api.service:app --host 0.0.0.0 --port 9000 --lifespan on
}


export -f uvicorn_server
export -f uvicorn_server_production

echo -en "\033[92m
The following commands are available:
    uvicorn_server
        Run the Uvicorn Server
\033[0m
"

if [ "${DEV}" = 1 ]; then
  exec /bin/bash
else
  uvicorn_server_production
fi
