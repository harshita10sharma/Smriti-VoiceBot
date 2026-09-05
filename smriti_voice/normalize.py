from __future__ import annotations
import re, unicodedata
def normalize_text(s:str)->str:
    s=unicodedata.normalize('NFKC',s).casefold().strip(); s=re.sub(r'[\u200b\u200c\u200d]','',s); s=re.sub(r'[^\w\s\-]',' ',s,flags=re.UNICODE); return re.sub(r'\s+',' ',s).strip()
def tokens(s:str): return normalize_text(s).split()
