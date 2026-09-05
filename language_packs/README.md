# Language packs

A pack is JSON. The voice engine does not contain language-specific Python logic.

`status` values:
- `validated_local`: a local provider is configured and can be provisioned.
- `benchmark_only`: model exists but needs device/field validation before production.
- `unsupported_local`: no validated local ASR is currently shipped.

Never silently fall back from a requested language to another language. If a requested language has
no usable local provider, return a structured `LANGUAGE_UNAVAILABLE` result.
