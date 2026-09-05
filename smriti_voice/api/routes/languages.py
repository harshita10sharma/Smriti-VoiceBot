"""The language capability matrix."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ...app import Application
from ...exceptions import LanguageNotSupported
from ..dependencies import application

router = APIRouter(tags=['languages'])


@router.get('/v1/languages')
def languages(app: Application = Depends(application)) -> dict:
    return {
        'count': len(app.languages.all()),
        'summary': app.languages.summary(),
        'note': 'A language is reported SUPPORTED only after a measured validation run. '
                'See LANGUAGE_SUPPORT.md.',
        'languages': [capability.model_dump(mode='json') for capability in app.languages.all()],
    }


@router.get('/v1/languages/{code}')
def language(code: str, app: Application = Depends(application)) -> dict:
    try:
        return app.languages.get(code).model_dump(mode='json')
    except LanguageNotSupported as exc:
        raise HTTPException(404, str(exc)) from exc
