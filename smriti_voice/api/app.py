"""FastAPI application factory."""
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.requests import Request

from .. import __version__
from ..app import Application, get_application
from ..exceptions import SmritiError
from ..logging import configure, get_logger
from .routes import command, conversation, health, languages, memory_sync, tools, voice

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

    for module in (health, languages, conversation, voice, tools, command, memory_sync):
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

    @api.on_event('startup')
    async def warn_if_multi_worker_env_detected() -> None:
        # SessionStore, RateLimiter (api/dependencies.py) and the Indic
        # Parler-TTS model instance are all process-local. This deployment
        # is safe only with exactly one worker (Dockerfile hardcodes
        # `--workers 1`). A running process cannot reliably detect its
        # sibling workers, so this only catches the common env-var
        # conventions (WEB_CONCURRENCY, UVICORN_WORKERS) an operator might
        # set if they override the Dockerfile's launch command -- it is a
        # best-effort signal, not a guarantee. See SECURITY.md and
        # tools/validate_config.py, which runs the same check before deploy.
        for name in ('WEB_CONCURRENCY', 'UVICORN_WORKERS'):
            raw = (os.getenv(name) or '').strip()
            if not raw:
                continue
            try:
                count = int(raw)
            except ValueError:
                continue
            if count > 1:
                log.error('unsafe_multi_worker_configuration_detected',
                          fields={'env_var': name, 'value': count,
                                  'hint': 'SessionStore and RateLimiter are process-local; '
                                          'more than one worker silently breaks session and '
                                          'rate-limit isolation across patients. This '
                                          'deployment must run exactly one worker.'})

    return api


app = create_app()
