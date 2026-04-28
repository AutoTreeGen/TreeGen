# Agent brief — Phase 5.1: FamilySearch → parser-service integration

> **Кому:** Агент 3 (Claude Code CLI, bypass on) — продолжение после Phase 5.0.
> **Контекст:** Windows, `D:\Projects\TreeGen`.
> **Worktree:** используй существующий `TreeGen-fs-worktree` или создай новый.
> **Перед стартом:** `git checkout main && git pull`

---

## Контекст

Phase 5.0 закрыта твоей работой:

- `packages/familysearch-client/` готов с OAuth PKCE + `get_person` + retry
- ADR-0011 утверждён
- Mock-tests проходят (real API ждёт sandbox key — твой /schedule на 2 недели)

Phase 5.1 — **использовать client из parser-service** для импорта персон
из FS API в локальное дерево. Это первая cross-platform интеграция для
AutoTreeGen — пользователь сможет import'ить персон с FamilySearch
напрямую (не через GED export → upload).

**Параллельно работают:**

- Агент 1: `apps/web/` (Phase 4.3 tree viz)
- Агент 2: `packages/entity-resolution/` + `services/parser-service/services/dedup_finder.py`
- Агент 4: `packages/dna-analysis/` (Phase 6.1)
- Агент 5: `packages/gedcom-parser/` (Phase 1.x)
- Агент 6: `packages/inference-engine/` (Phase 7.0)

**Твоя территория:**

- `services/parser-service/src/parser_service/services/familysearch_importer.py` — новый
- `services/parser-service/src/parser_service/api/familysearch.py` — новый router
- `services/parser-service/src/parser_service/main.py` — register router (минимально, может конфликтовать с Агентами 1/2)
- `services/parser-service/src/parser_service/schemas.py` — добавить
  `FamilySearchImportRequest/Response` (НЕ конфликт с Агентами — разные классы)
- `services/parser-service/tests/test_familysearch_*.py` — новые
- `packages/familysearch-client/` — добавить `get_pedigree` если ещё нет
- `docs/adr/0017-familysearch-import-mapping.md` — новый ADR

**Что НЕ трогай:**

- `apps/web/`, `packages/dna-analysis/`, `packages/entity-resolution/`,
  `packages/gedcom-parser/`, `packages/inference-engine/` — другие агенты
- `services/parser-service/services/import_runner.py` — стабильный код Агента 2
- `services/parser-service/api/trees.py` — Агент 1 правит

---

## Цель Phase 5.1

1. **Маппинг FS Person → ORM Person** (с names, events, places)
2. **Endpoint** `POST /imports/familysearch` принимает `{access_token, person_id, depth=5}`
3. **Pedigree import** — рекурсивно тянуть N поколений предков
4. **Provenance:** каждая запись должна иметь `provenance.source = "familysearch"`
   - `provenance.fs_person_id` для traceability
5. **No DNA endpoints** — FS не expose'ит, ADR-0009 это зафиксировал

---

## Задачи (в этом порядке)

### Task 1 — docs(adr): ADR-0017 FS Person → ORM mapping

**Цель:** зафиксировать маппинг полей до кода.

**Шаги:**

1. `git checkout main && git pull`
2. `git checkout -b docs/adr-0017-familysearch-mapping`
3. Создать `docs/adr/0017-familysearch-import-mapping.md`:
   - Status: Accepted, Date: today
   - Tags: integration, familysearch, mapping, phase-5
   - Контекст: FS GEDCOM-X модели не 1:1 совпадают с нашим ORM
   - **Mapping table:**
     - FS `Person.id` → `Person.gedcom_xref` (с prefix `fs:`)
     - FS `Person.names[].nameForms[].fullText` → `Name.full_name`
     - FS `Person.gender.type` (`http://gedcomx.org/Male`) → `Person.sex`
     - FS `Person.facts[]` (BIRTH/DEATH/MARRIAGE) → `Event.event_type`
     - FS `Fact.date.original` → `Event.date_raw`
     - FS `Fact.place.original` → `Place` lookup или create
     - FS `Person.living` → `Person.status` (если living, status может быть HYPOTHETICAL)
   - **Provenance schema:**

     ```json
     {
       "source": "familysearch",
       "fs_person_id": "ABCD-123",
       "fs_url": "https://www.familysearch.org/tree/person/details/ABCD-123",
       "imported_at": "2026-04-27T...",
       "import_job_id": "..."
     }
     ```

   - **Conflict resolution** (когда impórt уже существующего FS-id):
     - Если existing person с тем же `provenance.fs_person_id` — update
       (refresh)
     - Иначе — create new
     - **НЕТ автоматического merge** с другими persons (CLAUDE.md §5)
   - **Rate limiting:** FS limit 100 req/min для personal apps. Используй
     tenacity с backoff (уже есть в client).
   - Когда пересмотреть: появится bulk download endpoint у FS, или
     scope expanded на sources/multimedia
4. `pwsh scripts/check.ps1` зелёное
5. Commit, push, PR

### Task 2 — feat(familysearch-client): get_pedigree endpoint

**Цель:** В client добавить `get_pedigree(person_id, generations=5)`.

**Шаги:**

1. `feat/phase-5.1-fs-pedigree`
2. В `packages/familysearch-client/src/familysearch_client/client.py`:

   ```python
   async def get_pedigree(
       self,
       person_id: str,
       generations: int = 5,
   ) -> FsPedigreeTree:
       """GET /platform/tree/persons/{person_id}/ancestry?generations=N.

       Returns nested ancestor tree.
       """
   ```

3. Pydantic model `FsPedigreeTree`:

   ```python
   class FsPedigreeNode(BaseModel):
       person: FsPerson
       father: "FsPedigreeNode | None" = None
       mother: "FsPedigreeNode | None" = None
   ```

4. Mock-тесты с pytest-httpx (sample FS pedigree response)
5. `pwsh scripts/check.ps1` зелёное
6. Commit, push, PR

### Task 3 — feat(parser-service): familysearch_importer service

**Цель:** Pure-function importer FS Person → ORM rows.

**Шаги:**

1. `feat/phase-5.1-fs-importer-service`
2. `services/parser-service/src/parser_service/services/familysearch_importer.py`:

   ```python
   async def import_fs_person(
       session: AsyncSession,
       access_token: str,
       fs_person_id: str,
       tree_id: UUID,
       owner_user_id: UUID,
       depth: int = 5,
   ) -> ImportJob:
       """Fetch FS person + N generations ancestors, insert into ORM."""
       client = FamilySearchClient(access_token=access_token)
       pedigree = await client.get_pedigree(fs_person_id, generations=depth)

       # Walk tree, dedupe by fs_person_id, build person/name/event rows
       # Bulk insert (same pattern как import_runner)
       # Provenance с source="familysearch"
   ```

3. Внутри:
   - `_fs_to_person_row(fs_person, tree_id, job_id)` — pure function
   - `_fs_to_name_rows(fs_person)` — names list
   - `_fs_to_event_rows(fs_person, person_id)` — birth/death/marriage events
   - `_fs_place_to_place_id(fs_place_text, tree_id, places_cache)` — lookup-or-create
   - Audit-skip pattern как у Агента 2's import_runner
4. Тесты с mock'нутым FS pedigree:
   - test_import_single_person_creates_person_with_provenance
   - test_import_pedigree_5_generations_creates_31_persons_max
   - test_existing_fs_person_id_updates_not_duplicates
   - test_provenance_includes_fs_url
5. `pwsh scripts/check.ps1` зелёное
6. Commit, push, PR

### Task 4 — feat(api): POST /imports/familysearch endpoint

**Цель:** REST endpoint.

**Шаги:**

1. `feat/phase-5.1-fs-import-api`
2. `services/parser-service/src/parser_service/api/familysearch.py`:

   ```python
   @router.post("/imports/familysearch", response_model=ImportJobResponse)
   async def import_familysearch(
       request: FamilySearchImportRequest,
       session: AsyncSession = Depends(get_session),
   ) -> ImportJobResponse:
       """Import person from FamilySearch with pedigree."""
   ```

3. `FamilySearchImportRequest` в `schemas.py`:

   ```python
   class FamilySearchImportRequest(BaseModel):
       access_token: str = Field(..., min_length=10)
       fs_person_id: str = Field(..., pattern=r"^[A-Z0-9-]+$")
       tree_id: UUID
       depth: int = Field(default=5, ge=1, le=10)
   ```

4. **Security note:** access_token приходит от user (after OAuth flow на их стороне).
   Mы НЕ храним access_token. Логируем только sha256[:8] для traceability.
5. Register router в `main.py` (одна строка `app.include_router(familysearch.router)`)
6. Тесты с mock FS API:
   - test_post_familysearch_import_success
   - test_invalid_fs_person_id_returns_422
   - test_missing_access_token_returns_422
   - test_fs_api_401_returns_401_to_user
7. `pwsh scripts/check.ps1` зелёное
8. Commit, push, PR

### Task 5 (опционально) — docs(roadmap): mark Phase 5.1 done

После всех 4 PR:

- ROADMAP § (FS section) update
- README parser-service: добавить "FamilySearch import" секцию

---

## Что НЕ делать

- ❌ **Хранить access tokens** в БД — security risk. User передаёт каждый раз.
- ❌ **Auto-merge** с existing persons (CLAUDE.md §5) — только update по
  fs_person_id match
- ❌ **Bulk import всего FS дерева** — start с pedigree depth ≤ 10
- ❌ **DNA endpoints** — FS не expose'ит (ADR-0009)
- ❌ **Memory upload** к FS — Phase 5.3 если будет write-direction
- ❌ Трогать пакеты других агентов
- ❌ `git commit --no-verify`
- ❌ Мердж с красным CI

---

## Сигналы успеха

После 4 PR:

1. ✅ ADR-0017 в `docs/adr/`
2. ✅ `client.get_pedigree()` работает (mock-tested)
3. ✅ `import_fs_person()` создаёт persons + names + events с
   provenance.source="familysearch"
4. ✅ `POST /imports/familysearch` работает (mock-tested)
5. ✅ Re-import same fs_person_id → update, не duplicate
6. ✅ Все CI зелёные

---

## Coordination

- Корневой `pyproject.toml` / `uv.lock` — rebase + uv lock при конфликте
- `services/parser-service/main.py` — Агент 1 (трогает для tree viz endpoint)
  и Агент 2 (трогает для dedup endpoint) тоже могут править. Если конфликт —
  взять обе строки `app.include_router(...)`
- `schemas.py` — Агент 1 добавляет `AncestorTreeNode`, Агент 2 добавляет
  `DuplicateSuggestion`, ты добавляешь `FamilySearchImportRequest/Response`.
  Разные классы, конфликт минимальный.
- Worktree isolation: используй уже существующий `TreeGen-fs-worktree`
  или новый

Удачи. После Phase 5.1 у пользователя будет первая cross-platform import
функция: paste FS person ID → 5 поколений предков в локальном дереве с
full provenance. **Это уникально** — Ancestry/MyHeritage не дают такого
вне walled garden.
