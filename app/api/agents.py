"""智能体管理API路由"""
from fastapi import APIRouter, HTTPException, Depends
from app.deps import require_api_key

from app.models import Agent, AgentCreate, AgentStatus
from app.services import services

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.post("", response_model=Agent, status_code=201, dependencies=[Depends(require_api_key)])
async def register_agent(agent_create: AgentCreate):
    """注册新智能体"""
    return await services.agent_registry.register(agent_create)


@router.get("", response_model=list[Agent])
async def list_agents():
    """列出所有注册的智能体"""
    return await services.agent_registry.list_all()


@router.get("/{agent_id}", response_model=Agent)
async def get_agent(agent_id: str):
    """获取智能体详情"""
    agent = await services.agent_registry.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@router.delete("/{agent_id}", dependencies=[Depends(require_api_key)])
async def unregister_agent(agent_id: str):
    """注销智能体"""
    success = await services.agent_registry.unregister(agent_id)
    if not success:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"message": "Agent unregistered", "agent_id": agent_id}


@router.post("/{agent_id}/heartbeat", response_model=Agent, dependencies=[Depends(require_api_key)])
async def heartbeat(agent_id: str):
    """发送心跳"""
    agent = await services.agent_registry.heartbeat(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@router.patch("/{agent_id}/status", response_model=Agent, dependencies=[Depends(require_api_key)])
async def update_status(agent_id: str, status: AgentStatus):
    """更新智能体状态"""
    agent = await services.agent_registry.update_status(agent_id, status)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@router.get("/{agent_id}/tasks")
async def get_agent_tasks(agent_id: str):
    """获取智能体当前分配的任务"""
    tasks = await services.task_coordinator.get_task_assignments(agent_id)
    return {"agent_id": agent_id, "tasks": tasks, "count": len(tasks)}
