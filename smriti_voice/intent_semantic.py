from __future__ import annotations
import os, json

ALLOWED = ["OPEN_PLAY","OPEN_MY_PEOPLE","OPEN_TODAY","CALL_PRIMARY_CONTACT","CALL_BINA","OPEN_MEDICINE","HELP","STOP","NO_ACTION"]
DESCRIPTIONS = {
    "OPEN_PLAY":"open the game or play area",
    "OPEN_MY_PEOPLE":"show family members, family photos, or people",
    "OPEN_TODAY":"show today's schedule, routine, or today's activities",
    "CALL_PRIMARY_CONTACT":"call the primary caregiver/contact",
    "CALL_BINA":"legacy alias for the primary caregiver/contact",
    "OPEN_MEDICINE":"show medicine/reminder information or what medicine to take",
    "HELP":"ask for help or explain what Smriti can do",
    "STOP":"stop, cancel, go back, or stop the current voice interaction",
    "NO_ACTION":"unclear, unrelated, unsafe, or unsupported request"
}

class SemanticIntent:
    def __init__(self,key=None,model=None,timeout=15): self.key=key or os.getenv('OPENAI_API_KEY'); self.model=model or os.getenv('OPENAI_INTENT_MODEL','gpt-5-mini'); self.timeout=timeout
    def classify(self,text):
        if not self.key or not text.strip(): return None
        import httpx
        system=("You are a strict intent classifier for an elderly-care tablet. "
                "Return JSON only. Choose exactly one action from the allowed list. "
                "Never invent actions. If uncertain, choose NO_ACTION. Do not answer the user. "
                "This is navigation/command classification, not a general chatbot.\n"
                f"Allowed actions: {json.dumps(DESCRIPTIONS,ensure_ascii=False)}")
        body={
            "model":self.model,
            "input":[{"role":"system","content":system},{"role":"user","content":text}],
            "max_output_tokens":120,
            "store":False,
            "text":{
                "format":{
                    "type":"json_schema",
                    "name":"smriti_intent",
                    "strict":True,
                    "schema":{
                        "type":"object",
                        "properties":{
                            "action":{"type":"string","enum":ALLOWED},
                            "confidence":{"type":"number","minimum":0,"maximum":1}
                        },
                        "required":["action","confidence"],
                        "additionalProperties":False
                    }
                },
                "verbosity":"low"
            }
        }
        try:
            r=httpx.post("https://api.openai.com/v1/responses",headers={"Authorization":f"Bearer {self.key}","Content-Type":"application/json"},json=body,timeout=self.timeout)
            if r.status_code>=400:return None
            d=r.json(); out=d.get("output_text","").strip()
            if not out:
                for item in d.get("output",[]):
                    for c in item.get("content",[]):
                        if c.get("type")=="output_text": out += c.get("text","")
            start=out.find('{'); end=out.rfind('}')
            if start<0 or end<=start:return None
            obj=json.loads(out[start:end+1]); action=obj.get('action','NO_ACTION')
            if action not in ALLOWED:return None
            conf=float(obj.get('confidence',0.0)); return action,max(0.0,min(1.0,conf))
        except Exception:return None
