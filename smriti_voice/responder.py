from __future__ import annotations
from pathlib import Path
import sounddevice as sd, soundfile as sf
class LocalResponder:
    def __init__(self,directory:Path): self.directory=directory
    def play(self,key:str):
        p=self.directory/key
        if not p.exists(): return False
        data,sr=sf.read(p,dtype='float32'); sd.play(data,sr); sd.wait(); return True
