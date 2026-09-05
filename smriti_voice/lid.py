from __future__ import annotations

class LIDError(RuntimeError): pass

class NELID:
    def __init__(self): self.model=None
    def predict(self,text:str):
        if not text.strip(): raise LIDError('empty text')
        if self.model is None:
            try:
                from ne_lid import NELID as Model
                self.model=Model()
            except Exception as e: raise LIDError('ne-lid unavailable') from e
        r=self.model.predict(text)
        return r.get('lang'), float(r.get('score',0.0))
