"""Composition root.

One object owns every subsystem, so the API, the CLI and the tests all build the
same wiring instead of each assembling their own.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from .asr.router import ASRRouter
from .config import AppConfig
from .conversation.context import SessionStore
from .conversation.manager import ConversationManager
from .database import Database
from .idempotency import IdempotencyStore
from .language.detector import LanguageDetector
from .language.registry import LanguageService
from .llm.router import LLMRouter
from .memory.repository import MemoryRepository
from .memory.service import MemoryService
from .offline.manager import OfflineManager
from .safety.policy import get_policy
from .telemetry import Telemetry
from .tools.builtin import build_default_registry
from .tools.weather import OpenMeteoWeatherProvider
from .tts.router import TTSRouter
from .voice_jobs import VoiceJobRepository, VoiceJobWorker


@dataclass
class Application:
    config: AppConfig
    languages: LanguageService
    memory: MemoryService
    registry: Any
    llm: LLMRouter
    tts: TTSRouter
    asr: ASRRouter
    detector: LanguageDetector
    offline: OfflineManager
    conversation: ConversationManager
    telemetry: Telemetry
    voice_jobs: VoiceJobRepository
    idempotency: IdempotencyStore
    voice_job_worker: VoiceJobWorker | None = None

    @classmethod
    def build(cls, config: AppConfig | None = None, *, database: Database | None = None,
              weather: Any = None) -> 'Application':
        config = config or AppConfig.load()
        policy = get_policy()
        languages = LanguageService(config.settings.language_pack_dir)
        repository = MemoryRepository(database or Database(config.database_url))
        memory = MemoryService(repository)
        registry = build_default_registry(policy)
        llm = LLMRouter(config)
        tts = TTSRouter(config, languages)
        asr = ASRRouter(config, languages.packs)
        offline = OfflineManager(config)
        telemetry = Telemetry(config.settings.telemetry_enabled,
                              config.settings.telemetry_path,
                              config.settings.retain_raw_audio)
        conversation = ConversationManager(
            config, memory=memory, registry=registry, llm=llm, languages=languages,
            policy=policy, weather=weather if weather is not None else OpenMeteoWeatherProvider(),
            offline_manager=offline, telemetry=telemetry,
            sessions=SessionStore(max_turns=config.max_history_turns,
                                  idle_timeout_minutes=config.max_session_idle_minutes))
        # Shares the same database/migration as MemoryRepository above.
        voice_jobs = VoiceJobRepository(repository.db)
        # Any job still queued/processing at this exact moment belongs to a
        # previous process that crashed or was restarted -- nothing has been
        # submitted to this process's worker yet, so it cannot be a job
        # actually in flight. See VoiceJobRepository.recover_stale_jobs.
        voice_jobs.recover_stale_jobs()
        idempotency = IdempotencyStore(repository.db, ttl_hours=config.idempotency_ttl_hours)
        application = cls(config=config, languages=languages, memory=memory, registry=registry,
                          llm=llm, tts=tts, asr=asr,
                          detector=LanguageDetector(languages), offline=offline,
                          conversation=conversation, telemetry=telemetry,
                          voice_jobs=voice_jobs, idempotency=idempotency)
        application.voice_job_worker = VoiceJobWorker(application, voice_jobs,
                                                       queue_max=config.voice_job_queue_max)
        return application


@lru_cache(maxsize=1)
def get_application() -> Application:
    return Application.build()
