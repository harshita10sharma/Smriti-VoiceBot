from __future__ import annotations
import json, os
from dotenv import load_dotenv

from .exceptions import ConfigurationError

load_dotenv(override=False)
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

@dataclass(frozen=True)
class Settings:
    sample_rate:int; channels:int; record_seconds:float; min_speech_seconds:float; max_speech_seconds:float
    default_language:str; language_pack_dir:Path; intent_threshold:float; ambiguity_margin:float
    max_intent_candidates:int; audio_dir:Path; recordings_dir:Path; telemetry_enabled:bool; telemetry_path:Path
    retain_raw_audio:bool; api_key_env:str; max_upload_bytes:int; max_request_seconds:float
    @classmethod
    def load(cls, path:Path|None=None):
        p=path or ROOT/'config/settings.json'; d=json.loads(p.read_text(encoding='utf-8'))
        def rp(x): return ROOT/x
        return cls(d['sample_rate'],d['channels'],d['record_seconds'],d['min_speech_seconds'],d['max_speech_seconds'],
                   d['default_language'],rp(d['language_pack_dir']),d['intent_threshold'],d['ambiguity_margin'],
                   d['max_intent_candidates'],rp(d['audio_dir']),rp(d['recordings_dir']),d['telemetry_enabled'],
                   rp(d['telemetry_path']),d['retain_raw_audio'],d['api_key_env'],d['max_upload_bytes'],d['max_request_seconds'])

@dataclass(frozen=True)
class LanguagePack:
    code:str; name:str; script:str; status:str; providers:tuple[str,...]; commands_path:Path; model_id:str|None

class LanguageRegistry:
    def __init__(self, directory:Path):
        self.directory=directory; raw=json.loads((directory/'ner_languages.json').read_text(encoding='utf-8'))
        self._packs={x['code']:LanguagePack(x['code'],x['name'],x.get('script',''),x['status'],tuple(x.get('providers',[])),directory/f"commands_{x['code']}.json",x.get('model_id')) for x in raw}
    def get(self, code:str)->LanguagePack:
        if code not in self._packs: raise KeyError(code)
        return self._packs[code]
    def all(self): return tuple(self._packs.values())


# --------------------------------------------------------------------------- #
# v5 configuration.  Everything below is environment-driven; no secret ever
# appears in a file that is committed.  `Settings` above is unchanged v4.1.
# --------------------------------------------------------------------------- #
def _env_str(name: str, default: str = '') -> str:
    return (os.getenv(name) or default).strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name) or default)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name) or default)
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or '').strip().lower()
    if not raw:
        return default
    return raw in {'1', 'true', 'yes', 'on'}


@dataclass(frozen=True)
class ProviderConfig:
    """Provider selection and credentials.  Keys are read, never written out."""
    llm_provider: str = 'auto'
    llm_model_gemini: str = 'gemini-3.7-flash'
    llm_model_openai: str = 'gpt-4o-mini'
    llm_model_sarvam: str = 'sarvam-105b'
    llm_model_groq: str = 'qwen/qwen3.8-27b'
    llm_model_local: str = ''
    tts_provider: str = 'auto'
    tts_model: str = 'bulbul:v2'
    asr_provider: str = 'auto'
    weather_provider: str = 'open-meteo'
    request_timeout_s: float = 20.0
    llm_timeout_s: float = 30.0
    tts_timeout_s: float = 30.0
    max_retries: int = 2
    retry_backoff_s: float = 0.5

    # Indic Parler-TTS configuration
    indic_parler_enabled: bool = False
    indic_parler_model: str = 'ai4bharat/indic-parler-tts'
    indic_parler_device: str = 'auto'
    indic_parler_languages: str = 'asm,brx,mni,npi'
    indic_parler_description: str = 'a calm, neutral Indian voice'

    @property
    def groq_key(self) -> str:
        return _env_str('GROQ_API_KEY')

    @property
    def sarvam_key(self) -> str:
        return _env_str('SARVAM_API_KEY')

    @property
    def gemini_key(self) -> str:
        return _env_str('GEMINI_API_KEY')

    @property
    def openai_key(self) -> str:
        return _env_str('OPENAI_API_KEY')

    @classmethod
    def from_env(cls) -> 'ProviderConfig':
        return cls(
            llm_provider=_env_str('SMRITI_LLM_PROVIDER', 'auto'),
            llm_model_gemini=_env_str('GEMINI_MODEL', 'gemini-3.7-flash'),
            llm_model_openai=_env_str('OPENAI_MODEL', 'gpt-4o-mini'),
            llm_model_sarvam=_env_str('SARVAM_CHAT_MODEL', 'sarvam-105b'),
            llm_model_groq=_env_str('GROQ_MODEL', 'qwen/qwen3.8-27b'),
            llm_model_local=_env_str('SMRITI_LOCAL_LLM_PATH', ''),
            tts_provider=_env_str('SMRITI_TTS_PROVIDER', 'auto'),
            tts_model=_env_str('SARVAM_TTS_MODEL', 'bulbul:v2'),
            asr_provider=_env_str('SMRITI_ASR_PROVIDER', 'auto'),
            weather_provider=_env_str('SMRITI_WEATHER_PROVIDER', 'open-meteo'),
            request_timeout_s=_env_float('SMRITI_REQUEST_TIMEOUT_S', 20.0),
            llm_timeout_s=_env_float('SMRITI_LLM_TIMEOUT_S', 30.0),
            tts_timeout_s=_env_float('SMRITI_TTS_TIMEOUT_S', 30.0),
            max_retries=_env_int('SMRITI_MAX_RETRIES', 2),
            retry_backoff_s=_env_float('SMRITI_RETRY_BACKOFF_S', 0.5),
            # Indic Parler-TTS configuration
            indic_parler_enabled=_env_bool('SMRITI_INDIC_PARLER_ENABLED', False),
            indic_parler_model=_env_str('SMRITI_INDIC_PARLER_MODEL', 'ai4bharat/indic-parler-tts'),
            indic_parler_device=_env_str('SMRITI_INDIC_PARLER_DEVICE', 'auto'),
            indic_parler_languages=_env_str('SMRITI_INDIC_PARLER_LANGUAGES', 'asm,brx,mni,npi'),
            indic_parler_description=_env_str('SMRITI_INDIC_PARLER_DESCRIPTION', 'a calm, neutral Indian voice'),
        )

    def configured_providers(self) -> dict[str, bool]:
        """Booleans only â€” this feeds /v1/health and must never leak a key."""
        return {
            'sarvam': bool(self.sarvam_key),
            'gemini': bool(self.gemini_key),
            'openai': bool(self.openai_key),
            'groq': bool(self.groq_key),
        }


@dataclass(frozen=True)
class AppConfig:
    """Top-level runtime configuration for the conversational subsystem."""
    settings: Settings
    providers: ProviderConfig
    database_url: str
    audio_cache_dir: Path
    audio_retention_minutes: int
    max_history_turns: int
    max_session_idle_minutes: int
    default_location: str
    default_latitude: float
    default_longitude: float
    offline_forced: bool
    environment: str
    allow_unauthenticated: bool
    api_key_env: str
    rate_limit_per_minute: int
    max_upload_bytes: int
    voice_job_queue_max: int
    idempotency_ttl_hours: int
    version: str

    @classmethod
    def load(cls, settings: Settings | None = None) -> 'AppConfig':
        s = settings or Settings.load()
        from . import __version__

        # Fail closed by default: an unset/misspelled SMRITI_ENV is treated
        # as production, never as an implicit opt-in to relaxed behaviour.
        environment = _env_str('SMRITI_ENV', 'production').lower() or 'production'
        allow_unauthenticated = _env_bool('SMRITI_ALLOW_UNAUTHENTICATED', False)

        # SMRITI_ALLOW_UNAUTHENTICATED is a real foot-gun (SECURITY.md): with
        # no server key configured, it serves every protected route as
        # 'anonymous' with no patient binding at all. It exists for local
        # development only. Rather than let a single stray environment
        # variable silently open a network-facing deployment, refuse to
        # start at all unless the operator has also explicitly declared
        # this a development environment -- two separate, deliberate
        # opt-ins, not one. This never fires for the existing single-user
        # deployment, which authenticates normally and never sets this flag.
        if allow_unauthenticated and environment != 'development':
            raise ConfigurationError(
                'SMRITI_ALLOW_UNAUTHENTICATED=1 is set but SMRITI_ENV is '
                f'{environment!r}, not "development". Refusing to start: this '
                'combination would serve every protected route unauthenticated '
                'on what is being treated as a network-facing deployment. Set '
                'SMRITI_ENV=development to run unauthenticated locally, or '
                'unset SMRITI_ALLOW_UNAUTHENTICATED.',
                code='UNSAFE_AUTH_CONFIGURATION')

        return cls(
            settings=s,
            providers=ProviderConfig.from_env(),
            database_url=_env_str('SMRITI_DB_PATH', str(ROOT / 'runtime' / 'smriti.db')),
            audio_cache_dir=Path(_env_str('SMRITI_AUDIO_CACHE_DIR', str(ROOT / 'runtime' / 'audio'))),
            audio_retention_minutes=_env_int('SMRITI_AUDIO_RETENTION_MINUTES', 15),
            max_history_turns=_env_int('SMRITI_MAX_HISTORY_TURNS', 8),
            max_session_idle_minutes=_env_int('SMRITI_SESSION_IDLE_MINUTES', 30),
            default_location=_env_str('SMRITI_DEFAULT_LOCATION', 'Guwahati'),
            default_latitude=_env_float('SMRITI_DEFAULT_LATITUDE', 26.1445),
            default_longitude=_env_float('SMRITI_DEFAULT_LONGITUDE', 91.7362),
            offline_forced=_env_bool('SMRITI_FORCE_OFFLINE', False),
            environment=environment,
            allow_unauthenticated=allow_unauthenticated,
            api_key_env=s.api_key_env,
            rate_limit_per_minute=_env_int('SMRITI_RATE_LIMIT_PER_MINUTE', 60),
            max_upload_bytes=s.max_upload_bytes,
            voice_job_queue_max=_env_int('SMRITI_VOICE_JOB_QUEUE_MAX', 200),
            idempotency_ttl_hours=_env_int('SMRITI_IDEMPOTENCY_TTL_HOURS', 24),
            version=__version__,
        )

