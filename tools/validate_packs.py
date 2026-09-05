import json,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]; packs=json.loads((root/'language_packs/ner_languages.json').read_text())
required={'code','name','status','providers','model_id'}; seen=set()
for p in packs:
    missing=required-set(p)
    if missing: raise SystemExit(f"{p.get('code')}: missing {sorted(missing)}")
    if p['code'] in seen: raise SystemExit(f"duplicate language: {p['code']}")
    seen.add(p['code'])
    c=root/'language_packs'/f"commands_{p['code']}.json"
    if not c.exists(): raise SystemExit(f"missing commands file: {c.name}")
    json.loads(c.read_text(encoding='utf-8'))
print(f'VALID: {len(packs)} language packs')
