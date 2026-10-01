"""API 依赖 - 鉴权等横切关注点"""
from __future__ import annotations

import os
import secrets

from fastapi import Header, HTTPException

# 通过环境变量 BLACKBOARD_API_KEY 配置。
# 未配置时（默认空串）不启用鉴权，保持向后兼容与本地开发便利；
# 生产部署务必设置该变量。
API_KEY = os.getenv("BLACKBOARD_API_KEY", "")


def api_key_enabled() -> bool:
    """鉴权是否已启用"""
    return bool(API_KEY)


async def require_api_key(x_api_key: str = Header(default="")) -> None:
    """
    校验请求头 X-API-Key

    - 未配置 BLACKBOARD_API_KEY：直接放行（开发模式）
    - 已配置：使用常量时间比较，不匹配返回 401
    """
    if not API_KEY:
        return
    # secrets.compare_digest 防止时序侧信道
    if not secrets.compare_digest(x_api_key, API_KEY):
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "X-API-Key"},
        )
