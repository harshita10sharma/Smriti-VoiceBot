from __future__ import annotations
from pathlib import Path
from typing import Any
import os
import json
import time

class ASRError(RuntimeError): pass

class BaseASR:
    online = False
    name = "base"
    def warmup(self): pass
    def transcribe(self,wav:Path,language:str|None=None): raise NotImplementedError

class SarvamASR(BaseASR):
    online=True; name="sarvam"
    BASE="https://api.sarvam.ai"
    SUPPORTED={
        "eng":"en-IN","hin":"hi-IN","asm":"as-IN","ben":"bn-IN","brx":"brx-IN",
        "mni":"mni-IN","npi":"ne-IN","kok":"kok-IN","ksm":"ks-IN","snd":"sd-IN",
        "san":"sa-IN","sat":"sat-IN","doi":"doi-IN","guj":"gu-IN","mar":"mr-IN",
        "ori":"od-IN","pan":"pa-IN","tam":"ta-IN","tel":"te-IN","kan":"kn-IN",
        "mal":"ml-IN","urd":"ur-IN"
    }
    def __init__(self,key,model="saaras:v4",timeout=25): self.key=key; self.model=model; self.timeout=timeout
    def transcribe(self,wav,language=None):
        if not self.key: raise ASRError("SARVAM_API_KEY not configured")
        import httpx
        code="unknown" if not language or language=="auto" else self.SUPPORTED.get(language,language)
        with wav.open("rb") as fh:
            files={"file":(wav.name,fh,"audio/wav")}
            data={"model":self.model,"language_code":code,"mode":"transcribe"}
            r=httpx.post(self.BASE+"/speech-to-text",headers={"api-subscription-key":self.key},files=files,data=data,timeout=self.timeout)
        if r.status_code>=400: raise ASRError(f"SARVAM_HTTP_{r.status_code}:{r.text[:200]}")
        d=r.json(); return str(d.get("transcript","")).strip(), d.get("language_code"), float(d.get("language_probability") or 0.0)
    def translate(self,wav,language=None):
        if not self.key: raise ASRError("SARVAM_API_KEY not configured")
        import httpx
        code="unknown" if not language or language=="auto" else self.SUPPORTED.get(language,language)
        with wav.open("rb") as fh:
            files={"file":(wav.name,fh,"audio/wav")}
            data={"model":"saaras:v3","language_code":code,"mode":"translate"}
            r=httpx.post(self.BASE+"/speech-to-text",headers={"api-subscription-key":self.key},files=files,data=data,timeout=self.timeout)
        if r.status_code>=400: raise ASRError(f"SARVAM_TRANSLATE_HTTP_{r.status_code}:{r.text[:200]}")
        d=r.json(); return str(d.get("transcript","")).strip()

class OpenAIASR(BaseASR):
    online=True; name="openai"
    BASE="https://api.openai.com/v1/audio/transcriptions"
    def __init__(self,key,model="gpt-4o-transcribe",timeout=30): self.key=key; self.model=model; self.timeout=timeout
    def transcribe(self,wav,language=None):
        if not self.key: raise ASRError("OPENAI_API_KEY not configured")
        import httpx
        data={"model":self.model,"temperature":"0"}
        # Only pass a language hint for well-defined ISO-639-1 codes.
        # For low-resource/custom NER codes, omit it and let the multilingual model detect.
        # 'mni' (Meiteilon/Manipuri) has no ISO-639-1 two-letter code; 'mn' is
        # Mongolian, an unrelated language, so it must never be hinted here.
        iso_hints={'eng':'en','hin':'hi','asm':'as','ben':'bn','brx':'brx','npi':'ne'}
        if language and language in iso_hints and len(iso_hints[language])==2:
            data['language']=iso_hints[language]
        with wav.open("rb") as fh:
            r=httpx.post(self.BASE,headers={"Authorization":f"Bearer {self.key}"},files={"file":(wav.name,fh,"audio/wav")},data=data,timeout=self.timeout)
        if r.status_code>=400: raise ASRError(f"OPENAI_HTTP_{r.status_code}:{r.text[:200]}")
        return str(r.json().get("text","")).strip(), None, 0.0

class IndicConformerASR(BaseASR):
    online=False; name="indicconformer"
    def __init__(self,model_id,local_dir,language): self.model_id=model_id; self.local_dir=Path(local_dir); self.language=language; self.model=None
    def warmup(self):
        if self.model is not None:return
        try: import onnx_asr
        except ImportError as e: raise ASRError('onnx-asr is not installed') from e
        # Do NOT pre-create local_dir: onnx-asr's resolver treats an *existing*
        # directory as a complete local cache and skips the Hugging Face download
        # entirely, even if it is empty. Let it create the directory itself when
        # it actually downloads something; on a pre-provisioned machine the
        # directory already exists with real files, so this is a no-op there.
        if os.getenv('SMRITI_PROVISIONING') == '1':
            os.environ.pop('HF_HUB_OFFLINE', None)
        else:
            os.environ.setdefault('HF_HUB_OFFLINE', '1')
        self.model=onnx_asr.load_model(self.model_id, path=str(self.local_dir), quantization='int8')
    def transcribe(self,wav,language=None):
        self.warmup()
        try:return str(self.model.recognize(str(wav),language=language or self.language)).strip(), self.language, 1.0
        except TypeError:return str(self.model.recognize(str(wav))).strip(), self.language, 1.0

class NeASR(BaseASR):
    online=False; name="ne_asr"
    def __init__(self,model_id,local_dir): self.model_id=model_id; self.local_dir=Path(local_dir); self.processor=None; self.model=None
    def warmup(self):
        if self.model is not None:return
        try:
            from transformers import WhisperProcessor, WhisperForConditionalGeneration
        except ImportError as e: raise ASRError('transformers is required for ne_asr provider') from e
        self.local_dir.mkdir(parents=True,exist_ok=True)
        self.processor=WhisperProcessor.from_pretrained(self.model_id,cache_dir=self.local_dir,local_files_only=True)
        self.model=WhisperForConditionalGeneration.from_pretrained(self.model_id,cache_dir=self.local_dir,local_files_only=True).eval()
    def transcribe(self,wav,language=None):
        import soundfile as sf, torch
        self.warmup(); audio,sr=sf.read(wav,dtype='float32')
        inp=self.processor(audio,sampling_rate=16000,return_tensors='pt')
        with torch.inference_mode(): ids=self.model.generate(inp.input_features,max_new_tokens=96)
        return self.processor.batch_decode(ids,skip_special_tokens=True)[0].strip(), language, 1.0

class GenericWhisperASR(NeASR): pass

class ProviderFactory:
    def __init__(self,root:Path): self.root=root
    def create(self,provider,lang,model_id=None):
        if provider=='sarvam': return SarvamASR(os.getenv('SARVAM_API_KEY'), os.getenv('SARVAM_STT_MODEL','saaras:v4'))
        if provider=='openai': return OpenAIASR(os.getenv('OPENAI_API_KEY'), os.getenv('OPENAI_STT_MODEL','gpt-4o-transcribe'))
        if provider=='indicconformer':
            if not model_id: raise ASRError(f'No model_id configured for {lang}')
            return IndicConformerASR(model_id,self.root/'models'/f'indicconformer-{lang}',lang)
        if provider=='ne_asr':
            if not model_id: raise ASRError(f'No model_id configured for {lang}')
            return NeASR(model_id,self.root/'models'/f'ne-asr-{lang}')
        if provider=='generic_whisper':
            if not model_id: raise ASRError(f'No model_id configured for {lang}')
            return GenericWhisperASR(model_id,self.root/'models'/f'whisper-{lang}')
        raise ASRError(f'Unknown ASR provider: {provider}')
