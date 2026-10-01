"""冲突解决API路由"""
from fastapi import APIRouter, HTTPException, Query, Depends
from app.deps import require_api_key

from app.models import Conflict, ConflictStatus
from app.services import services

router = APIRouter(prefix="/api/conflicts", tags=["conflicts"])


@router.get("", response_model=list[Conflict])
async def list_conflicts(status: ConflictStatus | None = None):
    """列出冲突记录，可按状态过滤"""
    return await services.conflict_resolver.list_conflicts(status)


# ⚠️ 路由顺序要求：静态路径必须注册在动态路径 /{conflict_id} 之前，
# 否则 /api/conflicts/detect/opinion 会被 /{conflict_id} 匹配吞掉。
@router.post("/detect/opinion", dependencies=[Depends(require_api_key)])
async def detect_opinion_conflict(topic: str):
    """手动触发意见冲突检测"""
    conflict = await services.conflict_resolver.detect_opinion_conflict(topic)
    if conflict:
        return {"message": "Conflict detected", "conflict": conflict}
    return {"message": "No conflict detected", "conflict": None}


@router.get("/{conflict_id}", response_model=Conflict)
async def get_conflict(conflict_id: str):
    """获取冲突详情"""
    conflict = await services.conflict_resolver.get_conflict(conflict_id)
    if not conflict:
        raise HTTPException(status_code=404, detail="Conflict not found")
    return conflict


@router.post("/{conflict_id}/resolve", response_model=Conflict, dependencies=[Depends(require_api_key)])
async def resolve_conflict(
    conflict_id: str,
    strategy: str | None = None,
    resolved_by: str = "system",
):
    """
    解决冲突

    状态码：
    - 404 冲突不存在
    - 409 冲突已处于终态（RESOLVED / DISMISSED），不允许重复解决
    """
    try:
        return await services.conflict_resolver.resolve_conflict(
            conflict_id, strategy, resolved_by
        )
    except ValueError as e:
        message = str(e)
        if message.endswith("not found"):
            raise HTTPException(status_code=404, detail=message)
        raise HTTPException(status_code=409, detail=message)
