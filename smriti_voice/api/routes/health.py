"""Health and capability reporting.  Never returns a credential."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ...app import Application
from ...offline.health import build_health
from ..dependencies import application

router = APIRouter(tags=['health'])


@router.get('/v1/health')
def health(app: Application = Depends(application)) -> dict:
    return build_health(config=app.config, languages=app.languages, llm=app.llm,
                        tts=app.tts, offline=app.offline,
                        tool_count=len(app.registry.names()))
