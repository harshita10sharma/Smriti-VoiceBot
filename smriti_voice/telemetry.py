from __future__ import annotations
import json,time,uuid
from pathlib import Path
class Telemetry:
    def __init__(self,enabled,path,retain_audio=False): self.enabled=enabled; self.path=Path(path); self.retain_audio=retain_audio
    def log(self,**event):
        if not self.enabled:return
        safe={k:v for k,v in event.items() if k not in {'audio','raw_audio','transcript_raw_audio'}}
        safe.setdefault('event_id',str(uuid.uuid4())); safe.setdefault('ts',time.time())
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.path.open('a',encoding='utf-8') as f:f.write(json.dumps(safe,ensure_ascii=False,separators=(',',':'))+'\n')
