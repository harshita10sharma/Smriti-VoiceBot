"""Regenerate docs/integration/language_matrix.json from the live registry.

Writes exactly what GET /v1/languages would return for each language, using
the same model_dump(mode='json') serialization as the real endpoint — so this
file cannot drift from the API's actual field names, value formats, or set of
languages the way a hand-maintained copy can.

    python tools/generate_language_matrix.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from smriti_voice.app import Application  # noqa: E402


def main() -> int:
    app = Application.build()
    languages = [capability.model_dump(mode='json') for capability in app.languages.all()]
    payload = {
        'generated_from': 'smriti_voice.language.registry.LanguageService.all(), '
                          'via tools/generate_language_matrix.py',
        'note': 'Field names, value formats, and language set are identical to a live '
                'GET /v1/languages response body (same model_dump(mode="json") call the '
                'route itself uses) plus this generated_from/note wrapper.',
        'count': len(languages),
        'languages': languages,
    }
    out_path = ROOT / 'docs' / 'integration' / 'language_matrix.json'
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + '\n',
                        encoding='utf-8')
    print(f'Wrote {len(languages)} languages to {out_path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
