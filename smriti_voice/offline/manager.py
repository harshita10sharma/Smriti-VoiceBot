"""Online/offline execution policy.

Connectivity is a *fact to be measured*, not a flag to be assumed.  The manager
probes cheaply, caches the answer briefly, and reports one of four modes:

``ONLINE_PRIMARY``   network reachable and at least one cloud provider configured
``OFFLINE_PRIMARY``  no network (or forced offline) but local capability exists
``DEGRADED``         serving from deterministic local data only, no general AI
``ERROR``            nothing can serve this turn
"""
from __future__ import annotations

import socket
import time
from dataclasses import dataclass

from ..config import AppConfig
from ..schemas import ExecutionMode

PROBE_HOSTS = (('1.1.1.1', 53), ('8.8.8.8', 53))


@dataclass
class Connectivity:
    online: bool
    checked_at: float
    detail: str


class OfflineManager:
    def __init__(self, config: AppConfig, *, cache_seconds: int | None = None,
                 probe_timeout: float | None = None) -> None:
        import os
        self.config = config
        self.cache_seconds = cache_seconds if cache_seconds is not None else int(
            os.getenv('SMRITI_CONNECTIVITY_CACHE_SECONDS', '20'))
        self.probe_timeout = probe_timeout if probe_timeout is not None else float(
            os.getenv('SMRITI_CONNECTIVITY_PROBE_TIMEOUT', '1.5'))
        self._cached: Connectivity | None = None

    # ------------------------------------------------------------------ #
    def probe(self, *, force: bool = False) -> Connectivity:
        if self.config.offline_forced:
            return Connectivity(False, time.time(), 'forced_offline')
        now = time.time()
        if not force and self._cached and (now - self._cached.checked_at) < self.cache_seconds:
            return self._cached
        detail = 'no_route'
        online = False
        for host, port in PROBE_HOSTS:
            try:
                with socket.create_connection((host, port), timeout=self.probe_timeout):
                    online, detail = True, f'reachable:{host}'
                    break
            except OSError as exc:
                detail = f'{type(exc).__name__}'
        self._cached = Connectivity(online, now, detail)
        return self._cached

    # ------------------------------------------------------------------ #
    def mode(self, *, llm_available: bool, local_llm_available: bool = False) -> ExecutionMode:
        """Decide how this turn will be served."""
        connectivity = self.probe()
        if connectivity.online and llm_available:
            return ExecutionMode.ONLINE_PRIMARY
        if local_llm_available:
            return ExecutionMode.OFFLINE_PRIMARY
        # Deterministic local answers still work: family, routine, medicine, games.
        return ExecutionMode.DEGRADED

    def is_online(self) -> bool:
        return self.probe().online

    def snapshot(self) -> dict:
        connectivity = self.probe()
        return {'online': connectivity.online, 'detail': connectivity.detail,
                'forced_offline': self.config.offline_forced,
                'checked_seconds_ago': int(time.time() - connectivity.checked_at)}
