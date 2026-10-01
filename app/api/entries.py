"""黑板知识条目API路由"""
from fastapi import APIRouter, HTTPException, Query, Depends
from app.deps import require_api_key
from pydantic import BaseModel, Field

from app.models import KnowledgeEntry, EntryCreate, EntryUpdate, TopicText
from app.blackboard import VersionConflictError
from app.services import services

router = APIRouter(prefix="/api/entries", tags=["entries"])


class MergeRequest(BaseModel):
    """条目合并请求体"""

    source_ids: list[str] = Field(..., min_length=1)
    target_topic: TopicText


class BatchEntryCreate(BaseModel):
    """批量条目请求。

    上限防止单请求无界占用内存，500 条也足以覆盖报告中的批量导入场景。
    """

    entries: list[EntryCreate] = Field(..., min_length=1, max_length=500)


@router.post("", response_model=KnowledgeEntry, status_code=201, dependencies=[Depends(require_api_key)])
async def create_entry(entry_create: EntryCreate, author_id: str = Query(...)):
    """创建知识条目"""
    try:
        return await services.blackboard.create_entry(author_id, entry_create)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post(
    "/batch",
    response_model=list[KnowledgeEntry],
    status_code=201,
    dependencies=[Depends(require_api_key)],
)
async def create_entries_batch(
    request: BatchEntryCreate,
    author_id: str = Query(..., min_length=1, max_length=128),
):
    """在一次数据库提交中批量创建知识条目。"""
    try:
        return await services.blackboard.create_entries_batch(
            author_id, request.entries
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("", response_model=list[KnowledgeEntry])
async def list_entries(topic: str | None = None):
    """列出知识条目，可按主题过滤"""
    return await services.blackboard.list_entries(topic)


# ⚠️ 路由顺序要求：静态路径 /merge 必须注册在动态路径 /{entry_id} 之前。
# FastAPI 按注册顺序匹配，若 /{entry_id} 在前，请求 /api/entries/merge
# 会被当作 entry_id="merge" 的路径参数吞掉，导致该接口永远返回 422。
@router.post("/merge", response_model=KnowledgeEntry, status_code=201, dependencies=[Depends(require_api_key)])
async def merge_entries(req: MergeRequest, author_id: str = Query(...)):
    """合并多个知识条目"""
    try:
        return await services.blackboard.merge_entries(
            req.source_ids, req.target_topic, author_id
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{entry_id}", response_model=KnowledgeEntry)
async def get_entry(entry_id: str):
    """获取知识条目详情"""
    entry = await services.blackboard.get_entry(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    return entry


@router.put("/{entry_id}", response_model=KnowledgeEntry, dependencies=[Depends(require_api_key)])
async def update_entry(entry_id: str, update: EntryUpdate, author_id: str = Query(...)):
    """更新知识条目（乐观锁）"""
    try:
        return await services.blackboard.update_entry(entry_id, author_id, update)
    except VersionConflictError as e:
        raise HTTPException(
            status_code=409,
            detail=f"Version conflict: current version is {e.current_version}",
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/{entry_id}", dependencies=[Depends(require_api_key)])
async def delete_entry(entry_id: str, author_id: str = Query(...)):
    """删除知识条目"""
    try:
        success = await services.blackboard.delete_entry(entry_id, author_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    if not success:
        raise HTTPException(status_code=404, detail="Entry not found")
    return {"message": "Entry deleted", "entry_id": entry_id}
