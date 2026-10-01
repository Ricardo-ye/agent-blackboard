"""智能体动态协作黑板系统 - 主入口"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.deps import require_api_key, api_key_enabled
from app.services import services
from app.api import agents, entries, tasks, rules, conflicts, websocket
from app.models import Stats
from app.event_bus import event_bus

# 应用日志接入 uvicorn 的日志配置，保证与 uvicorn 的 INFO 行同通道输出
# （不配置 handler 时 root logger 默认 WARNING，info 级启动信息会丢失）
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(levelname)s:     %(message)s",
)
logger = logging.getLogger("blackboard")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    await services.initialize()
    # 使用 logging 而非 print：print 走行缓冲的 stdout，与 uvicorn 的 stderr 日志
    # 不同步，重定向到文件时顺序错乱、由管道截断时还会丢失。
    logger.info("Services initialized successfully")
    if api_key_enabled():
        logger.info("API key auth: ENABLED")
    else:
        logger.warning("API key auth: DISABLED (dev mode) - set BLACKBOARD_API_KEY for production")
    yield
    await services.shutdown()
    logger.info("Services shutdown")


app = FastAPI(
    title="智能体动态协作黑板系统",
    description="基于黑板模式的多智能体协作系统，支持智能体注册、信息共享、任务分配、冲突解决与动态规则配置",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS配置
# 注意：allow_origins=["*"] 与 allow_credentials=True 是非法组合（浏览器会拒绝），
# 因此按是否配置 CORS_ORIGINS 环境变量二选一。
_cors_origins_raw = os.getenv("CORS_ORIGINS", "").strip()
if _cors_origins_raw:
    _allowed_origins = [o.strip() for o in _cors_origins_raw.split(",") if o.strip()]
    _allow_credentials = True
else:
    _allowed_origins = ["*"]
    _allow_credentials = False

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
# 鉴权粒度：写操作（POST/PUT/PATCH/DELETE）在各自路由上挂 require_api_key，
# 读操作（GET）保持开放。未配置 BLACKBOARD_API_KEY 时全部放行。
app.include_router(agents.router)
app.include_router(entries.router)
app.include_router(tasks.router)
app.include_router(rules.router)
app.include_router(conflicts.router)
app.include_router(websocket.router)


@app.get("/", tags=["system"])
async def root():
    """系统根端点"""
    return {
        "name": "智能体动态协作黑板系统",
        "version": "1.0.0",
        "status": "running",
        "docs": "/docs",
    }


@app.get("/api/stats", response_model=Stats, tags=["system"])
async def get_stats():
    """获取系统统计信息"""
    return await services.storage.get_stats()


@app.get(
    "/metrics",
    response_class=PlainTextResponse,
    tags=["system"],
    include_in_schema=False,
)
async def metrics() -> str:
    """输出 Prometheus text exposition 指标，不引入额外运行时依赖。"""
    stats = await services.storage.get_stats()
    gauges = {
        "blackboard_agents_total": stats.total_agents,
        "blackboard_agents_online": stats.online_agents,
        "blackboard_entries_total": stats.total_entries,
        "blackboard_tasks_total": stats.total_tasks,
        "blackboard_tasks_pending": stats.pending_tasks,
        "blackboard_tasks_completed": stats.completed_tasks,
        "blackboard_conflicts_total": stats.total_conflicts,
        "blackboard_conflicts_resolved": stats.resolved_conflicts,
        "blackboard_rules_total": stats.total_rules,
    }
    lines = []
    for name, value in gauges.items():
        lines.extend((f"# TYPE {name} gauge", f"{name} {value}"))
    lines.extend((
        "# TYPE blackboard_event_bus_dropped_events_total counter",
        f"blackboard_event_bus_dropped_events_total {event_bus.dropped_events}",
    ))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# 静态控制台（/ui）
# 纯前端资源，挂在独立前缀下，与 /api 完全隔离：
#   - 不影响任何既有 REST / WebSocket 路由的匹配顺序
#   - 不改动 / 根端点的响应，后端行为保持原样
# 目录不存在时静默跳过，保证纯 API 部署也能正常启动。
# ---------------------------------------------------------------------------
WEB_DIR = Path(__file__).resolve().parent / "web"

if WEB_DIR.is_dir():

    @app.get("/ui", include_in_schema=False)
    async def ui_redirect():
        """跳转到控制台首页（补上结尾斜杠，便于静态目录索引）"""
        return RedirectResponse(url="/ui/")

    app.mount("/ui", StaticFiles(directory=str(WEB_DIR), html=True), name="ui")


if __name__ == "__main__":
    import uvicorn
    from config import HOST, PORT
    uvicorn.run("main:app", host=HOST, port=PORT, reload=True)
