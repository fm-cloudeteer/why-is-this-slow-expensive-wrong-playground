"""
Feature flag store — in-memory, no external dependency.
The orchestrator calls POST /flags/{name} to flip flags.
The demo app reads flags synchronously on every request.
"""

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/flags")

_FLAGS: dict[str, bool] = {
    "prompt_regression_active": False,
    "expensive_tenant_active": False,
}


class FlagUpdate(BaseModel):
    value: bool


class FlagStore:
    def get(self, name: str) -> bool:
        return _FLAGS.get(name, False)


@router.get("/{name}")
async def get_flag(name: str):
    if name not in _FLAGS:
        return {"name": name, "value": False}
    return {"name": name, "value": _FLAGS[name]}


@router.post("/{name}")
async def set_flag(name: str, body: FlagUpdate):
    _FLAGS[name] = body.value
    return {"name": name, "value": _FLAGS[name]}


@router.get("")
async def list_flags():
    return _FLAGS
