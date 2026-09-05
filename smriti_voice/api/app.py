"""FastAPI application factory."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.requests import Request

from .. import __version__
from ..app import Application, get_application
from ..exceptions import SmritiError
from ..logging import configure, get_logger
from .routes import command, conversation, health, languages, tools, voice

log = get_logger('api')

DESCRIPTION = """\
SMRITI voice assistant for elderly care.

The deterministic command router and the safety layer have final authority over
every action; the language model can only propose. See SECURITY.md.
"""


def create_app(application: Application | None = None) -> FastAPI:
    configure()
    api = FastAPI(title='Smriti Voice API', version=__version__, description=DESCRIPTION)
    api.state.application = application or get_application()

    for module in (health, languages, conversation, voice, tools, command):
        api.include_router(module.router)

    @api.exception_handler(SmritiError)
    async def handle_smriti_error(request: Request, exc: SmritiError) -> JSONResponse:
        # Typed errors carry a stable code and never a provider message body.
        log.info('request_failed', fields={'path': request.url.path, 'code': exc.code})
        return JSONResponse(status_code=400, content={'error': exc.code, 'detail': str(exc)})

    @api.on_event('startup')
    async def warn_if_unauthenticated() -> None:
        config = api.state.application.config
        if config.allow_unauthenticated:
            log.warning('unauthenticated_access_enabled',
                        fields={'hint': 'SMRITI_ALLOW_UNAUTHENTICATED=1 is set. '
                                        'Never do this on a network-facing deployment.'})

    return api


app = create_app()
