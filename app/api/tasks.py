"""任务协调API路由"""
from fastapi import APIRouter, HTTPException, Query, Depends
from app.deps import require_api_key

from app.models import Task, TaskCreate, TaskUpdate, TaskStatus
from app.task_coordinator import CircularDependencyError
from app.services import services

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.post("", response_model=Task, status_code=201, dependencies=[Depends(require_api_key)])
async def create_task(task_create: TaskCreate):
    """
    创建任务

    状态码：
    - 400 参数非法（含循环依赖）
    - 404 指定的创建者或依赖任务不存在
    """
    try:
        return await services.task_coordinator.create_task(task_create)
    except CircularDependencyError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        message = str(e)
        if "not found" in message:
            raise HTTPException(status_code=404, detail=message)
        raise HTTPException(status_code=400, detail=message)


@router.get("", response_model=list[Task])
async def list_tasks(status: TaskStatus | None = None):
    """列出任务，可按状态过滤"""
    return await services.task_coordinator.list_tasks(status)


@router.get("/{task_id}", response_model=Task)
async def get_task(task_id: str):
    """获取任务详情"""
    task = await services.task_coordinator.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.post("/{task_id}/assign", response_model=Task, dependencies=[Depends(require_api_key)])
async def assign_task(task_id: str, assignee_id: str):
    """手动分配任务"""
    try:
        return await services.task_coordinator.assign_task(task_id, assignee_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{task_id}/auto-assign", response_model=Task, dependencies=[Depends(require_api_key)])
async def auto_assign_task(task_id: str):
    """自动分配任务"""
    try:
        task = await services.task_coordinator.auto_assign(task_id)
        if not task:
            raise HTTPException(
                status_code=409,
                detail="No available agent or dependencies not ready",
            )
        return task
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch("/{task_id}", response_model=Task, dependencies=[Depends(require_api_key)])
async def update_task(task_id: str, update: TaskUpdate, updater_id: str = Query(...)):
    """更新任务状态与字段"""
    try:
        return await services.task_coordinator.update_task(task_id, updater_id, update)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
