# Модель данных

> **Статус:** Phase 2 MVP. Покрывает entities/management/audit. DNA, hypotheses, embeddings — отдельные миграции в своих фазах.
>
> Определения в коде: `packages/shared-models/src/shared_models/orm/`.

---

## 1. Принципы

- Все доменные записи дерева версионируются: **audit-log + soft delete** (см. ADR-0003).
- На каждой записи обязательны: `id`, `tree_id`, `confidence_score`,
  `status`, `provenance`, `version_id`, `created_at`, `updated_at`, `deleted_at`.
- `status ∈ {confirmed, probable, hypothesis, rejected, merged}`.
- `provenance` — `jsonb`: `{source_files: [...], import_job_id: "...", manual_edits: [...]}`.
- Все PK — `uuid` (генерируется в приложении через `uuid7` для естественной упорядоченности).
- Все FK имеют явные `ondelete` стратегии (см. таблицу ниже).
- Имена идентификаторов БД — snake_case, `English`. Названия таблиц во множественном числе.

---

## 2. Каскадные правила (ondelete)

| Связь | Стратегия | Обоснование |
|---|---|---|
| `tree → *` | `RESTRICT` | Дерево нельзя удалить, пока есть данные. Hard delete — отдельный flow с очисткой. |
| `user → trees.owner_user_id` | `RESTRICT` | Защита от случайного удаления аккаунта владельца дерева. |
| `person → names` | `CASCADE` | Имена не существуют вне персоны. |
| `family → events` (m2m через event_participants) | `RESTRICT` | События ссылаются на семьи, разрыв связи — явное действие. |
| `person → families.husband_id/wife_id` | `SET NULL` | Семья остаётся как структура, даже если супруг удалён. |
| `family → person.child_in_family` (m2n через family_children) | `CASCADE` (на стороне линка) | Удаление семьи разрывает ссылки на детей, но детей не удаляет. |
| `import_job → entities (через provenance)` | без FK, ссылка в jsonb | Чтобы удаление import_job не каскадило по сущностям. |
| `place → place_aliases` | `CASCADE` | Алиасы — часть места. |

---

## 3. Группы таблиц (Phase 2 scope)

### 3.1 Управление

`users`, `trees`, `tree_collaborators`, `import_jobs`, `audit_log`, `versions`.

### 3.2 Сущности дерева

`persons`, `names`, `families`, `family_children`, `events`, `event_participants`,
`places`, `place_aliases`, `sources`, `citations`, `notes`, `multimedia_objects`,
`entity_notes`, `entity_multimedia`.

### 3.3 Phase 6 (DNA) — отдельная миграция

`dna_kits`, `dna_matches`, `shared_matches`, `clusters`, `cluster_members`,
`chromosome_segments`, `person_kit_links`.

### 3.4 Phase 8 (гипотезы) — отдельная миграция

`hypotheses`, `hypothesis_evidence`, `evidence_artifacts`, `confidence_scores`.

### 3.5 Phase 10 (векторы) — отдельная миграция

`person_embeddings`, `place_embeddings`, `document_embeddings` (pgvector).

---

## 4. ER-диаграмма (Phase 2 MVP)

```mermaid
erDiagram
    USERS ||--o{ TREES : "owns"
    USERS ||--o{ TREE_COLLABORATORS : "is"
    TREES ||--o{ TREE_COLLABORATORS : "has"
    TREES ||--o{ PERSONS : "contains"
    TREES ||--o{ FAMILIES : "contains"
    TREES ||--o{ EVENTS : "contains"
    TREES ||--o{ PLACES : "contains"
    TREES ||--o{ SOURCES : "contains"
    TREES ||--o{ NOTES : "contains"
    TREES ||--o{ MULTIMEDIA_OBJECTS : "contains"
    TREES ||--o{ IMPORT_JOBS : "has"
    TREES ||--o{ AUDIT_LOG : "logs"
    TREES ||--o{ VERSIONS : "snapshots"

    PERSONS ||--o{ NAMES : "has"
    PERSONS ||--o{ EVENT_PARTICIPANTS : "in"
    EVENTS ||--o{ EVENT_PARTICIPANTS : "involves"
    EVENTS }o--|| PLACES : "at"

    FAMILIES }o--o| PERSONS : "husband"
    FAMILIES }o--o| PERSONS : "wife"
    FAMILIES ||--o{ FAMILY_CHILDREN : "lists"
    PERSONS ||--o{ FAMILY_CHILDREN : "child_in"
    FAMILIES ||--o{ EVENT_PARTICIPANTS : "in"

    PLACES ||--o{ PLACE_ALIASES : "aka"

    SOURCES ||--o{ CITATIONS : "cited_by"
    PERSONS ||--o{ CITATIONS : "claims"
    EVENTS ||--o{ CITATIONS : "claims"
    FAMILIES ||--o{ CITATIONS : "claims"

    PERSONS ||--o{ ENTITY_NOTES : "annotated"
    FAMILIES ||--o{ ENTITY_NOTES : "annotated"
    EVENTS ||--o{ ENTITY_NOTES : "annotated"
    NOTES ||--o{ ENTITY_NOTES : "applied_to"

    PERSONS ||--o{ ENTITY_MULTIMEDIA : "has_media"
    EVENTS ||--o{ ENTITY_MULTIMEDIA : "has_media"
    MULTIMEDIA_OBJECTS ||--o{ ENTITY_MULTIMEDIA : "linked_to"

    IMPORT_JOBS ||--o{ AUDIT_LOG : "produced"
    USERS ||--o{ AUDIT_LOG : "by"

    USERS {
        uuid id PK
        text email UK
        text external_auth_id UK "Clerk/Auth0 sub"
        text display_name
        text locale "en|ru|..."
        timestamptz created_at
        timestamptz updated_at
        timestamptz deleted_at
    }

    TREES {
        uuid id PK
        uuid owner_user_id FK
        text name
        text description
        text visibility "private|shared|public"
        text default_locale
        jsonb settings
        jsonb provenance
        bigint version_id
        timestamptz created_at
        timestamptz updated_at
        timestamptz deleted_at
    }

    TREE_COLLABORATORS {
        uuid id PK
        uuid tree_id FK
        uuid user_id FK
        text role "owner|editor|viewer"
        timestamptz added_at
    }

    PERSONS {
        uuid id PK
        uuid tree_id FK
        text gedcom_xref "@I123@ из исходника"
        text sex "M|F|U|X"
        text status "confirmed|probable|hypothesis|rejected|merged"
        float confidence_score
        uuid merged_into_person_id FK "nullable, для status=merged"
        jsonb provenance
        bigint version_id
        timestamptz created_at
        timestamptz updated_at
        timestamptz deleted_at
    }

    NAMES {
        uuid id PK
        uuid person_id FK
        text given_name
        text surname
        text prefix
        text suffix
        text nickname
        text patronymic
        text maiden_surname
        text name_type "birth|married|aka|religious|hebrew"
        text script "latin|cyrillic|hebrew|yiddish|polish"
        text romanized "ASCII транслитерация для поиска"
        int sort_order
        timestamptz created_at
        timestamptz updated_at
        timestamptz deleted_at
    }

    FAMILIES {
        uuid id PK
        uuid tree_id FK
        text gedcom_xref
        uuid husband_id FK "nullable"
        uuid wife_id FK "nullable"
        text status
        float confidence_score
        jsonb provenance
        bigint version_id
        timestamptz created_at
        timestamptz updated_at
        timestamptz deleted_at
    }

    FAMILY_CHILDREN {
        uuid id PK
        uuid family_id FK
        uuid child_person_id FK
        text relation_type "biological|adopted|foster|step|unknown"
        int birth_order
        timestamptz created_at
    }

    EVENTS {
        uuid id PK
        uuid tree_id FK
        text event_type "BIRT|DEAT|MARR|RESI|EMIG|IMMI|CHR|BURI|...|CUSTOM"
        text custom_type "если event_type=CUSTOM"
        uuid place_id FK "nullable"
        text date_raw "оригинальная GEDCOM-фраза"
        date date_start "распарсенный нижний край"
        date date_end "распарсенный верхний край"
        text date_qualifier "ABT|BEF|AFT|EST|CAL|BET|FROM..TO"
        text date_calendar "gregorian|julian|hebrew|french_r"
        text description
        float confidence_score
        text status
        jsonb provenance
        bigint version_id
        timestamptz created_at
        timestamptz updated_at
        timestamptz deleted_at
    }

    EVENT_PARTICIPANTS {
        uuid id PK
        uuid event_id FK
        uuid person_id FK "nullable"
        uuid family_id FK "nullable"
        text role "principal|witness|godparent|officiant|other"
    }

    PLACES {
        uuid id PK
        uuid tree_id FK
        text canonical_name "наиболее официальное современное"
        text country_code_iso "RU|PL|LT|UA|BY|..."
        text admin1 "губерния/область/штат"
        text admin2 "уезд/повет/район"
        text settlement
        float latitude
        float longitude
        date historical_period_start "когда название/границы валидны"
        date historical_period_end
        jsonb provenance
        bigint version_id
        timestamptz created_at
        timestamptz updated_at
        timestamptz deleted_at
    }

    PLACE_ALIASES {
        uuid id PK
        uuid place_id FK
        text name
        text language "ru|pl|lt|yi|he|..."
        text script
        text romanized
        date valid_from
        date valid_to
        text note "Wilno/Vilna/Vilnius/Вильно — все варианты"
    }

    SOURCES {
        uuid id PK
        uuid tree_id FK
        text title
        text author
        text publication
        text source_type "book|metric_record|census|gravestone|website|interview|dna_test"
        text repository "архив, библиотека"
        text repository_id "идентификатор внутри архива"
        text url
        date publication_date
        jsonb provenance
        bigint version_id
        timestamptz created_at
        timestamptz updated_at
        timestamptz deleted_at
    }

    CITATIONS {
        uuid id PK
        uuid tree_id FK
        uuid source_id FK
        text entity_type "person|family|event"
        uuid entity_id
        text page_or_section
        text quoted_text
        float quality "0..1, оценка надёжности этой цитаты"
        text note
        jsonb provenance
        timestamptz created_at
        timestamptz updated_at
        timestamptz deleted_at
    }

    NOTES {
        uuid id PK
        uuid tree_id FK
        text body
        text content_type "text/plain|text/markdown"
        text language
        jsonb provenance
        timestamptz created_at
        timestamptz updated_at
        timestamptz deleted_at
    }

    ENTITY_NOTES {
        uuid id PK
        uuid note_id FK
        text entity_type
        uuid entity_id
    }

    MULTIMEDIA_OBJECTS {
        uuid id PK
        uuid tree_id FK
        text object_type "image|document|audio|video|pdf"
        text storage_url "minio://... или gs://..."
        text mime_type
        bigint size_bytes
        text sha256
        text caption
        date taken_date
        jsonb metadata "EXIF, OCR результат, перевод"
        jsonb provenance
        timestamptz created_at
        timestamptz updated_at
        timestamptz deleted_at
    }

    ENTITY_MULTIMEDIA {
        uuid id PK
        uuid multimedia_id FK
        text entity_type
        uuid entity_id
        text role "primary|secondary|signature|...
    }

    IMPORT_JOBS {
        uuid id PK
        uuid tree_id FK
        uuid created_by_user_id FK
        text source_kind "gedcom|dna_csv|archive_match|manual"
        text source_filename
        bigint source_size_bytes
        text source_sha256 "идемпотентность по хешу"
        text status "queued|running|succeeded|failed|partial"
        jsonb stats "{persons: N, families: M, errors: K, warnings: ...}"
        jsonb errors "массив структурированных ошибок"
        timestamptz started_at
        timestamptz finished_at
        timestamptz created_at
    }

    AUDIT_LOG {
        uuid id PK
        uuid tree_id FK
        text entity_type
        uuid entity_id
        text action "insert|update|delete|restore|merge"
        uuid actor_user_id FK "nullable"
        text actor_kind "user|system|import_job|inference"
        uuid import_job_id FK "nullable"
        text reason
        jsonb diff "{before:{}, after:{}, fields:[]}"
        timestamptz created_at
    }

    VERSIONS {
        uuid id PK
        uuid tree_id FK
        text entity_type
        uuid entity_id
        jsonb snapshot "полный JSON состояния"
        text reason "import_pre_apply|manual_checkpoint|nightly_rolling"
        uuid created_by_user_id FK "nullable"
        timestamptz created_at
    }
```

---

## 5. Индексы (минимум для MVP)

| Таблица | Индекс | Назначение |
|---|---|---|
| `users` | `email` UNIQUE, `external_auth_id` UNIQUE | login lookup |
| `trees` | `(owner_user_id, deleted_at)` | список деревьев пользователя |
| `persons` | `(tree_id, deleted_at)`, `gedcom_xref` per tree, `merged_into_person_id` | основные обходы |
| `names` | `(person_id)`, GIN по `romanized` через pg_trgm | fuzzy search по именам |
| `families` | `(tree_id, deleted_at)`, `husband_id`, `wife_id` | связи |
| `family_children` | `(family_id)`, `(child_person_id)` | bidirectional walk |
| `events` | `(tree_id, deleted_at)`, `(event_type, date_start)`, `place_id` | timeline + map views |
| `event_participants` | `(event_id)`, `(person_id)`, `(family_id)` | граф участия |
| `places` | `(tree_id)`, GIN по `canonical_name`, `(latitude, longitude)` | geo queries |
| `place_aliases` | `(place_id)`, GIN по `romanized` | поиск по alias |
| `sources` | `(tree_id)` | список |
| `citations` | `(source_id)`, `(entity_type, entity_id)` | обратные ссылки |
| `audit_log` | `(tree_id, created_at DESC)`, `(entity_type, entity_id, created_at DESC)` | хронология |
| `versions` | `(tree_id, entity_type, entity_id, created_at DESC)` | restore lookup |
| `import_jobs` | `(tree_id, status, created_at DESC)`, `source_sha256` | идемпотентность |

В проде партиционирование `audit_log` по `tree_id` (hash) + по `created_at` (range на год).

---

## 6. Версионирование

См. **ADR-0003**. На старте — audit-log + soft delete + опциональные снапшоты в `versions`.
Переход к bi-temporal — точечно для гипотез в Phase 8.

Запись в `audit_log` — через SQLAlchemy `before_flush` event listener в той же транзакции.

---

## 7. Бенчмарки (целевые)

- Вставка 100 000 персон < 60 сек (bulk-режим, audit отключён или batched).
- Запрос предков 10 поколений < 200 мс (recursive CTE с индексом по `family_children`).
- Audit-вставка не более +20% к write-latency для одиночных операций.

---

## 8. Открытые вопросы (отдельные ADR)

- ADR-0004: pgvector vs внешний vector store при scale > 10M эмбеддингов.
- ADR-0006: хранение DNA-сегментов (БД vs Cloud Storage + кэш).
- ADR-0009: схема evidence-graph для гипотез (Phase 8).
