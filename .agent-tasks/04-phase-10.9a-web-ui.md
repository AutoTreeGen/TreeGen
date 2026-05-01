# Agent #4 — Phase 10.9a: web UI (recorder + consent banner + sessions list + transcript view)

Ты — инженер на проекте AutoTreeGen / SmarTreeDNA (репозиторий `F:\Projects\TreeGen`).

## Перед началом ОБЯЗАТЕЛЬНО прочитай

1. `CLAUDE.md` — конвенции (RU comments, EN identifiers, Next.js 15,
   React 19, TypeScript strict, Tailwind 4, shadcn/ui, biome, `pnpm`,
   pre-commit must pass, **`--no-verify` запрещён**).
2. `docs/feature_voice_to_tree.md` — §3.1 user flow, §3.5 consent gate UI.
3. `docs/adr/0064-voice-to-tree-pipeline.md` — §«Решение» E1 (read-only
   transcript на 10.9a, edit — 10.9d), §«Риски» (Safari fallback — 10.9d).
4. Existing patterns:
   - `apps/web/src/components/ai-extraction-panel.tsx` — recent panel-style
     component (poll status, render result)
   - `apps/web/src/lib/api.ts` — API client extensions pattern
   - `apps/web/src/app/trees/[id]/` — существующие tree-pages
   - `apps/web/messages/{en,ru}.json` — i18n keys (Phase 4.13b — обязательно
     добавлять оба)
   - `apps/web/src/components/__tests__/ai-extraction-panel.test.tsx` —
     тестовый паттерн

## ЗАВИСИМОСТЬ — стартовать после merge

**Stay in your lane.** Сначала должны merge'нуться в `main` PR'ы #1 (ORM
migration) и #2 (ai-layer Whisper) — без них #3 в принципе не пройдёт CI,
а у тебя поедет контракт. Когда #1 + #2 в `main`, дождись ещё и merge'а
PR #3 (parser-service API) — это даёт тебе финальную форму endpoint'ов.
Только после этого открывай свой PR.

- **#1 (ORM migration) + #2 (ai-layer Whisper)** — это upstream'ы #3.
  Пока они не в `main` — НЕ начинай scaffold.
- **#3 (parser-service API)** — для контракта endpoint'ов. До его merge
  scaffold'ить против stub'а можно (хардкод-моки в `lib/api.ts`), но **не
  открывай PR до merge'а #3** — иначе CI без backend'а в e2e упадёт.
- **Не лезь в чужие worktree'ы.** Каждый upstream-агент работает в своей
  ветке / worktree (`F:/Projects/TreeGen-wt/phase-10-9a-*`). Не читай,
  не правь, не «помогай» — это путает merge-порядок и ломает чужой CI.
  Если #1 / #2 / #3 ещё открыты — жди их merge'а в `main` и пуллай оттуда.

## Branch

```text
feat/phase-10-9a-web-ui
```

От свежего main (после merge'а #1 + #2 + #3): `git checkout main && git pull
&& git checkout -b feat/phase-10-9a-web-ui`.

## Scope

### A. API client extensions (`lib/api.ts`)

Добавить функции:

```typescript
// Consent
async function getAudioConsent(treeId: string): Promise<AudioConsentResponse>
async function setAudioConsent(treeId: string, provider?: 'openai'): Promise<AudioConsentResponse>
async function revokeAudioConsent(treeId: string): Promise<{ erasure_job_ids: string[] }>

// Sessions
async function uploadAudioSession(
  treeId: string,
  audio: Blob,
  options?: { languageHint?: string; mimeType?: string }
): Promise<AudioSessionResponse>
async function getAudioSession(sessionId: string): Promise<AudioSessionResponse>
async function listAudioSessions(treeId: string, page?: number): Promise<AudioSessionListResponse>
async function deleteAudioSession(sessionId: string): Promise<void>
```

Типы должны соответствовать Pydantic-моделям из #3. Используй существующий
паттерн с `fetch()` + error mapping; не вводи новый HTTP-клиент.

### B. Components

#### B.1 `components/voice/consent-banner.tsx`

Показать баннер «Voice-to-tree (beta)»:

- Если `consent_egress_at === null` — текст согласия (i18n key
  `voice.consent.body`) + кнопка `[I consent]`
- Если уже set — короткий текст «Consent granted at {date}» + кнопка
  `[Revoke]` (с confirm-dialog)

Текст согласия (RU) — из ADR-0064 §3.5:

> «Запись будет отправлена в OpenAI Whisper для расшифровки. OpenAI хранит
> запросы 30 дней по политике Standard tier. После расшифровки текст
> хранится в вашей зоне дерева. Вы можете отозвать согласие в настройках,
> что удалит все записи.»

EN-версия — параллельно в `messages/en.json`.

State: `useState` для loading/error; никакого глобального store.

#### B.2 `components/voice/recorder.tsx`

Использовать `MediaRecorder` API:

- Формат: WebM/Opus (`audio/webm; codecs=opus`)
- Кнопка Record/Stop с состояниями (idle/recording/uploading/uploaded/error)
- Disabled state с tooltip «Требуется согласие на egress аудио» если
  consent отсутствует (двойная защита фронт + бек 403)
- Min duration 5 сек, max 5 мин (UX-cap, не путать с `WHISPER_MAX_DURATION_SEC`)
- Visual feedback: waveform (опционально, можно простой счётчик секунд)
- На stop: загружаем blob через `uploadAudioSession()`

Использовать shadcn/ui `Button`, `Card`, `Tooltip`. Никаких сторонних
audio-библиотек — `MediaRecorder` нативный.

#### B.3 `components/voice/sessions-list.tsx`

Список audio sessions дерева:

- Запрос `listAudioSessions(treeId)` через `useEffect` или TanStack Query
  (если уже используется в проекте — посмотри в `app/trees/`)
- Для каждой session — карточка со status, duration, language, created_at,
  кнопка `[View transcript]` или `[Delete]`
- Спиннер для status `uploaded`/`transcribing` с auto-refresh каждые 3
  секунды (poll). Когда `ready` или `failed` — стоп poll.

#### B.4 `components/voice/transcript-view.tsx`

Read-only рендер транскрипта:

- Текст в `<pre>`-like с `whitespace: pre-wrap`
- Метаданные: language, duration, cost_usd, transcript_provider
- Caveat-баннер: «Авто-транскрипт. Проверьте перед использованием.»
- НИКАКОГО редактирования (10.9a — read-only). Кнопка `[Edit]` отсутствует;
  это для 10.9d.

### C. Page integration

`apps/web/src/app/trees/[id]/voice/page.tsx`:

```tsx
export default async function TreeVoicePage({ params }) {
  const { id } = await params;
  return (
    <div className="space-y-6">
      <ConsentBanner treeId={id} />
      <Recorder treeId={id} />
      <SessionsList treeId={id} />
    </div>
  );
}
```

Добавить link в site-header или tree navigation на `/trees/[id]/voice`
(найди существующий tree-nav компонент).

### D. i18n keys (`messages/{en,ru}.json`)

Namespace `voice.*`:

```json
{
  "voice": {
    "title": "Voice-to-tree (beta)",
    "consent": {
      "body": "...",
      "grant": "I consent",
      "revoke": "Revoke consent",
      "revoke_confirm": "..."
    },
    "recorder": {
      "start": "Start recording",
      "stop": "Stop",
      "uploading": "Uploading...",
      "consent_required": "Consent required to record audio"
    },
    "transcript": {
      "loading": "Transcribing...",
      "caveat": "...",
      "metadata": { "language": "...", "duration": "...", "cost": "..." }
    },
    "sessions": {
      "empty": "No recordings yet",
      "delete_confirm": "..."
    },
    "errors": {
      "consent_required": "...",
      "audio_too_long": "...",
      "stt_unavailable": "..."
    }
  }
}
```

Все keys обязательно в обоих файлах (en + ru) — pre-commit hook
`check-i18n-strings (Phase 4.13)` упадёт иначе.

### E. Тесты

`components/voice/__tests__/consent-banner.test.tsx`:

- Render с null consent → видна кнопка Grant
- Click Grant → API mock called → re-render с timestamp
- Render с заполненным consent → видна кнопка Revoke
- Click Revoke → confirm dialog → API mock called

`components/voice/__tests__/recorder.test.tsx`:

- Recorder disabled при null consent
- Click Record → MediaRecorder mock starts
- Click Stop → upload mock called, status updates

`components/voice/__tests__/sessions-list.test.tsx`:

- Empty list → empty state
- List с одной session — card rendered
- Status `transcribing` → poll triggered
- Status `ready` → poll stopped, transcript link shown

**E2E (Playwright)** — `apps/web/playwright/voice-flow.spec.ts`:

- Happy path: open `/trees/.../voice` → grant consent → record 5 sec → wait
  for transcript ≤ 30 sec (mocked Whisper) → verify text visible
- 403 path: visit без consent → recorder disabled → tooltip text correct

## Definition of Done

- [ ] 4 компонента + 1 page implemented
- [ ] `lib/api.ts` extended с 7 новыми функциями
- [ ] i18n en + ru заполнены, `check-i18n-strings` passing
- [ ] `pnpm -F web typecheck` — passing strict
- [ ] `pnpm -F web lint` (biome) — passing (без `noLabelWithoutControl` —
      проверь, что не повторяешь ошибку из waitlist-form pre-existing)
- [ ] `pnpm -F web test` — passing все unit-tests
- [ ] Playwright e2e (happy + consent-403) — passing на staging-mock
- [ ] `uv run pre-commit run --files <ваши_файлы>` — passing
- [ ] PR-описание ссылается на ADR-0064 §3.1 + §3.5
- [ ] PR-описание прикрепляет screencast (gif/mp4 30 сек) демо-сценария —
      это для рев инвестору 06.05

## Что НЕ трогать

- `packages/shared-models/` — закрыто #1
- `packages/ai-layer/` — закрыто #2
- `services/parser-service/` — закрыто #3
- `apps/landing/` — другой sub-app, к 10.9a не относится

## Подводные камни

1. **MediaRecorder в Safari.** WebM/Opus не поддерживается. На 10.9a — пометь
   баннером «Demo поддерживается в Chrome / Firefox; Safari fallback в
   следующей версии». Не пытайся фиксить в этом sprint'е (отложено в 10.9d).
2. **Long-poll cost.** poll каждые 3 сек на странице с 10+ сессиями = 200+
   requests/min. Сделай poll **только для не-готовых** (status ∈ {uploaded,
   transcribing}). Стоп когда ready/failed.
3. **Privacy в UI.** Не показывай audio_session.id в URL'ах в открытом виде
   слишком близко к persist-метаданным; UUID OK, но не embed в `og:image`
   или sharing-links.
4. **Locale fallback.** RU-локаль обязательна для демо 06.05 (owner тестирует
   на русском). Проверь, что i18n provider корректно роутится.
5. **TanStack Query (если используется).** Убедись, что `staleTime` для
   sessions list — 0, чтобы новая запись отражалась после upload без F5.
6. **A11y.** Не повторяй pre-existing `noLabelWithoutControl` баг из
   waitlist-form. Каждый `<input>` / `Checkbox` имеет `id` + `<label htmlFor>`
   ИЛИ обёрнут в label с native input внутри.

## Conventional Commits шаблоны

```text
feat(web): add voice-to-tree page (recorder + consent banner + sessions list) (Phase 10.9a)
feat(web): extend api.ts with audio sessions and consent client
feat(web): add voice.* i18n keys for en/ru
test(web): add voice-to-tree component + e2e tests
```

## Demo-готовность чеклист

К 05.05 EOD на staging:

- [ ] OPENAI_API_KEY в Secret Manager на staging cluster
- [ ] MinIO bucket `audio-sessions` создан
- [ ] `audio_sessions` table мигрирована
- [ ] Owner на staging-tree поставил consent
- [ ] Test-recording 30 сек RU прошёл успешно (transcript ≤ 30 сек)
- [ ] Browser (Chrome) тестируется на демо-машине

Если хоть один пункт failed — escalate к owner'у в день обнаружения.
