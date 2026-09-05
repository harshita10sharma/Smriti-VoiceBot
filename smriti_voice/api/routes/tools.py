"""Introspection of the tool registry.

Sensitive tools are listed here for operator visibility, clearly marked, but they
are never advertised to a model and can never execute.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ...app import Application
from ...schemas import SafetyLevel
from ..dependencies import application, require_api_key

router = APIRouter(tags=['tools'], dependencies=[Depends(require_api_key)])


@router.get('/v1/tools')
def tools(app: Application = Depends(application)) -> dict:
    return {
        'count': len(app.registry.names()),
        'advertised_to_model': [spec.name for spec in app.registry.specs()],
        'tools': [{
            'name': tool.name,
            'description': tool.description,
            'safety_level': tool.safety_level.value,
            'permission': tool.permission.value,
            'requires_confirmation': tool.requires_confirmation,
            'executable_by_voice': tool.safety_level is not SafetyLevel.SENSITIVE,
            'parameters': tool.json_schema(),
        } for tool in app.registry.all()],
    }
