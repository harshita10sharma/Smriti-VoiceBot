"""Text conversation endpoint.

Exists so the whole conversational stack — safety, commands, memory, tools,
language following — can be tested without audio.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ...app import Application
from ...schemas import ConversationRequest, ConversationResponse
from ..dependencies import application, authenticated_user_id, request_id, require_api_key

router = APIRouter(tags=['conversation'], dependencies=[Depends(require_api_key)])


@router.post('/v1/conversation', response_model=ConversationResponse)
def conversation(payload: ConversationRequest,
                 app: Application = Depends(application),
                 rid: str = Depends(request_id),
                 authenticated_id: str = Depends(authenticated_user_id)) -> ConversationResponse:
    if payload.user_id != authenticated_id:
        raise HTTPException(403, 'user_id does not match the authenticated user')
    language = payload.language
    if language:
        if not app.languages.is_known(language):
            raise HTTPException(400, f'Unknown language: {language!r}')
    else:
        language = app.detector.detect(payload.message).language

    try:
        return app.conversation.handle(user_id=payload.user_id, message=payload.message,
                                       session_id=payload.session_id, language=language,
                                       request_id=rid)
    except PermissionError as exc:
        # A session id belonging to a different user.
        raise HTTPException(403, 'This session does not belong to this user') from exc
