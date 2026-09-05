from __future__ import annotations
import json, re
from dataclasses import dataclass
from pathlib import Path
from rapidfuzz.fuzz import WRatio, token_set_ratio, partial_ratio
from .normalize import normalize_text,tokens
from .safety.validators import CONTRADICTORY_VERBS, CONFLICT_TARGETS, has_conflicting_utterance

@dataclass(frozen=True)
class IntentMatch:
    intent:str; action:str; score:float; matched_phrase:str|None; accepted:bool; reason:str

class IntentRouter:
    def __init__(self, path:Path, threshold=82, ambiguity_margin=8):
        self.path=path
        pack=json.loads(path.read_text(encoding='utf-8'))
        default_path=path.parent/'commands.json'
        fallback={}
        if default_path.exists():
            fallback=json.loads(default_path.read_text(encoding='utf-8'))
        merged={}
        for intent in sorted(set(pack) | set(fallback)):
            data=pack.get(intent, fallback.get(intent, {}))
            fallback_data=fallback.get(intent, {})
            phrases=[]
            seen=set()
            for source in (fallback_data, data):
                for phrase in source.get('phrases',[]):
                    norm=normalize_text(phrase)
                    if norm and norm not in seen:
                        seen.add(norm); phrases.append(phrase)
            merged[intent]={'action': data.get('action', fallback_data.get('action','NO_ACTION')), 'phrases': phrases}
        self.spec=merged; self.threshold=threshold; self.ambiguity_margin=ambiguity_margin
        self.phrases={i:[normalize_text(p) for p in v.get('phrases',[]) if normalize_text(p)] for i,v in self.spec.items()}

    def _has_conflicting_utterance(self,text:str)->bool:
        # Delegates to the shared deterministic validator so the command router and
        # the conversational layer can never disagree about what is unsafe.
        return has_conflicting_utterance(text)

    def candidates(self,text,limit=3):
        q=normalize_text(text)
        if not q:return []
        out=[]
        for intent,phrases in self.phrases.items():
            if not phrases: continue
            best=-1; bp=None
            for p in phrases:
                if q==p: score=100
                else: score=.45*WRatio(q,p)+.35*token_set_ratio(q,p)+.20*partial_ratio(q,p)
                if score>best: best,bp=score,p
            out.append(IntentMatch(intent,self.spec[intent].get('action','NO_ACTION'),best,bp,False,'candidate'))
        out.sort(key=lambda z:z.score,reverse=True); return out[:limit]
    def classify(self,text):
        q=normalize_text(text)
        if not q:
            return IntentMatch('UNKNOWN','NO_ACTION',0,None,False,'no_phrase_data_or_empty')
        if self._has_conflicting_utterance(q):
            return IntentMatch('UNKNOWN','NO_ACTION',0,None,False,'unsafe_conflicting_request')
        c=self.candidates(text,3)
        if not c:return IntentMatch('UNKNOWN','NO_ACTION',0,None,False,'no_phrase_data_or_empty')
        b=c[0]; s=c[1] if len(c)>1 else None
        if b.intent=='UNKNOWN':return IntentMatch('UNKNOWN','NO_ACTION',b.score,b.matched_phrase,False,'unknown')
        if b.score<self.threshold:return IntentMatch('UNKNOWN','NO_ACTION',b.score,b.matched_phrase,False,'below_threshold')
        if s and s.intent!='UNKNOWN' and b.score-s.score<self.ambiguity_margin:return IntentMatch('UNKNOWN','NO_ACTION',b.score,b.matched_phrase,False,'ambiguous')
        return IntentMatch(b.intent,b.action,b.score,b.matched_phrase,True,'accepted')
