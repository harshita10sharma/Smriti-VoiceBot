from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import time,uuid,os
from .actions import SafeActionGate,Action
from .asr import ProviderFactory,ASRError,SarvamASR,OpenAIASR
from .config import Settings,LanguageRegistry
from .intents import IntentRouter
from .intent_semantic import SemanticIntent
from .lid import NELID, LIDError
from .telemetry import Telemetry

# /v1/command is a single-shot legacy endpoint: no session, no user_id, and
# therefore no way to ask "shall I call them now?" and wait for a spoken
# yes -- that confirmation state machine only exists on the conversational
# path (POST /v1/conversation, see conversation/manager.py's
# PendingConfirmation). A call action must never execute without an
# explicit confirmation, so this endpoint recognizes the intent (the
# `action` field still reports it) but never authorizes it -- `accepted`
# stays False, exactly the same signal a client already checks before
# executing anything (see HANDOFF.md: "execute only when accepted=true").
CALL_ACTIONS = {Action.CALL_BINA.value, Action.CALL_PRIMARY_CONTACT.value}

@dataclass(frozen=True)
class TurnResult:
    request_id:str; transcript:str; language:str; language_confidence:float; intent:str; action:str; accepted:bool; confidence:float; reason:str; latency_ms:int; offline:bool; provider:str="none"

class VoiceEngine:
    def __init__(self,settings:Settings):
        self.s=settings; self.registry=LanguageRegistry(settings.language_pack_dir); self.factory=ProviderFactory(Path(settings.language_pack_dir).parent)
        self.gate=SafeActionGate(); self.telemetry=Telemetry(settings.telemetry_enabled,settings.telemetry_path,settings.retain_raw_audio); self._routers={}; self._asr={}; self.semantic=SemanticIntent(); self.lid=NELID()
    def languages(self): return self.registry.all()
    def _router(self,lang):
        if lang not in self._routers:
            pack=self.registry.get(lang); self._routers[lang]=IntentRouter(pack.commands_path,self.s.intent_threshold,self.s.ambiguity_margin)
        return self._routers[lang]
    def _result(self,rid,t,lang,lc,intent,action,accepted,conf,reason,start,offline,provider):
        ms=int((time.perf_counter()-start)*1000); return TurnResult(rid,t,lang,lc,intent,action,accepted,round(conf,4),reason,ms,offline,provider)
    def _local_provider(self,lang):
        if lang in self._asr:return self._asr[lang]
        pack=self.registry.get(lang); last=None
        if pack.status != 'validated_local':
            raise ASRError(f'Local ASR is not production-enabled for {lang} ({pack.status})')
        for p in pack.providers:
            if p in {'sarvam','openai'}: continue
            try:
                obj=self.factory.create(p,lang,pack.model_id); obj.warmup(); self._asr[lang]=obj; return obj
            except Exception as e:last=e
        raise ASRError(f'No local provider available for {lang}: {last}')
    def _online_provider(self,lang,auto=False):
        # Prefer Sarvam for supported Indian languages; OpenAI is a fallback transcription service.
        if auto or lang in SarvamASR.SUPPORTED:
            try:
                s=SarvamASR(os.getenv('SARVAM_API_KEY'),os.getenv('SARVAM_STT_MODEL','saaras:v4'))
                if s.key:return s
            except Exception: pass
        o=OpenAIASR(os.getenv('OPENAI_API_KEY'),os.getenv('OPENAI_STT_MODEL','gpt-4o-transcribe'))
        if o.key:return o
        raise ASRError('No online ASR key configured')
    def _classify(self,lang,transcript):
        if not transcript:return ('UNKNOWN','NO_ACTION',0.0,False,'empty_transcript')
        # Deterministic pack first. This is the preferred offline path after native-speaker validation.
        try:
            m=self._router(lang).classify(transcript)
            if m.accepted:return m.intent,m.action,m.score/100,True,m.reason
        except Exception: pass
        # Online multilingual semantic fallback. It lets Hindi/English and other transcribed languages
        # use the same action vocabulary without hardcoding language-specific phrases in Python.
        sem=self.semantic.classify(transcript)
        if sem:
            action,conf=sem
            if action!='NO_ACTION' and conf>=0.80:return action,action,conf,True,'semantic_accepted'
        return 'UNKNOWN','NO_ACTION',0.0,False,'intent_rejected'

    def process(self,wav:Path,language:str,request_id:str|None=None)->TurnResult:
        rid=request_id or str(uuid.uuid4()); t=time.perf_counter(); provider='none'; offline=False
        lang=language
        if language=='auto':
            # 1) Sarvam auto-detect + transcription when online.
            try:
                p=self._online_provider('auto',True); transcript,detected,prob=p.transcribe(wav,'auto'); provider=p.name; offline=False
                if detected and detected.endswith('-IN'): detected=detected[:2]
                aliases={'en':'eng','hi':'hin','as':'asm','bn':'ben','brx':'brx','mni':'mni','ne':'npi','gu':'guj','mr':'mar','od':'ori','pa':'pan','ta':'tam','te':'tel','kn':'kan','ml':'mal','ur':'urd','kok':'kok','ks':'ksm','sd':'snd','sa':'san','sat':'sat','doi':'doi'}
                lang=aliases.get(detected or '',detected or 'auto')
                # If the transcript is one of the 11 NER languages covered by NE-LID, trust NE-LID when
                # it gives a strong result. This is especially useful for Khasi/Garo/Mizo/Naga/Nyishi.
                try:
                    lid_lang,lid_score=self.lid.predict(transcript)
                    lid_map={'njz':'nyish'}
                    if lid_score>=0.80: lang=lid_map.get(lid_lang,lid_lang); prob=max(prob,lid_score)
                except Exception: pass
                if lang not in {p.code for p in self.languages()}: lang='auto'
                if lang=='auto': return self._result(rid,transcript,'auto',prob,'UNKNOWN','NO_ACTION',False,0,'LANGUAGE_NOT_CONFIGURED',t,False,provider)
            except Exception:
                # 2) Generic online transcription + NE-LID fallback.
                try:
                    p=OpenAIASR(os.getenv('OPENAI_API_KEY'),os.getenv('OPENAI_STT_MODEL','gpt-4o-transcribe')); transcript,_,_=p.transcribe(wav,'auto'); provider=p.name; offline=False
                    lang,prob=self.lid.predict(transcript); lang={'njz':'nyish'}.get(lang,lang)
                except Exception:
                    return self._result(rid,'','auto',0,'UNKNOWN','NO_ACTION',False,0,'AUTO_LANGUAGE_DETECTION_FAILED',t,False,'none')
        else:
            # Known pack: local-first. Unknown/low-resource language: online ASR fallback is allowed;
            # the action layer remains the same and can classify the resulting transcript semantically.
            try:
                self.registry.get(language)
                known=True
            except KeyError:
                known=False
            try:
                if known:
                    p=self._local_provider(language)
                    transcript,detected,prob=p.transcribe(wav,language); provider=p.name; offline=True
                else:
                    p=self._online_provider(language)
                    transcript,detected,prob=p.transcribe(wav,language); provider=p.name; offline=False
            except Exception:
                try:
                    p=self._online_provider(language)
                    transcript,detected,prob=p.transcribe(wav,language); provider=p.name; offline=False
                except Exception:
                    return self._result(rid,'',language,0,'UNKNOWN','NO_ACTION',False,0,'ASR_UNAVAILABLE',t,offline,provider)
        intent,action,conf,ok,reason=self._classify(lang if lang!='auto' else language,transcript)
        if not ok and provider=='sarvam' and isinstance(self._asr.get(lang), SarvamASR):
            try:
                en=self._asr[lang].translate(wav,lang)
                intent,action,conf,ok,reason=self._classify('eng',en)
                if ok: transcript=f'{transcript} | semantic_en={en}'
            except Exception: pass
        req=self.gate.authorize(action,intent) if ok else self.gate.authorize('NO_ACTION','UNKNOWN')
        if req.action.value in CALL_ACTIONS:
            accepted=False
            reason='call_requires_confirmation_use_conversation_endpoint'
        else:
            accepted=ok and req.action!=Action.NO_ACTION
        result=self._result(rid,transcript,lang,prob,intent,req.action.value,accepted,conf,reason,t,offline,provider)
        self.telemetry.log(request_id=rid,language=result.language,intent=result.intent,action=result.action,accepted=accepted,confidence=result.confidence,latency_ms=result.latency_ms,provider=provider,offline=offline)
        return result
