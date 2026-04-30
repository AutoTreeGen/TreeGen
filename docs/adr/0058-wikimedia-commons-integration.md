# ADR-0058: Wikimedia Commons integration architecture (Phase 9.1)

- **Status:** Accepted
- **Date:** 2026-04-30
- **Authors:** @autotreegen
- **Tags:** `archive-integration`, `multimedia`, `attribution`, `phase-9`

## Контекст

Phase 9.0-pre research note (`docs/research/archive-integrations-2026.md`,
PR #125) разделил Phase 9 источники на три tier'а по readiness'у:

- **Tier A** — public API, нет partnership-prerequisite, можно
  стартовать сразу. Wikimedia Commons, WikiTree, BillionGraves.
- **Tier B** — engineering после ручного approval (MyHeritage app-key,
  Geni revisit).
- **Tier C** — partnership-only, multi-month timelines (JewishGen,
  JRI-Poland, GenTeam, Polish/Lithuanian state archives).

Phase 9.1 — первая реальная архивная интеграция. Из Tier A research note
рекомендует начать с Wikimedia Commons (~3 дня, тривиально), потому
что:

1. Это устанавливает шаблон attribution/provenance pipeline'а, который
   потом унаследуют все остальные адаптеры (WikiTree CC-BY-SA,
   BillionGraves ToS, Tier B/C).
2. Place imagery — ортогональна pedigree-данным (FamilySearch уже даёт
   родственные связи); UX-выигрыш сразу виден на event/place pages.
3. Лицензирование Commons — самое строгое из Tier A (CC-BY-SA с
   обязательной атрибуцией, public-domain edge cases): если шаблон
   протестирован на Commons, остальные source'ы — упрощение.

Документация Wikimedia Foundation: <https://commons.wikimedia.org/wiki/Commons:API>.
User-Agent policy: <https://foundation.wikimedia.org/wiki/Policy:User-Agent_policy>.

## Рассмотренные варианты

### Вариант A — отдельный package + новая таблица `place_images`

- ✅ «Чисто» по доменной модели: одна таблица — один источник.
- ❌ Дублирует структуру `multimedia_objects` (storage_url, mime,
  caption, object_metadata, провенанс, soft-delete) ради single-source
  семантики.
- ❌ Каждый следующий source-адаптер (WikiTree photo, BillionGraves
  gravestone) тоже захочет свою таблицу — multiplier по таблицам растёт.
- ❌ Полиморфная связка с местами/событиями требует новой
  `place_images_entities` (или копии `entity_multimedia`) — ещё больше
  дублей.

### Вариант B — reuse `multimedia_objects` + `entity_multimedia`, source хранится в `provenance`

- ✅ `multimedia_objects` уже имеет всё нужное: `storage_url`,
  `object_metadata` (jsonb для специфики source'а), `provenance` (для
  source-tag'а + license/audit trail), TreeEntityMixins (soft-delete,
  versioning, status, confidence).
- ✅ `entity_multimedia` уже полиморфен — Place, Event, Person линкуются
  единым механизмом. Не надо плодить FK-таблицы.
- ✅ UI запрос «show all images for this place» — один JOIN, тот же
  что для GEDCOM-imported / user-uploaded media.
- ✅ Schema invariants без изменений: `multimedia_objects` — уже
  TreeEntity-table, `entity_multimedia` — уже SERVICE-table.
- ❌ Source-tag (`provenance.source = "wikimedia_commons"`) — слабее
  типизирован, чем колонка enum'а. Ловить по jsonb-ключу.
- ❌ `storage_url` семантически означал «где лежат байты в нашем
  storage'е»; для Commons мы не качаем байты, а ссылаемся на CDN.

### Вариант C — отдельный package, новый ImportSourceKind, новая ImportJob row на каждый fetch

- ✅ Audit-trail каждого fetch'а: row в `import_jobs` с stats
  (fetched/created/skipped).
- ❌ ImportJob тяжелее (status state machine, async-worker), а Wikimedia
  fetch — синхронный, single-HTTP, ~200мс. Overkill.
- ❌ Поощряет дрейф: каждый дальнейший source потребует вариант C +
  новый enum value, что плохо масштабируется.

## Решение

Выбран **Вариант B** — reuse `multimedia_objects` + `entity_multimedia`,
source-tag через `provenance.source = "wikimedia_commons"`.

### Архитектурные подробности

#### 1. `packages/wikimedia-commons-client` (новый)

Read-only async клиент MediaWiki Action API. Структура клонирована с
`packages/familysearch-client` (ADR-0011): `client.py`, `config.py`,
`errors.py`, `models.py`. Различия от FS-клиента:

| Аспект | FamilySearch | Wikimedia Commons |
|---|---|---|
| Auth | OAuth 2.0 PKCE | **Anonymous** (read-only sufficient) |
| User-Agent | дефолтный httpx | **Required, descriptive** (WMF policy) |
| Errors | AuthError + ... | без AuthError (нет токена); 401/403 → ClientError |
| Endpoints | per-resource RESTful | один `?action=query` с параметрами |

#### 2. Anonymous vs OAuth

WMF разрешает анонимные read-запросы; OAuth даёт более высокие per-IP
квоты и убирает captcha. Phase 9.1 use-case — десятки запросов на
дерево в день. Этого достаточно для anonymous read; OAuth-flow
добавит сложности (config + storage токенов) без proportional benefit.
Если будем bulk-fetch'ать по тысяче Place'ов — пересмотрим.

#### 3. User-Agent (mandatory)

Дефолт — `AutoTreeGen/0.1 (+https://github.com/AutoTreeGen/TreeGen;
autotreegen@gmail.com) parser-service/0.1`. Format:
`<client>/<version> (<contact>) <library>/<version>` per WMF policy.
Generic UA → 403 Forbidden. Конфигурируется через
`PARSER_SERVICE_WIKIMEDIA_USER_AGENT`; `WikimediaCommonsConfig`
валидирует non-empty в `__post_init__`.

#### 4. Retry — tenacity на 429/503 (как FS-клиент, ADR-0011)

3 попытки, exponential backoff с jitter, `1s → ~2s → ~4s`, потолок 30s.
Не-retryable ошибки (404, 4xx-кроме-429) поднимаются сразу.

#### 5. Search strategy

Два метода: `search_by_coordinates` (geosearch generator) и
`search_by_title` (search generator). Importer выбирает — есть ли у
Place'а lat/lon; иначе fallback на canonical_name. Первая стратегия
гораздо точнее; full-text — backup для Place'ов без координат.

#### 6. Provenance + `object_metadata` split

Каждый MultimediaObject row несёт **два jsonb-блока**:

- `provenance` — стабильный legal/audit trail. Поля: `source`,
  `commons_page_url` (= **дедуп-ключ**), `fetched_at`,
  `license_short_name`, `attribution_required`. **Не обновляется** при
  refresh — это исторический факт «кто сказал».
- `object_metadata` — UI-relevant полный набор (thumb_url, width,
  height, credit_html, license_url). **Обновляется** при refresh — это
  «что показывать» сейчас.

Зачем два блока: legal trail должен переживать refresh; UI-данные
устаревают (thumb-сервер ротирует CDN-URLs, credit может быть
переписан uploader'ом). Хранить и то, и то — единственный способ
честно совмещать compliance и actuality.

#### 7. Идемпотентность

Дедуп — по `provenance.commons_page_url` в пределах `tree_id`. Re-fetch
не дублирует MultimediaObject; importer возвращает stats со счётчиком
`skipped_existing`. Соль выбора `commons_page_url` (не `image_url`):
страница файла на Commons имеет стабильный URL даже при rename'е через
redirect, тогда как image URL может быть переброшен на другой CDN.

#### 8. No ImportJob row

Wikimedia fetch — синхронный single-HTTP, не bulk-import. Заводить
ImportJob под каждое нажатие кнопки «Fetch images» — дисбаланс. Audit
покрывается:

- `provenance.fetched_at` на каждой Multimedia row;
- существующий `audit_log` (TreeEntityMixins пишет on-update events).

Если Phase 9.4+ потребует bulk-fetch (все Place'ы дерева сразу), тогда
оборачиваем importer в arq-job + `ImportSourceKind.ARCHIVE_MATCH`.

#### 9. Endpoints

- `POST /trees/{tree_id}/places/{place_id}/wikimedia-fetch` — EDITOR.
  Тело пустое; `?limit=&radius_m=` опциональны. Ответ:
  `{place_id, search_strategy, fetched, created, skipped_existing}`.
- `GET /trees/{tree_id}/places/{place_id}/wikimedia-images` — VIEWER.
  Возвращает уже импортированные изображения с license/attribution для
  UI рендеринга. Read-only; не вызывает Commons.

Permission: запись через EDITOR (модифицирует tree-state); чтение —
VIEWER (existing pattern).

## Последствия

### Положительные

- Шаблон установлен: WikiTree (Phase 9.2), BillionGraves (Phase 9.3) и
  далее — клонируют `packages/wikimedia-commons-client/` структурно,
  адаптируют только мапперы CC/ToS-полей. Существенно ускоряет Phase 9.x.
- Schema без изменений — нет alembic migration, нет SERVICE_TABLES
  диффа. PR ниже планки <500 строк по продуктовому коду.
- License/attribution-trail в БД — auditable (можно ответить на
  «откуда эта картинка?» и «должна ли быть атрибуция?» одним SQL'ем).

### Отрицательные / стоимость

- Source-tag хранится в jsonb-ключе `provenance.source`, не в колонке.
  Запросы «все wikimedia изображения» делают index-on-jsonb-expression
  если станет узким местом (сейчас не блокер).
- `MultimediaObject.storage_url` теперь несёт **два** значения семантически:
  «байты в MinIO/GCS» (для uploaded) и «ссылка на CDN» (для external).
  Это не break, но подразумевает читать `provenance.source` перед
  download/proxying'ом. Фиксируется в ADR-0058 §«storage_url semantics
  clarification» (этот раздел).

### Риски

- WMF может ужесточить anonymous quotas — мониторим 429-rate, при
  необходимости переходим на OAuth (не блокер для текущего volume).
- Изменения в Action API формате — tolerant парсер (extra fields
  ignored, license missing → null) выживает большинство таких
  изменений; критичные поля (descriptionurl, url) защищены тестами.
- Image hotlinking от Commons CDN: WMF разрешает hotlink с правильной
  attribution. UI **должен** рендерить credit_html — фронт-сторона
  Phase 9.1b enforce'ит это.

### Что нужно сделать в коде

- ✅ `packages/wikimedia-commons-client/` (client + config + errors +
  models, 35 unit tests).
- ✅ `services/parser-service/services/wikimedia_importer.py` + 9
  integration tests с testcontainers Postgres.
- ✅ `services/parser-service/api/places.py` — два endpoint'а,
  permission-gated.
- ✅ Schema unchanged (verified by `test_no_unexpected_tables`).
- ⏭️ Phase 9.1b — frontend panel: place detail page (если будет
  заведена) + рендер credit_html с DOMPurify-санитизацией +
  `attribution_required` flag → инлайн-credit под изображением.

## Когда пересмотреть

- Если volume превысит anonymous-friendly (~50 req/min на IP) — OAuth.
- Если появится 4-й + source-адаптер с принципиально другой моделью
  attribution (например, BillionGraves с GPS-tag'ами как доменными
  фактами): возможно потребуется выделить `external_media`-под-таблицу
  multimedia_objects'а с typed-колонками. Сейчас (1 source) — overkill.
- Если Wikimedia Foundation deprecate'ит anonymous read access (нет
  публичных сигналов на 2026-04, но мониторим WMF blog).

## Ссылки

- Phase 9.0-pre research note: `docs/research/archive-integrations-2026.md`.
- Roadmap: `ROADMAP.md` §13 — Phase 9.x ordering.
- ADR-0011 — FamilySearch client design (template для retry/error
  hierarchy).
- ADR-0017 — FS import mapping (template для provenance pipeline).
- WMF policy: <https://foundation.wikimedia.org/wiki/Policy:User-Agent_policy>.
- Commons API: <https://commons.wikimedia.org/wiki/Commons:API>.
- MediaWiki Action API: <https://www.mediawiki.org/wiki/API:Main_page>.
