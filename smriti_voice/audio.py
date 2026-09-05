from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import numpy as np, sounddevice as sd, soundfile as sf
from scipy.signal import resample_poly

@dataclass(frozen=True)
class AudioQuality:
    usable:bool; reason:str; duration_s:float; rms_dbfs:float; peak:float; clipping_ratio:float; speech_ratio:float

def _trim(x,sr):
    if x.size==0:return x
    frame=max(1,int(sr*.03)); hop=max(1,int(sr*.015));
    vals=[]
    for i in range(0,max(1,len(x)-frame+1),hop):
        vals.append(float(np.sqrt(np.mean(x[i:i+frame]**2)+1e-12)))
    if not vals:return x
    a=np.asarray(vals); threshold=max(np.percentile(a,20)*1.8,1e-4); ids=np.flatnonzero(a>=threshold)
    if ids.size==0:return np.array([],dtype=np.float32)
    s=max(0,ids[0]*hop-int(.12*sr)); e=min(len(x),ids[-1]*hop+frame+int(.12*sr)); return x[s:e]

def assess(x,sr,min_s=.35,max_s=6.0):
    x=np.asarray(x,dtype=np.float32).reshape(-1); dur=len(x)/sr if sr else 0
    rms=float(np.sqrt(np.mean(x*x)+1e-12)); db=20*np.log10(rms+1e-12); peak=float(np.max(np.abs(x))) if len(x) else 0
    clip=float(np.mean(np.abs(x)>=.999)) if len(x) else 0
    speech=_trim(x,sr); ratio=len(speech)/max(1,len(x))
    usable=dur>=min_s and dur<=max_s and db>-48 and peak<.9995 and ratio>=.08
    reason='ok' if usable else ('too_short' if dur<min_s else 'too_long' if dur>max_s else 'clipped' if peak>=.9995 else 'too_quiet' if db<=-48 else 'no_speech')
    return AudioQuality(usable,reason,dur,db,peak,clip,ratio)

def load_mono_16k(path:Path):
    x,sr=sf.read(path,dtype='float32',always_2d=False); x=np.mean(x,axis=1) if x.ndim==2 else x
    if sr!=16000: x=resample_poly(x,16000,sr).astype(np.float32)
    return np.clip(x,-1,1),16000

def record_push_to_talk(path:Path,seconds:float,sr:int,channels:int):
    print('Recording… speak now.')
    data=sd.rec(int(seconds*sr),samplerate=sr,channels=channels,dtype='float32'); sd.wait()
    x=data[:,0] if channels==1 else np.mean(data,axis=1)
    q=assess(x,sr); path.parent.mkdir(parents=True,exist_ok=True)
    sf.write(path,x,sr,subtype='PCM_16'); return q
