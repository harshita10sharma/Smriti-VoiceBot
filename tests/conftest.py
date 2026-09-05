import sys
import time
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from smriti_voice.app import Application            # noqa: E402
from smriti_voice.config import AppConfig           # noqa: E402
from smriti_voice.database import Database          # noqa: E402
from smriti_voice.memory.seed import seed_demo_user  # noqa: E402
from smriti_voice.offline.manager import Connectivity  # noqa: E402
from smriti_voice.tools.weather import StaticWeatherProvider, WeatherReading  # noqa: E402


@pytest.fixture
def weather_provider():
    return StaticWeatherProvider(WeatherReading(
        location='Guwahati', temperature_c=29.4, condition='partly cloudy',
        humidity_percent=71.0, timestamp='2026-09-05T10:00', source='static'))


@pytest.fixture
def app(tmp_path, monkeypatch, weather_provider):
    """A fully wired Application on a throwaway in-memory database."""
    monkeypatch.setenv('SMRITI_AUDIO_CACHE_DIR', str(tmp_path / 'audio'))
    monkeypatch.setenv('SMRITI_TTS_PROVIDER', 'mock')
    monkeypatch.setenv('SMRITI_LLM_PROVIDER', 'mock')
    monkeypatch.delenv('SARVAM_API_KEY', raising=False)
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    application = Application.build(AppConfig.load(), database=Database(':memory:'),
                                    weather=weather_provider)
    # Tests must not depend on (or wait for) real network connectivity.
    application.offline._cached = Connectivity(True, time.time(), 'stubbed-for-tests')
    seed_demo_user(application.memory.repo, 'demo-user', today=date.today())
    return application


@pytest.fixture
def two_users(app):
    """A second user, so cross-user isolation can be tested for real."""
    from smriti_voice.memory.models import FamilyMember, Meal, Medicine, User
    repo = app.memory.repo
    repo.upsert_user(User(user_id='other-user', display_name='Lakshmi'))
    repo.add_family_member(FamilyMember(user_id='other-user', name='Priya', relation='daughter',
                                        phone='+919999999999', is_trusted_contact=True))
    repo.add_meal(Meal(user_id='other-user', meal_date=date.today(), meal_type='breakfast',
                       description='Upma'))
    repo.add_medicine(Medicine(user_id='other-user', name='Atorvastatin', dosage='10 mg',
                               schedule_time='21:00', time_of_day='night'))
    return app


@pytest.fixture
def client(app, monkeypatch):
    from fastapi.testclient import TestClient
    from smriti_voice.api.app import create_app
    monkeypatch.setenv('SMRITI_API_KEY', 'test-server-key')
    monkeypatch.setenv('SMRITI_AUTH_USER_ID', 'demo-user')
    return TestClient(create_app(app))


@pytest.fixture
def auth_headers():
    return {'x-api-key': 'test-server-key'}
