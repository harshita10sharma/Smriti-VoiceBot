"""Weather as a real tool, not a guess.

Open-Meteo is used because it needs no API key, which keeps the demo path honest
and keyless.  A ``WeatherProvider`` protocol is defined so a paid provider can be
swapped in.

Offline (or when the call fails) a cached reading is returned **only if it is
explicitly marked stale**, with its age in minutes, so the assistant can say
"this is from two hours ago" instead of implying it is current.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import httpx

from ..exceptions import WeatherError
from ..logging import get_logger

log = get_logger('tools.weather')

OPEN_METEO_URL = 'https://api.open-meteo.com/v1/forecast'
GEOCODE_URL = 'https://geocoding-api.open-meteo.com/v1/search'

# WMO weather interpretation codes, in words an elderly user would use.
WEATHER_CODES: dict[int, str] = {
    0: 'clear sky', 1: 'mainly clear', 2: 'partly cloudy', 3: 'overcast',
    45: 'foggy', 48: 'foggy', 51: 'light drizzle', 53: 'drizzle', 55: 'heavy drizzle',
    61: 'light rain', 63: 'rain', 65: 'heavy rain', 66: 'freezing rain', 67: 'freezing rain',
    71: 'light snow', 73: 'snow', 75: 'heavy snow', 80: 'rain showers', 81: 'rain showers',
    82: 'heavy rain showers', 95: 'thunderstorm', 96: 'thunderstorm with hail',
    99: 'thunderstorm with hail',
}


@dataclass
class WeatherReading:
    location: str
    temperature_c: float | None
    condition: str
    humidity_percent: float | None
    timestamp: str
    source: str
    stale: bool = False
    age_minutes: int = 0

    def to_dict(self) -> dict:
        return {
            'location': self.location,
            'temperature_c': self.temperature_c,
            'condition': self.condition,
            'humidity_percent': self.humidity_percent,
            'timestamp': self.timestamp,
            'source': self.source,
            'stale': self.stale,
            'age_minutes': self.age_minutes,
        }


@runtime_checkable
class WeatherProvider(Protocol):
    name: str

    def get_weather(self, *, latitude: float, longitude: float,
                    location: str) -> WeatherReading: ...


class OpenMeteoWeatherProvider:
    name = 'open-meteo'

    def __init__(self, *, timeout: float = 10.0, cache_ttl_s: int = 900) -> None:
        self.timeout = timeout
        self.cache_ttl_s = cache_ttl_s
        self._cache: dict[str, tuple[float, WeatherReading]] = {}

    def get_weather(self, *, latitude: float, longitude: float,
                    location: str) -> WeatherReading:
        key = f'{latitude:.3f},{longitude:.3f}'
        cached = self._cache.get(key)
        if cached and (time.time() - cached[0]) < self.cache_ttl_s:
            return cached[1]

        try:
            response = httpx.get(OPEN_METEO_URL, params={
                'latitude': latitude, 'longitude': longitude,
                'current': 'temperature_2m,relative_humidity_2m,weather_code',
                'timezone': 'auto',
            }, timeout=self.timeout)
        except httpx.HTTPError as exc:
            return self._stale_or_fail(key, f'{type(exc).__name__}')
        if response.status_code >= 400:
            return self._stale_or_fail(key, f'HTTP {response.status_code}')

        current = (response.json().get('current') or {})
        reading = WeatherReading(
            location=location,
            temperature_c=current.get('temperature_2m'),
            condition=WEATHER_CODES.get(int(current.get('weather_code', -1)), 'unknown'),
            humidity_percent=current.get('relative_humidity_2m'),
            timestamp=str(current.get('time') or ''),
            source=self.name,
        )
        self._cache[key] = (time.time(), reading)
        return reading

    def _stale_or_fail(self, key: str, reason: str) -> WeatherReading:
        """A stale reading is better than a guess — but it must say it is stale."""
        cached = self._cache.get(key)
        if not cached:
            raise WeatherError(f'Weather is unavailable ({reason}) and nothing is cached')
        age_minutes = int((time.time() - cached[0]) / 60)
        log.info('weather_stale', fields={'reason': reason, 'age_minutes': age_minutes})
        return WeatherReading(**{**cached[1].__dict__, 'stale': True, 'age_minutes': age_minutes})


class StaticWeatherProvider:
    """Test double.  Returns whatever it was constructed with."""
    name = 'static'

    def __init__(self, reading: WeatherReading) -> None:
        self.reading = reading
        self.calls = 0

    def get_weather(self, *, latitude: float, longitude: float,
                    location: str) -> WeatherReading:
        self.calls += 1
        return self.reading
