# Agent brief — Phase 3.6: Source + Citation evidence ingestion

> **Кому:** Claude Code CLI с `--dangerously-skip-permissions` (bypass on).
> **Контекст:** Windows, `D:\Projects\TreeGen`. Worktree `../TreeGen-sources`.
> Это естественное продолжение твоей PR #73 (Citation sub-tags exposed
> в parser). Теперь — **сохраняем эти sub-tags в БД** как
> first-class evidence.
> Перед стартом: `CLAUDE.md` §3 (evidence-first), своя PR #73, GEDCOM
> 5.5.5 секция SOUR + CITATION.

---

## Зачем

CLAUDE.md §3 пункт 1: "Evidence-first. Каждое утверждение → источник +
confidence + provenance." Сейчас parser **видит** SOUR/PAGE/QUAY,
но не сохраняет в БД. Это первая фаза, где архитектурный принцип
становится реальным кодом.

После 3.6 любая person/event/family запись имеет ссылку на
конкретное цитирование (страница книги, ссылка на архив, фото
свидетельства), и frontend сможет показать «откуда мы это знаем».

---

## Что НЕ делать

- ❌ Не парсить тело текста источников (LLM-задача → Phase 10).
- ❌ Не нормализовать архивы автоматически (Phase 5.x — historian-агент).
- ❌ Не auto-merge дубликаты source records. Phase 3.6.1.
- ❌ Не вычислять composite confidence ещё (это inference engine задача).
- ❌ `--no-verify`, прямой push в main.

---

## Задачи

### Task 1 — shared-models: Source + EvidenceCitation ORM

**Файл:** `packages/shared-models/src/.../orm.py`

```python
class Source(Base):
    __tablename__ = "sources"
    id, tree_id, gedcom_xref (e.g. "@S1@"), title, author, publication,
    repo_xref, abbreviation, text (полное TEXT поле из 1 TEXT subrecord),
    provenance (jsonb), created_at, updated_at, deleted_at

class EvidenceCitation(Base):
    __tablename__ = "evidence_citations"
    id, source_id (FK), 
    page (PAGE — "p. 123, line 4"),
    quay (QUAY — 0/1/2/3 GEDCOM quality),
    even (EVEN — какое событие подтверждает),
    role (ROLE — например "Witness"),
    note (NOTE),
    text_excerpt (TEXT — цитата из источника, если есть),
    provenance (jsonb)

class CitationLink(Base):
    """Связь Citation → Person/Family/Event."""
    __tablename__ = "citation_links"
    id, citation_id (FK), 
    linked_table ("person"|"family"|"event"|"name"|"fact"),
    linked_id (int),
    confidence (float, 0..1, derive из quay по умолчанию),
    provenance (jsonb)
```

Indexes:

- `(tree_id)` на sources
- `(source_id)` на evidence_citations
- `(linked_table, linked_id)` на citation_links
- `(citation_id)` на citation_links

Alembic миграция в `infrastructure/alembic/versions/`. Тестируй
upgrade + downgrade на чистой БД.

⚠ Watch: Agent 2 (Hypothesis ORM), Agent 3 (multimedia ORM), Agent 4
(DNA ORM) тоже трогают `orm.py`. Перед commit обязательно
`git pull --rebase origin main` и резолвить конфликты — модели
независимые, конфликт чисто текстовый.

### Task 2 — parser-service: import sources + citations

**Файл:** `services/parser-service/src/parser_service/services/import_runner.py`

Используя данные из gedcom-parser (которые твой PR #73 уже
выставил в API):

1. Bulk-insert `Source` для каждого `0 @S?@ SOUR` record в GEDCOM.
2. Для каждого CITATION (любая `2 SOUR @Sx@` под INDI/FAM/EVEN):
   - Создать `EvidenceCitation` (page, quay, even, role, note).
   - Создать `CitationLink` указывающий на person/family/event.
3. `quay` → `confidence` mapping:
   - 3 (direct primary) → 0.95
   - 2 (secondary) → 0.7
   - 1 (questionable) → 0.4
   - 0 (unreliable) → 0.1
   - missing → 0.5 (default unknown)

Тесты в `tests/test_source_citation_import.py`:

- GED с 5 SOUR → 5 sources в БД.
- INDI с 3 SOUR refs → 3 evidence_citations + 3 citation_links.
- QUAY 3 → confidence 0.95.
- Round-trip: import → export → diff (только sources секция, остальное
  можно за scope этого теста).
- Real corpus smoke (`-m gedcom_real`): >= 2 файла из `D:/Projects/GED`
  парсятся без crash.

### Task 3 — Lightweight API endpoint

**Файл:** `services/parser-service/src/parser_service/api/sources.py` (новый)

```text
GET /trees/{id}/sources                  → пагинированный список
GET /sources/{id}                        → детали + linked entities
GET /persons/{id}/citations              → все citations для персоны
```

Response shape — простой:

```json
{
  "id": 1,
  "title": "Russia, Pinkasei Kehilot",
  "page": "p. 123, line 4",
  "quay": 2,
  "confidence": 0.7,
  "linked": {"table": "person", "id": 42}
}
```

Тесты в `tests/test_sources_api.py`: 3 базовых happy-path.

### Task 4 — Финал

1. ROADMAP §3.6 → done.
2. `pwsh scripts/check.ps1` green.
3. PR `feat/phase-3.6-source-citation-evidence`.
4. CI green до merge. Никакого `--no-verify`.
5. PR description: что добавилось, как frontend сможет это использовать
   в будущем (Phase 4.7 — source viewer).

---

## Сигналы успеха

1. ✅ ORM + миграция в main.
2. ✅ На моём GED (D:/Projects/GED) parsed sources > 0, citation_links > 0.
3. ✅ QUAY → confidence mapping работает.
4. ✅ API endpoints возвращают данные.
5. ✅ Round-trip sources секция без потерь.

---

## Если застрял

- shared-models конфликт → `git pull --rebase`, объединить классы.
- Real GED содержит SOUR в неожиданном формате (legacy/Ancestry) →
  graceful skip с warning лог, не падай.
- Provenance jsonb непонятно что хранить → минимум `import_job_id`
  и `gedcom_line_number` (для дебага).

Удачи.
