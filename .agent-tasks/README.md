# Parallel agent task briefs — Phase 10.9a sprint (May 1–5, 2026)

6 self-contained ТЗ для параллельной работы 6 инстансов Claude Code:
**4 на Phase 10.9a (voice-to-tree)** + **2 на параллельные cleanup'ы**
вне зоны 10.9a (chore main + staging prep).

> **Spec (must-read):** `docs/feature_voice_to_tree.md` (§3 — детальный scope 10.9a).
> **ADR (must-read):** `docs/adr/0064-voice-to-tree-pipeline.md` (7 осей решений).
> **Hard deadline:** demo 2026-05-06 (staging до 05.05 EOD).
> **Status (2026-05-01):** PR #160 (spec + ADR) ✅ merged (commit `5a64c68`).
> PR #161 (#5 cleanup, see «Pre-existing main issues») ✅ merged (commit `98b7ec8`).
> Sprint launched — agents #1/#2/#3/#4/#6/#7 unblocked.

## Запуск (PowerShell)

```powershell
# 1. Открыть 6 окон PS в корне репо:
1..6 | ForEach-Object {
  $title = "Agent $_"
  Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-WorkingDirectory", "F:\Projects\TreeGen",
    "-Command", "`$Host.UI.RawUI.WindowTitle = '$title'; Write-Host 'Run: claude' -ForegroundColor Green"
  )
  Start-Sleep -Milliseconds 300
}

# 2. В каждом окне:
claude
# затем скопировать содержимое соответствующего файла .agent-tasks/0N-*.md
# и вставить как первое сообщение
```

## Распределение по агентам

| # | Файл | Зона | Зависит от |
|---|---|---|---|
| 1 | `01-phase-10.9a-orm-migration.md` | `packages/shared-models/`, `infrastructure/alembic/versions/` | — (стартует первым) |
| 2 | `02-phase-10.9a-ai-layer-whisper.md` | `packages/ai-layer/` | — (стартует параллельно с #1) |
| 3 | `03-phase-10.9a-api-and-job.md` | `services/parser-service/` (кроме `main.py` router register) | #1 (ORM), #2 (Whisper client) |
| 4 | `04-phase-10.9a-web-ui.md` | `apps/web/` (recorder, consent, transcript view) | #3 (API contract) — может стартовать против stub'а из spec §3.3 (`docs/feature_voice_to_tree.md`) |
| 5 | `05-chore-main-cleanup.md` | ✅ **DONE** — shipped via PR #161 (commit `98b7ec8`, 2026-05-01). Не запускать. | — |
| 6 | `06-phase-10.9a-staging-prep.md` | `infrastructure/`, `scripts/staging-*`, `docs/runbooks/`, `ROADMAP.md` | — (полностью независим от 10.9a app-кода) |
| 7 | `07-chore-ci-optimization.md` | `.github/workflows/`, `.github/path-filters.yml`, `tests/test_ci_parity.py`, `docs/runbooks/ci-architecture.md` | — (полностью независим от sprint'а; чисто инфра CI) |

## Правила параллельного запуска (HARD)

| Ресурс | Кому разрешено трогать |
|---|---|
| `packages/shared-models/` | **только #1** (новая ORM `AudioSession` + `Tree` consent fields patch — Option A, см. #1 §B) |
| `infrastructure/alembic/` | **только #1** (1 additive миграция) |
| `packages/ai-layer/` | **только #2** (Whisper client + use_case + pricing patch) |
| `services/parser-service/` | **только #3** (кроме router-регистрации в `main.py` — координируется в финал-merge) |
| `apps/web/messages/{en,ru}.json` | **только #4** (namespace `voice.*`) |
| `apps/web/src/components/voice/` | **только #4** |
| Корневой `pyproject.toml` | никто (workspace members не добавляются на 10.9a) |
| `.env.example` | **только #3** (добавляет `OPENAI_API_KEY` + `WHISPER_*`) |
| `apps/landing/` | **только #5** (biome a11y fix) |
| `docs/adr/0003-versioning-strategy.md` | **только #5** (markdownlint MD040) |
| `docs/adr/0057-*` (rename) | **только #5** (collision fix) |
| `scripts/*.ps1` (existing 5) | **только #5** (CRLF→LF) |
| `scripts/staging-*` (новые) | **только #6** |
| `infrastructure/` (terraform/k8s/monitoring) | **только #6** |
| `docs/runbooks/`, `ROADMAP.md` | **только #6** |
| `.gitignore` | **только #5** (dev-junk patch) |
| `.github/workflows/`, `.github/path-filters.yml`, `tests/test_ci_parity.py` | **только #7** (CI рефакторинг) |
| `docs/runbooks/ci-architecture.md` | **только #7** |

## Branches

Все ветки независимые от свежего main:

```text
feat/phase-10-9a-orm-audio-sessions       (#1)
feat/phase-10-9a-ai-layer-whisper         (#2)
feat/phase-10-9a-api-and-job              (#3)
feat/phase-10-9a-web-ui                   (#4)
chore/main-cleanup-may-2026               (#5)
chore/phase-10-9a-staging-prep            (#6)
chore/ci-optimization-2026-05             (#7)
```

## Порядок merge

1. **#1 ORM + миграция** → main (контракт shared-models стабильный)
2. **#2 ai-layer** → main (контракт `WhisperClient` + `AudioTranscriber` стабильный)
3. ~~**#5 cleanup**~~ — ✅ done in #161, see «Pre-existing main issues» (resolved)
4. **#6 infra/staging prep** → main (полностью независим от app-code; ROADMAP update)
4а. **#7 CI optimization** → main (полностью независим; туда же бранч-protection follow-up для owner'а)
5. **#3 API + job** → main (rebase на 1+2)
6. **#4 web UI** → main (rebase на 5, проверка против реального API)

Параллельность Day 1: #1, #2, #6. #3 ждёт #1+#2. #4 ждёт #3
(или работает против stub'а параллельно #3).

- **День 1 (1 мая):** #1 + #2 + #6 стартуют параллельно. #1+#2
  ожидаемо мерджатся к концу дня. #6 может уйти на ревью раньше.
- **День 2 (2 мая):** #3 на свежем main; #4 начинает работу против stub'а
  параллельно
- **День 3 (3 мая):** #3 merge → #4 rebase + integration test
- **День 4 (4 мая):** bug-fix, e2e, staging deploy (используя #6 runbook)
- **День 5 (5 мая):** demo rehearsal на staging (используя #6 чеклист)

## Definition of Done — общее для всех

Каждый агент перед PR обязан:

1. `uv run pre-commit run --all-files` — passing (или знать, какой
   pre-existing fail в main игнорируется — см. §Pre-existing main issues).
2. `uv run pytest -m "not slow and not integration"` — passing для
   своих пакетов.
3. Тестовое покрытие новой логики **> 80%**.
4. Ruff + mypy strict — без `# type: ignore` без комментария.
5. Биome (для #4) — passing.
6. Conventional Commits в коммитах ветки.
7. ADR-0064 §«Решение» соблюдён буквально (никаких deviations без согласования).
8. PR-описание ссылается на ADR-0064 + spec и перечисляет, что НЕ делалось
   в этой ветке (cross-agent boundaries).

## Privacy gate (CRITICAL — общая для #1 + #3 + #4)

ADR-0064 §«Риски»: **egress без consent = critical incident**. Защита в три
слоя обязательна:

1. **DB:** `audio_session.consent_egress_at IS NOT NULL` constraint при insert
   (или application-level check в #3 ORM service).
2. **Backend:** `POST /trees/{id}/audio-sessions` без consent → **403** с
   error code `consent_required`.
3. **Frontend:** кнопка Record disabled с tooltip при отсутствии consent.

**Integration-тест в #3 обязателен:** `POST` без consent → 403. CI-блок,
без него merge запрещён.

## Pre-existing main issues — RESOLVED 2026-05-01

Все четыре известных пункта закрыты в PR #161 (commit `98b7ec8`):

- ✅ `waitlist-form.tsx:159,174` biome a11y — fixed
- ✅ `0003-versioning-strategy.md:93,118` MD040 — fixed
- ✅ `scripts/*.ps1` CRLF — handled via `.gitattributes` normalization
- ✅ 3 ADR-0057 collisions — renamed to ADR-0065 / ADR-0066, cross-refs updated

Known main-blocking pre-existing issues: **none** as of 2026-05-01. Если новый
агент видит fail в `pre-commit run --all-files` — это его собственный код,
не legacy noise.

## Координация

- Если агент обнаруживает, что его scope пересекается с чужим — **остановиться
  и спросить владельца**. Никаких MERGE-конфликтов на коде других агентов.
- Если агент видит, что его DoD требует изменений в чужой зоне (например,
  #4 нужен новый endpoint в #3) — открыть **отдельный PR** с минимальной
  правкой, не мерджить локально к своему PR.
- Schema invariants (`packages/shared-models/tests/test_schema_invariants.py`):
  только #1 правит. Другие агенты при изменении ORM **остановиться**.

## Что НЕ входит в Phase 10.9a (отложено)

- 10.9b (LLM-extraction transcript → person/event candidates) — **другой sprint**.
- 10.9c (review queue UI) — **другой sprint**.
- 10.9d (transcript editor + re-extract) — **другой sprint**.
- Self-hosted faster-whisper — Phase 10.9.x (privacy-tier пейволл).
- Speaker diarization, real-time streaming, voice-id — out of scope для всей 10.9.
- Safari MediaRecorder fallback (mp4/aac) — Phase 10.9d.
