"""协作规则API路由"""
from typing import Any

from fastapi import APIRouter, HTTPException, Depends
from app.deps import require_api_key
from pydantic import BaseModel, ConfigDict

from app.models import CollaborationRule, RuleCreate
from app.services import services

router = APIRouter(prefix="/api/rules", tags=["rules"])


class RuleUpdate(BaseModel):
    """
    规则更新请求体 - 显式声明可更新字段，禁止主键/枚举骨架被改写

    extra="forbid" 让未声明字段（如 rule_id / trigger / action）直接返回 422，
    而不是被静默丢弃 —— 让调用方明确知道哪些字段不可改。
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    condition: dict[str, Any] | None = None
    action_params: dict[str, Any] | None = None
    enabled: bool | None = None
    priority: int | None = None


@router.post("", response_model=CollaborationRule, status_code=201, dependencies=[Depends(require_api_key)])
async def create_rule(rule_create: RuleCreate):
    """创建协作规则"""
    return await services.rule_engine.create_rule(rule_create)


@router.get("", response_model=list[CollaborationRule])
async def list_rules():
    """列出所有协作规则"""
    return await services.rule_engine.list_rules()


@router.get("/{rule_id}", response_model=CollaborationRule)
async def get_rule(rule_id: str):
    """获取规则详情"""
    rule = await services.rule_engine.get_rule(rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    return rule


@router.patch("/{rule_id}", response_model=CollaborationRule, dependencies=[Depends(require_api_key)])
async def update_rule(rule_id: str, updates: RuleUpdate):
    """更新规则（仅允许白名单字段，非法字段返回 400）"""
    try:
        # exclude_unset=True：只提交客户端显式传入的字段，
        # 避免未提供的字段被 None 覆盖。
        payload = updates.model_dump(exclude_unset=True)
        return await services.rule_engine.update_rule(rule_id, payload)
    except ValueError as e:
        message = str(e)
        # 规则不存在 -> 404，其余（字段非法/类型错误）-> 400
        if message.endswith("not found"):
            raise HTTPException(status_code=404, detail=message)
        raise HTTPException(status_code=400, detail=message)


@router.delete("/{rule_id}", dependencies=[Depends(require_api_key)])
async def delete_rule(rule_id: str):
    """删除规则"""
    success = await services.rule_engine.delete_rule(rule_id)
    if not success:
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"message": "Rule deleted", "rule_id": rule_id}
