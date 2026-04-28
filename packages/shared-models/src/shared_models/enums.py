"""Перечисления, используемые в ORM- и Pydantic-моделях.

Все enum'ы хранятся в БД как ``text`` (а не PostgreSQL ENUM): дешевле миграции,
проще миксовать новые значения, читаемо в дампах. Валидация — на уровне ORM/API.
"""

from __future__ import annotations

from enum import StrEnum


class EntityStatus(StrEnum):
    """Статус доменной записи в дереве.

    Применяется к persons, families, events, places и т. п.
    """

    CONFIRMED = "confirmed"
    PROBABLE = "probable"
    HYPOTHESIS = "hypothesis"
    REJECTED = "rejected"
    MERGED = "merged"


class TreeVisibility(StrEnum):
    """Видимость дерева для других пользователей."""

    PRIVATE = "private"
    SHARED = "shared"  # доступно по приглашению
    PUBLIC = "public"  # индексируется


class CollaboratorRole(StrEnum):
    """Роль соавтора дерева."""

    OWNER = "owner"
    EDITOR = "editor"
    VIEWER = "viewer"


class Sex(StrEnum):
    """GEDCOM SEX-тег.

    ``U`` — unknown, ``X`` — intersex/non-binary (расширение GEDCOM 7).
    """

    MALE = "M"
    FEMALE = "F"
    UNKNOWN = "U"
    OTHER = "X"


class NameType(StrEnum):
    """Тип имени (GEDCOM TYPE для NAME-структуры)."""

    BIRTH = "birth"
    MARRIED = "married"
    AKA = "aka"
    RELIGIOUS = "religious"
    HEBREW = "hebrew"
    NICKNAME = "nickname"
    OTHER = "other"


class EventType(StrEnum):
    """GEDCOM EVENT-теги, расширенные нашими типами.

    ``CUSTOM`` — для произвольных событий, конкретный тип в ``Event.custom_type``.
    """

    BIRTH = "BIRT"
    DEATH = "DEAT"
    MARRIAGE = "MARR"
    DIVORCE = "DIV"
    BAPTISM = "BAPM"
    CHRISTENING = "CHR"
    BURIAL = "BURI"
    CREMATION = "CREM"
    RESIDENCE = "RESI"
    EMIGRATION = "EMIG"
    IMMIGRATION = "IMMI"
    NATURALIZATION = "NATU"
    CENSUS = "CENS"
    OCCUPATION = "OCCU"
    EDUCATION = "EDUC"
    GRADUATION = "GRAD"
    MILITARY = "MILI"
    BAR_MITZVAH = "BARM"
    BAS_MITZVAH = "BASM"
    CONFIRMATION = "CONF"
    ADOPTION = "ADOP"
    ENGAGEMENT = "ENGA"
    ANNULMENT = "ANUL"
    CUSTOM = "CUSTOM"


class RelationType(StrEnum):
    """Тип связи ребёнок–семья."""

    BIOLOGICAL = "biological"
    ADOPTED = "adopted"
    FOSTER = "foster"
    STEP = "step"
    UNKNOWN = "unknown"


class SourceType(StrEnum):
    """Тип источника."""

    BOOK = "book"
    METRIC_RECORD = "metric_record"
    CENSUS = "census"
    GRAVESTONE = "gravestone"
    WEBSITE = "website"
    INTERVIEW = "interview"
    DNA_TEST = "dna_test"
    OTHER = "other"


class AuditAction(StrEnum):
    """Действие, зафиксированное в audit_log."""

    INSERT = "insert"
    UPDATE = "update"
    DELETE = "delete"
    RESTORE = "restore"
    MERGE = "merge"


class ActorKind(StrEnum):
    """Кто/что произвёл изменение."""

    USER = "user"
    SYSTEM = "system"
    IMPORT_JOB = "import_job"
    INFERENCE = "inference"


class ImportJobStatus(StrEnum):
    """Статус импорт-джоба.

    ``CANCELLED`` (Phase 3.5) — worker увидел ``cancel_requested=True``
    между стадиями и graceful-завершил импорт. Уже закоммиченные ряды
    остаются (если транзакция была commit'нута до cancel'а).
    """

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"
    CANCELLED = "cancelled"


class ImportSourceKind(StrEnum):
    """Тип источника для импорт-джоба."""

    GEDCOM = "gedcom"
    DNA_CSV = "dna_csv"
    ARCHIVE_MATCH = "archive_match"
    MANUAL = "manual"
    FAMILYSEARCH = "familysearch"


class DateQualifier(StrEnum):
    """GEDCOM date qualifier."""

    EXACT = "EXACT"
    ABOUT = "ABT"
    BEFORE = "BEF"
    AFTER = "AFT"
    ESTIMATED = "EST"
    CALCULATED = "CAL"
    BETWEEN = "BET"
    FROM_TO = "FROMTO"


class DateCalendar(StrEnum):
    """Календарь GEDCOM-даты."""

    GREGORIAN = "gregorian"
    JULIAN = "julian"
    HEBREW = "hebrew"
    FRENCH_REPUBLICAN = "french_r"


class DnaPlatform(StrEnum):
    """Платформа, с которой пришли DNA-данные."""

    ANCESTRY = "ancestry"
    MYHERITAGE = "myheritage"
    GEDMATCH = "gedmatch"
    FTDNA = "ftdna"
    TWENTY_THREE = "23andme"
    LIVING_DNA = "livingdna"
    DNAGEDCOM = "dnagedcom"
    OTHER = "other"


class DnaImportStatus(StrEnum):
    """Статус DNA-импорта (similar to ImportJobStatus)."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"


class DnaImportKind(StrEnum):
    """Тип CSV: список матчей, shared matches, segments и т.д."""

    MATCH_LIST = "match_list"
    SHARED_MATCHES = "shared_matches"
    SEGMENTS = "segments"
    KIT_SUMMARY = "kit_summary"


class EthnicityPopulation(StrEnum):
    """Популяция для endogamy-коррекции shared cM.

    Multiplier применяется к cM-значениям при оценке родства, чтобы скорректировать
    inflated-сегменты в endogamous-популяциях.
    """

    GENERAL = "general"  # multiplier = 1.0
    ASHKENAZI = "ashkenazi"  # multiplier ≈ 1.6 (Bettinger studies)
    SEPHARDI = "sephardi"  # multiplier ≈ 1.4
    AMISH = "amish"  # multiplier ≈ 2.0
    LDS_PIONEER = "lds_pioneer"  # multiplier ≈ 1.5


class HypothesisType(StrEnum):
    """Тип гипотезы о связи между двумя сущностями (Phase 7.2 persistence).

    Зеркалирует ``inference_engine.types.HypothesisType`` плюс расширяет
    его DUPLICATE_* для гипотез про non-person сущности (которые нельзя
    хранить как SAME_PERSON). Persistence-слой держит StrEnum здесь —
    inference-engine остаётся pure-functions без зависимости на shared-models.
    """

    SAME_PERSON = "same_person"
    PARENT_CHILD = "parent_child"
    SIBLINGS = "siblings"
    MARRIAGE = "marriage"
    DUPLICATE_SOURCE = "duplicate_source"
    DUPLICATE_PLACE = "duplicate_place"


class HypothesisReviewStatus(StrEnum):
    """Статус ручной проверки гипотезы пользователем (Phase 7.2 + 4.9).

    ``CONFIRMED``/``REJECTED`` — это user-judgment, не auto-merge.
    CLAUDE.md §5: подтверждение гипотезы НЕ мутирует доменные сущности.
    Слияние entities — отдельный явный flow (Phase 4.6 UI), отдельный
    endpoint, отдельная audit-log запись.

    ``DEFERRED`` (Phase 4.9): «вернусь позже» — отдельно от REJECTED.
    UI прячет из дефолтного pending-queue, но не считает отказом для
    metrics. ``person_merger`` не блокирует merge на DEFERRED (только
    REJECTED блокирует), что позволяет юзеру отложить и вернуться
    после сбора дополнительных evidence.
    """

    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    DEFERRED = "deferred"


class HypothesisSubjectType(StrEnum):
    """Тип сущности-субъекта гипотезы (полиморфные subject FK).

    То же семейство что у ``Citation.entity_type`` /
    ``EntityMultimedia.entity_type``: целостность на уровне приложения,
    БД хранит как text. Допустимый набор фиксируется здесь, чтобы
    UI и hypothesis_runner не разъезжались по строковым значениям.
    """

    PERSON = "person"
    FAMILY = "family"
    SOURCE = "source"
    PLACE = "place"


class HypothesisComputedBy(StrEnum):
    """Кто/что породил гипотезу.

    ``AUTOMATIC`` — bulk_compute_for_dedup_suggestions через
    inference-engine.
    ``MANUAL`` — user явно создал гипотезу через UI/API.
    ``IMPORTED`` — гипотеза пришла из external source (FamilySearch
    suggestions, Phase 5.x).
    """

    AUTOMATIC = "automatic"
    MANUAL = "manual"
    IMPORTED = "imported"


class NotificationEventType(StrEnum):
    """Тип события нотификации (Phase 8.0 — см. ADR-0024).

    Каждый тип — отдельный шаблон сообщения и отдельный idempotency
    namespace. Список расширяется по мере появления новых источников
    нотификаций; неизвестный type на ``POST /notify`` отвергается 400.
    """

    HYPOTHESIS_PENDING_REVIEW = "hypothesis_pending_review"
    DNA_MATCH_FOUND = "dna_match_found"
    IMPORT_COMPLETED = "import_completed"
    IMPORT_FAILED = "import_failed"
    MERGE_UNDONE = "merge_undone"
    DEDUP_SUGGESTION_NEW = "dedup_suggestion_new"


class MergeStrategy(StrEnum):
    """Стратегия слияния FS-импорта против существующего дерева (Phase 5.2).

    Решение принимает ``parser_service.services.fs_pedigree_merger.resolve_fs_person``
    до момента INSERT'а Person'а. Каждое решение записывается в
    ``fs_import_merge_attempts`` для последующего ручного аудита (CLAUDE.md §5).

    * ``SKIP`` — FS-person уже представлен в дереве (по ``fs_pid`` либо
      по high-confidence ID-match'у), Person'а **не** создаём, новые
      Names/Events не вставляем. Идемпотентный no-op.
    * ``MERGE`` — FS-person с ≥0.9 score'ом по entity-resolution
      совпадает с существующим локальным Person'ом. Не создаём новый
      Person, имена/события из FS вставляются под существующим
      ``matched_person_id`` (provenance этого Person'а получает FS-source
      attachment). Auto-merge запрещён CLAUDE.md §5 для Person'а как
      сущности — здесь мы не сливаем две Person-row, а **добавляем
      FS-evidence к уже существующей**, что классифицируется как
      «append source citations», а не cross-person merge.
    * ``CREATE_AS_NEW`` — score ниже high-threshold или вообще нет
      кандидатов. Создаём новый Person row с FS-provenance, как делал
      Phase 5.1 importer без merge-mode'а. Если score попал в
      mid-confidence коридор, attempt-row помечается ``needs_review=true``,
      чтобы UI Phase 4.5/4.6 предложил пару юзеру.
    """

    SKIP = "skip"
    MERGE = "merge"
    CREATE_AS_NEW = "create_as_new"


class HypothesisComputeJobStatus(StrEnum):
    """Статус bulk hypothesis-compute job (Phase 7.5).

    Lifecycle: ``QUEUED`` → ``RUNNING`` → ``SUCCEEDED``/``FAILED``/``CANCELLED``.

    ``QUEUED`` — job создан, ещё не стартовал (sync-mode моментально
    переходит в ``RUNNING``).
    ``RUNNING`` — обрабатывает batch'и, прогресс в ``progress.processed``.
    ``SUCCEEDED`` — все pairs обработаны.
    ``FAILED`` — exception в одном из batch'ей; ``error`` поле заполнено,
    предыдущие закоммиченные batch'и остаются.
    ``CANCELLED`` — user через PATCH /cancel, worker увидел флаг между
    batch'ами и остановился.
    """

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
