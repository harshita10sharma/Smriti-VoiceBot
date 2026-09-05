"""Measure a language end to end and write the validation record.

This is the ONLY sanctioned way for a language to reach `SUPPORTED` in the
capability matrix. It needs real audio from real speakers and real credentials;
it refuses to write a record from anything else.

Input manifest: a CSV with columns `file,transcript` where `file` is a path to a
16 kHz mono WAV, relative to the manifest, and `transcript` is what the speaker
actually said.

    python tools/evaluate_languages.py --language hin --manifest data/hin/manifest.csv \
        --operator "Harshita Sharma"

Nothing is written unless --write is passed and at least one utterance was
transcribed. WER and CER are computed with Levenshtein distance; no figure in the
output is estimated.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from smriti_voice.app import Application            # noqa: E402
from smriti_voice.normalize import normalize_text   # noqa: E402

VALIDATION_PATH = ROOT / 'config' / 'language_validation.json'


def levenshtein(reference: list[str], hypothesis: list[str]) -> int:
    """Classic edit distance over tokens (or characters)."""
    if not reference:
        return len(hypothesis)
    previous = list(range(len(hypothesis) + 1))
    for i, ref_token in enumerate(reference, start=1):
        current = [i]
        for j, hyp_token in enumerate(hypothesis, start=1):
            current.append(min(previous[j] + 1,          # deletion
                               current[j - 1] + 1,       # insertion
                               previous[j - 1] + (ref_token != hyp_token)))  # substitution
        previous = current
    return previous[-1]


def word_error_rate(reference: str, hypothesis: str) -> float:
    ref_tokens = normalize_text(reference).split()
    hyp_tokens = normalize_text(hypothesis).split()
    if not ref_tokens:
        return 0.0 if not hyp_tokens else 1.0
    return levenshtein(ref_tokens, hyp_tokens) / len(ref_tokens)


def character_error_rate(reference: str, hypothesis: str) -> float:
    ref_chars = list(normalize_text(reference).replace(' ', ''))
    hyp_chars = list(normalize_text(hypothesis).replace(' ', ''))
    if not ref_chars:
        return 0.0 if not hyp_chars else 1.0
    return levenshtein(ref_chars, hyp_chars) / len(ref_chars)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--language', required=True, help='Internal code, e.g. hin, asm, ben')
    parser.add_argument('--manifest', required=True, type=Path,
                        help='CSV with columns file,transcript')
    parser.add_argument('--operator', required=True, help='Who ran this evaluation')
    parser.add_argument('--write', action='store_true',
                        help='Append the record to config/language_validation.json')
    args = parser.parse_args()

    app = Application.build()
    capability = app.languages.get(args.language)
    print(f'Language      : {capability.name} ({capability.code})')
    print(f'Current status: {capability.status.value}')
    print(f'ASR online    : {capability.asr_online}   offline: {capability.asr_offline}')
    print(f'TTS           : {capability.tts_online} ({capability.tts_provider or "none"})')
    print()

    rows = list(csv.DictReader(args.manifest.open(encoding='utf-8')))
    if not rows:
        print('ERROR: manifest is empty. A validation record needs real speech.',
              file=sys.stderr)
        return 2

    wers: list[float] = []
    cers: list[float] = []
    latencies: list[int] = []
    failures = 0
    detected_correct = 0

    print(f'{"FILE":<34} {"WER":>7} {"CER":>7} {"ms":>6}  TRANSCRIPT')
    print('-' * 100)
    for row in rows:
        wav = (args.manifest.parent / row['file']).resolve()
        if not wav.exists():
            print(f'{row["file"]:<34} {"MISSING":>7}')
            failures += 1
            continue
        try:
            result = app.asr.transcribe(wav, args.language)
        except Exception as exc:
            print(f'{row["file"]:<34} {"ERROR":>7}  {type(exc).__name__}: {exc}')
            failures += 1
            continue

        wer = word_error_rate(row['transcript'], result.transcript)
        cer = character_error_rate(row['transcript'], result.transcript)
        wers.append(wer)
        cers.append(cer)
        latencies.append(result.latency_ms)
        detection = app.detector.detect(result.transcript, provider_hint=result.language,
                                        provider_confidence=result.language_confidence)
        detected_correct += int(detection.language == args.language)
        print(f'{row["file"]:<34} {wer:>7.3f} {cer:>7.3f} {result.latency_ms:>6}  '
              f'{result.transcript[:40]}')

    if not wers:
        print('\nERROR: no utterance was transcribed. Nothing will be written.', file=sys.stderr)
        return 1

    mean_wer = statistics.mean(wers)
    mean_cer = statistics.mean(cers)
    p95 = sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)]
    detection_accuracy = detected_correct / len(wers)

    print('-' * 100)
    print(f'samples            : {len(wers)} transcribed, {failures} failed')
    print(f'mean WER           : {mean_wer:.4f}')
    print(f'mean CER           : {mean_cer:.4f}')
    print(f'p95 ASR latency    : {p95} ms')
    print(f'language detection : {detection_accuracy:.2%}')

    # TTS is checked separately: it either supports the language or it does not.
    tts_note = 'not attempted'
    if app.languages.can_speak(args.language):
        spoken = app.tts.synthesize('This is a validation test.', args.language)
        tts_note = (f'{spoken.provider} ok, {spoken.size_bytes} bytes, {spoken.latency_ms} ms'
                    if spoken.available else f'FAILED: {spoken.unavailable_reason}')
    else:
        tts_note = 'UNSUPPORTED: no configured provider speaks this language'
    print(f'tts                : {tts_note}')

    record = {
        'code': args.language,
        'date': date.today().isoformat(),
        'operator': args.operator,
        'samples': len(wers),
        'failed_samples': failures,
        'mean_wer': round(mean_wer, 4),
        'mean_cer': round(mean_cer, 4),
        'p95_asr_latency_ms': p95,
        'language_detection_accuracy': round(detection_accuracy, 4),
        'tts': tts_note,
        'benchmark_status': 'validated',
        'manifest': str(args.manifest),
    }
    print('\nRecord:')
    print(json.dumps(record, ensure_ascii=False, indent=2))

    if not args.write:
        print('\n(dry run — pass --write to append this to config/language_validation.json)')
        return 0

    document = json.loads(VALIDATION_PATH.read_text(encoding='utf-8'))
    document.setdefault('validated', [])
    document['validated'] = [entry for entry in document['validated']
                             if entry.get('code') != args.language]
    document['validated'].append(record)
    VALIDATION_PATH.write_text(json.dumps(document, ensure_ascii=False, indent=2) + '\n',
                               encoding='utf-8')
    print(f'\nWritten to {VALIDATION_PATH}. Regenerate LANGUAGE_SUPPORT.md and re-run pytest.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
