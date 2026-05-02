# ADR 0080 — Multilingual Voice-to-Tree Extraction (Phase 10.9e)

* Status: Accepted (Slice A)
* Date: 2026-05-02
* Phase: 10.9e
* Depends-on: 10.9a (`#163/164/166`) ✓ in main, 15.10 (`#170`) ✓ in main
* Soft-dep: 10.9b (`#197`) — slice B blocked on it; slice A independent

## Context

Owner runs through-language genealogy: his maternal-side DNA cousins are
distributed across English, Russian, and Hebrew sources, and he himself
will dictate to the voice-to-tree pipeline in Russian. Geoffrey demo on
2026-05-06 is English-only (Geoff speaks no Russian); multilingual is a
"look, also Russian works" wow factor on top of the English-default flow.

Pre-existing in main when this ADR was written:

* **10.9a Whisper transcription** — `WhisperClient.transcribe(...,
  language_hint=...)` already passes `language=` to the OpenAI Whisper
  API, and `audio_sessions` POST upload accepts a `language_hint` form
  field that propagates through `AudioSession.language` →
  `arq` job → `TranscribeAudioInput.language_hint`. The whole chain
  works for any caller that fills the field.
* **10.9d frontend voice page** — `voice-api.ts.uploadAudioSession`
  already accepts `{languageHint}` option and appends it to FormData.
  The recorder UI just doesn't surface a picker, so callers always get
  `undefined`.
* **15.10 Multilingual Name Engine** —
  `Transliterator.to_latin(text, source_script={"cyrillic"|"hebrew"|
  "yiddish"}, standard={"bgn"|"iso9"|"loc"})` is in main. BGN/PCGN for
  Cyrillic, ALA-LC for Hebrew, idempotent on Latin input.

NOT in main:

* **10.9b voice extraction** (`#197`) — would add `voice/extract`
  endpoint, `voice_extracted_proposal` table, NLU prompts. PR is
  CONFLICTING; ETA unclear; explicitly excluded as a blocker per brief
  `Hard dep '10.9b в main' DOWNGRADED: graceful fallback`.

## Decision

Phase 10.9e ships in **two slices**:

### Slice A (this ADR / this PR) — independent of 10.9b

1. **Frontend language picker** on `recorder.tsx`: native `<select>`
   with `Auto / English / Russian / Hebrew` options. Default = Auto
   (empty value → Whisper auto-detects). Selected value flows through
   `uploadAudioSession({languageHint})` to the existing `language_hint`
   form field that's already wired to AudioSession + Whisper.
2. **Locale hint copy** when RU/HE selected: tells the user that names
   will be preserved in the original script and transliterated to Latin
   via the Phase 15.10 engine.
3. **Locale-aware AI prompt scaffolding**: new files
   `name_normalizer_v1_ru.md` and `name_normalizer_v1_he.md` alongside
   the untouched English `name_normalizer_v1.md`. New
   `prompts.registry.select_for_locale(base, locale)` helper picks
   the locale variant if it exists, falls back to base otherwise. The
   template files exist; consumers will land in slice B / Phase 10.9b.
4. **Transliteration helper** `ai_layer.multilingual.transliterate_for_locale(name,
   locale)` returning `{original, latin, script}` — thin wrapper over
   15.10 with locale → script mapping + Unicode-block auto-detect.
5. **Tests** — backend regression on existing transcription path;
   frontend picker-renders + selection-flows-to-upload; transliteration
   round-trips for RU + HE + EN passthrough; registry locale fallback.

### Slice B (deferred, follow-up PR after 10.9b merges)

* `voice_extracted_proposal.source_language` + `.transliterated_names`
  jsonb columns (alembic migration).
* `POST /voice/extract` `language` body / query param.
* Wire `select_for_locale(NAME_NORMALIZER_V1, lang)` and
  `transliterate_for_locale(name, lang)` into the actual extraction
  pipeline.
* End-to-end test: Russian audio → Person extraction → assert
  `transliterated_names[0]` = `{"original": "Иван Петрович", "latin":
  "Ivan Petrovich"}`.

Rationale for split:

* 10.9b is in flight (`#197`, currently CONFLICTING). Stacking 10.9e
  on it violates `feedback_no_stacked_prs` and risks demo timing.
* Slice A delivers the user-visible wow factor (the language picker
  is the demoable feature) without depending on the missing extraction
  layer. Whisper transcription alone benefits from the language hint
  even before any structured extraction runs.
* Slice B becomes a small follow-up (~150 LOC) once 10.9b lands and
  exposes a stable extension surface.

## Alternatives rejected

1. **Single multilingual prompt template.** One huge prompt that handles
   all locales via instructions. Quality drops because Russian
   patronymic conventions and Hebrew RTL/gender rules differ enough
   that interleaving them in a single prompt confuses the model;
   accuracy is empirically lower than per-locale templates. Per-locale
   is also easier to A/B-test and roll back.
2. **Auto-detect only (no user picker).** Whisper's language detection
   is good but not perfect on short utterances. A user with a strong
   accent dictating English may be misdetected as Russian; a transcript
   with one sentence in English then Russian is genuinely ambiguous.
   Explicit user override + auto-default is the lowest-friction
   correct path.
3. **Per-language fine-tuned Whisper model.** Whisper-large-v3 already
   handles 99 languages; the limiting factor for our pipeline is the
   downstream NLU prompt (which the per-locale templates address), not
   the transcription quality.
4. **Machine translation between languages.** Explicit out-of-scope:
   we PRESERVE the original script + transliterate to Latin for
   indexing. Translation between Russian and Hebrew (or to English) is
   a separate feature the genealogist may opt into manually.
5. **Stack 10.9e on `#197`.** Forbidden by
   `feedback_no_stacked_prs.md` and explicitly by the brief
   anti-drift. Slice A independence resolves it.

## Consequences

* The `recorder.tsx` UI grows by ~25 lines (one `<select>` + one
  hint paragraph). i18n strings (en + ru) +6 keys each.
* `prompts/registry.py` grows by ~30 lines (locale parameter on
  `PromptTemplate`, `select_for_locale` helper, two new constants
  for the RU/HE name_normalizer variants).
* `ai_layer.multilingual` is a new module (~80 LOC) wrapping 15.10.
* Test coverage: regression test on `audio_sessions_api.py` POST
  upload locks the EN form-field-omitted path; new test asserts the
  RU/HE form-field-set path flows to AudioSession + arq + Whisper.
* No DB migration in slice A. No new endpoints. No new microservice.

## Risks

* AI hallucinations on Russian patronymic generation in slice B —
  mitigated via "conservative output: NULL fields better than guessed"
  rule in the locale prompts.
* Yiddish lexicon gap in 15.10 (documented in 15.10's own ADR-0068):
  Yiddish names get Hebrew-script-mapping for transliteration; full
  Yiddish lexicon is a follow-up phase.
* Hebrew RTL display on PDF reports (Phase 24.x) — not in 10.9e scope;
  WeasyPrint handles BiDi for the `original_*` fields automatically.

## Out-of-scope (slice A and B both)

* Translation between languages.
* Multi-speaker audio with per-speaker locale.
* On-the-fly script switching mid-utterance ("он сказал, что his name is John").
* Custom dialect tuning (e.g. Hebrew-of-Yemen vs Hebrew-of-Lithuania).
