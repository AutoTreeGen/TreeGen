# Agent brief — Phase 3.5: Multimedia (OBJE/FILE) import

> **Кому:** Claude Code CLI с `--dangerously-skip-permissions` (bypass on).
> **Контекст:** Windows, `D:\Projects\TreeGen`. Worktree `../TreeGen-multimedia`.
> Не коллидирует: Agent 2 в `services/parser-service/api/hypotheses.py`,
> Agent 4 в `services/dna-service/`. Ты будешь в
> `packages/gedcom-parser/src/.../multimedia.py` +
> `services/parser-service/services/import_runner.py` (только секция
> multimedia, не трогай events/places).
> Перед стартом: `CLAUDE.md` §11, `docs/gedcom-extensions.md`,
> `docs/data-model.md`, GEDCOM 5.5.5 spec секция OBJE.

---

## Зачем

Реальные GED-файлы (Ancestry, MyHeritage) содержат **OBJE**-записи —
ссылки на фото, документы, аудио. Сейчас наш parser их игнорирует.
Это значит при import → DB → export round-trip фотографии теряются.
Нарушает `CLAUDE.md` §11 «Round-trip без потерь».

Фото — критичны для UX (Phase 4.x tree view хочет показывать аватары)
и для evidence-based генеалогии (документ = source).

---

## Что НЕ делать

- ❌ **Скачивать файлы** по URL. Только хранить ссылки. Скачивание =
  отдельная Phase 3.5.1, нужны квоты, S3, антивирус.
- ❌ Парсить EXIF / OCR. Это Phase 10 (LLM-aware) либо отдельная фаза.
- ❌ Хранить blob в БД. Только metadata + path/URL.
- ❌ Auto-resolve duplicate media (тот же файл в 5 GED'ах). Phase 3.5.2.
- ❌ `--no-verify`, прямой push в main.

---

## Задачи

### Task 1 — gedcom-parser: parse OBJE/FILE/FORM/TITL

**Файлы:**

- `packages/gedcom-parser/src/gedcom_parser/multimedia.py` (новый)
- `packages/gedcom-parser/src/gedcom_parser/models.py` (добавить
  `MultimediaRecord`, `MultimediaLink`)
- `packages/gedcom-parser/tests/test_multimedia.py`

**Что парсить (GEDCOM 5.5.5 + 5.5.1 + Ancestry/MyHeritage variants):**

```text
0 @M1@ OBJE
1 FILE relative/path/to/photo.jpg
2 FORM jpeg
3 TYPE photo
1 TITL Wedding 1923
1 _CREA 2023-04-12 (Ancestry)
```

И **inline OBJE** внутри INDI/FAM:

```text
0 @I1@ INDI
1 OBJE @M1@           # ссылка на запись
1 OBJE                # inline без ID
2 FILE photo.jpg
2 FORM jpeg
```

Поля `MultimediaRecord`:

- `xref_id: str | None` (`@M1@`)
- `files: list[MultimediaFile]` (FILE+FORM+TITL+TYPE)
- `notes: list[str]`
- `change_date: date | None`
- `provenance: dict` (raw record)

Поля `MultimediaFile`:

- `path: str` (raw FILE value, может быть относит. путь, URL, или Win-путь)
- `form: str | None` (jpeg, gif, pdf, …)
- `type: str | None` (photo, document, audio, …)
- `title: str | None`

Поля `MultimediaLink` (для INDI/FAM/SOUR/EVEN):

- `target_xref: str | None` (если ссылка на @M1@)
- `inline_record: MultimediaRecord | None` (если inline)

**Round-trip требование:** parsed → serialized снова даёт байт-в-байт
(или нормализованный) тот же OBJE-блок. Тест с golden fixture.

### Task 2 — shared-models: ORM

**Файл:** `packages/shared-models/src/.../orm.py`

Добавить:

```python
class MultimediaRecord(Base):
    __tablename__ = "multimedia_records"
    id, tree_id, gedcom_xref, title, change_date, provenance(jsonb),
    deleted_at  # soft delete

class MultimediaFile(Base):
    __tablename__ = "multimedia_files"
    id, record_id (FK), path, form, type, title, sha256 (nullable for now)

class MultimediaLink(Base):
    __tablename__ = "multimedia_links"
    id, record_id (FK), linked_table (person/family/source/event),
    linked_id (int), provenance
```

Alembic миграция в `infrastructure/alembic/versions/`. Indexes на
`(tree_id)`, `(linked_table, linked_id)`.

⚠ Watch: Agent 2 (Phase 7.2 Hypothesis ORM) и Agent 4 (Phase 6.2 Dna
ORM) тоже трогают `orm.py`. Перед commit обязательно
`git pull --rebase origin main`. Конфликт решается тривиально —
модели независимые.

### Task 3 — parser-service: import multimedia

**Файл:** `services/parser-service/src/parser_service/services/import_runner.py`

В существующем `import_runner` после events/places добавить блок
multimedia:

1. Bulk-insert `MultimediaRecord` для каждой OBJE-записи.
2. Bulk-insert `MultimediaFile` (1-to-many).
3. Создать `MultimediaLink` для каждого INDI/FAM/SOUR где встретился OBJE.
4. Обновить provenance существующих persons/families: добавить
   `media_record_ids: [...]` в их jsonb.

Тесты:

- import GED с 3 OBJE → 3 records, N files, M links.
- Round-trip: import → export → diff (только media-секция).
- INDI с 5 OBJE → 5 links на эту персону.

### Task 4 — Real-corpus check

GEDCOM_TEST_CORPUS=D:/Projects/GED содержит файлы от разных платформ.
Прогон:

```bash
GEDCOM_TEST_CORPUS=D:/Projects/GED uv run pytest \
    packages/gedcom-parser -m gedcom_real -k multimedia
```

Ожидание: minimum 3 различных GED файла парсятся без падений, хотя бы
1 из них содержит >= 100 OBJE.

Если падает на конкретном — в issue с reproducer (sanitized — без
персональных данных), skip с `pytest.xfail` и продолжай.

### Task 5 — Финал

1. `docs/gedcom-extensions.md` дополни секцией про OBJE variants
   (Ancestry _CREA, MyHeritage особенности).
2. ROADMAP §3.5 → done.
3. `pwsh scripts/check.ps1` green.
4. PR `feat/phase-3.5-multimedia-import`.
5. CI green до merge. Никакого `--no-verify`.

---

## Сигналы успеха

1. ✅ Round-trip OBJE без потерь на golden fixture.
2. ✅ ORM + миграция в main, тесты зелёные.
3. ✅ Real GED-corpus прогоняется (>= 3 файла) без crashes.
4. ✅ provenance персон обновляется с media_record_ids.
5. ✅ docs/gedcom-extensions.md дополнен.

---

## Если застрял

- Inline OBJE с разными вложениями → начни с самого распространённого
  паттерна (FILE+FORM+TITL), остальное — TODO в комментариях.
- Кодировки путей FILE (CP1251, UTF-16, raw bytes) → храни raw, нормализуй
  только при serialize обратно.
- Большие файлы corpus (150 МБ) тормозят тесты → метку `slow` на real-corpus
  тесты, в обычный pytest run не попадут.
- shared-models конфликт с Agent 2/4 → `git pull --rebase`, реши
  конфликт через объединение классов, push.

Удачи.
