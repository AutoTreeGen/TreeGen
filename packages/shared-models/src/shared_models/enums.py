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
    """Роль соавтора дерева.

    Историческое имя из Phase 1 schema (legacy ``tree_collaborators``-таблица,
    осталась пустой). Новый код Phase 11.0+ использует :class:`TreeRole` —
    значения идентичны (``owner|editor|viewer``).
    """

    OWNER = "owner"
    EDITOR = "editor"
    VIEWER = "viewer"


class TreeRole(StrEnum):
    """Роль пользователя в дереве (Phase 11.0).

    Используется в ``tree_memberships`` и ``tree_invitations``. Значения —
    те же строки что у legacy :class:`CollaboratorRole`, чтобы DB-уровень
    был совместим (один enum-набор в схеме «text», без миграции колонки).

    Иерархия (для permission gate'ов): ``OWNER`` ⊃ ``EDITOR`` ⊃ ``VIEWER``.
    Используй :func:`role_satisfies` для сравнения.
    """

    OWNER = "owner"
    EDITOR = "editor"
    VIEWER = "viewer"


# Числовая шкала, которой sort'ится TreeRole для сравнения «role >= required».
# Изолирована от StrEnum.value чтобы не привязываться к строковому порядку.
_TREE_ROLE_RANK: dict[str, int] = {
    TreeRole.VIEWER.value: 1,
    TreeRole.EDITOR.value: 2,
    TreeRole.OWNER.value: 3,
}


def role_satisfies(actual: TreeRole | str, required: TreeRole | str) -> bool:
    """Проверить, удовлетворяет ли ``actual`` минимальной требуемой роли ``required``.

    Принимает и :class:`TreeRole`, и raw-строку из БД (DB хранит как text).
    Возвращает ``False`` для незнакомых значений — fail-closed.
    """
    a = actual.value if isinstance(actual, TreeRole) else str(actual)
    r = required.value if isinstance(required, TreeRole) else str(required)
    a_rank = _TREE_ROLE_RANK.get(a)
    r_rank = _TREE_ROLE_RANK.get(r)
    if a_rank is None or r_rank is None:
        return False
    return a_rank >= r_rank


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
    """Действие, зафиксированное в audit_log.

    Базовые domain-значения (``INSERT/UPDATE/DELETE/RESTORE/MERGE``) пишет
    автоматический listener в :mod:`shared_models.audit` для записей дерева.

    Phase 4.11a добавил user-level GDPR-action'ы (``EXPORT_*``,
    ``ERASURE_REQUESTED``). Они записываются вручную из worker'а /
    endpoint'а с ``tree_id=NULL`` (см. ADR-0046, миграция 0021): GDPR-
    запросы пользователя не привязаны к конкретному дереву, поэтому
    auto-listener (который требует ``tree_id``) их не видит.
    """

    INSERT = "insert"
    UPDATE = "update"
    DELETE = "delete"
    RESTORE = "restore"
    MERGE = "merge"

    # Phase 4.11a — GDPR right-of-access / right-of-portability (Art. 15/20).
    EXPORT_REQUESTED = "export_requested"
    EXPORT_PROCESSING = "export_processing"
    EXPORT_COMPLETED = "export_completed"
    EXPORT_FAILED = "export_failed"

    # Phase 4.11b/c — GDPR right-of-erasure (Art. 17). Stub только request-side
    # (Phase 4.10b создаёт user_action_request); processing — Phase 4.11b worker.
    ERASURE_REQUESTED = "erasure_requested"
    ERASURE_PROCESSING = "erasure_processing"
    ERASURE_COMPLETED = "erasure_completed"
    ERASURE_FAILED = "erasure_failed"
    # Phase 4.11b: блокирующий edge-case (shared tree, pending export, ...).
    # Отличается от FAILED: row остаётся в processing-style состоянии до ручного
    # вмешательства; не automatic-retry'ится.
    ERASURE_BLOCKED = "erasure_blocked"

    # Phase 4.11c — auto-ownership-transfer for shared trees during erasure
    # (см. ADR-0050). Tree-scoped (tree_id != NULL): tree остаётся, меняется
    # только OWNER. AUTO — выбран next-eligible editor; BLOCKED — не нашли
    # eligible editor, нужно manual intervention.
    OWNERSHIP_TRANSFER_AUTO = "ownership_transfer_auto"
    OWNERSHIP_TRANSFER_BLOCKED = "ownership_transfer_blocked"


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
    # Phase 4.11c — auto-transfer не нашёл eligible editor для дерева во время
    # GDPR-erasure preflight; user должен либо вручную пригласить editor'а,
    # либо явно отдать дерево viewer'у через PATCH /trees/{id}/transfer-owner.
    OWNERSHIP_TRANSFER_REQUIRED = "ownership_transfer_required"


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


class EmailKind(StrEnum):
    """Тип transactional-email сообщения (Phase 12.2a, ADR-0039).

    Каждый kind — отдельный шаблон в
    ``services/email-service/templates/{kind}/{locale}.{html,txt}`` +
    ``subject.txt``. Список расширяется по мере появления новых событий;
    неизвестный kind на ``POST /email/send`` отвергается 422.

    Phase 12.2a (this PR) поддерживает три kind'а — minimal launch surface:

    * ``WELCOME`` — после первого Clerk-signup'а (Phase 4.10 hook).
    * ``PAYMENT_SUCCEEDED`` / ``PAYMENT_FAILED`` — Stripe webhook
      ``invoice.paid`` / ``invoice.payment_failed`` (Phase 12.0 hook).

    Phase 12.2b добавит остальные:

    * ``SHARE_INVITE`` — приглашение на дерево (Phase 11.0 / Agent 4).
    * ``EXPORT_READY`` — async GEDCOM-экспорт готов.
    * ``ERASURE_CONFIRMATION`` — GDPR right-to-erasure подтверждение
      (Phase 13.x / Agent 5).
    * ``PASSWORD_RESET_NOTICE`` — Clerk password-reset hook.
    """

    WELCOME = "welcome"
    PAYMENT_SUCCEEDED = "payment_succeeded"
    PAYMENT_FAILED = "payment_failed"
    # Phase 4.11a — async GDPR data export готов к скачиванию.
    EXPORT_READY = "export_ready"
    # Phase 4.11b — GDPR erasure обработан, отправляем подтверждение.
    ERASURE_CONFIRMATION = "erasure_confirmation"
    # Phase 4.11c — auto-ownership-transfer завершён, новый OWNER уведомлён.
    OWNERSHIP_TRANSFERRED = "ownership_transferred"


class EmailSendStatus(StrEnum):
    """Статус попытки отправки transactional-email (Phase 12.2).

    * ``QUEUED`` — запись создана, провайдер ещё не вызван (редкое
      переходное состояние; happy path сразу пишет SENT).
    * ``SENT`` — Resend принял запрос и вернул ``message_id``.
    * ``FAILED`` — провайдер вернул ошибку (после возможных retry).
      ``error`` хранит сообщение для debugging.
    * ``SKIPPED_OPTOUT`` — у пользователя ``email_opt_out=True``.
      Сохраняем строку для audit, провайдер не вызывается.
    """

    QUEUED = "queued"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED_OPTOUT = "skipped_optout"


class Plan(StrEnum):
    """Подписочный план пользователя (Phase 12.0, ADR-0042).

    Хранится в ``subscriptions.plan`` (для PRO/PREMIUM) и резолвится
    в ``Plan.FREE`` для users без активной подписки.

    Бизнес-смысл — см. ADR-0042 §«Plan limits»:

    * ``FREE`` — 1 tree, 100 persons, без DNA, без FS-импорта.
    * ``PRO`` — без лимитов на trees/persons, DNA, FS-импорт
      (rate-limited 5/день, см. ADR-0028).
    * ``PREMIUM`` — Pro + bulk-инструменты, повышенные quota'ы
      (резерв для Phase 12.x — gating пока сводится к Pro-флагам).
    """

    FREE = "free"
    PRO = "pro"
    PREMIUM = "premium"


class SubscriptionStatus(StrEnum):
    """Статус Stripe-подписки.

    Зеркалирует подмножество значений из Stripe API
    (``subscription.status``), которое мы реально используем для
    feature-gating. См. ADR-0042 §«Failed payment policy».

    * ``ACTIVE`` — подписка платится, фичи включены.
    * ``PAST_DUE`` — последний платёж не прошёл, grace period 7 дней.
      В этом окне фичи остаются включены — даём шанс обновить карту.
    * ``CANCELED`` — отменена пользователем (или нами после grace period).
      Фичи сразу off; запись остаётся для history.
    * ``TRIALING`` — Stripe trial period: фичи включены, платёж ещё
      не списан. По окончанию Stripe сам перейдёт в ACTIVE или CANCELED.

    Stripe-ные ``incomplete`` / ``incomplete_expired`` / ``unpaid`` /
    ``paused`` мы сюда не маппим — их обрабатывает event-handler:
    ``incomplete`` → no-op (ждём перехода), ``incomplete_expired`` →
    ``CANCELED``, ``unpaid``/``paused`` → ``PAST_DUE``.
    """

    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    TRIALING = "trialing"


class StripeEventStatus(StrEnum):
    """Статус обработки Stripe webhook event'а (idempotency log).

    * ``RECEIVED`` — event попал в БД, но обработчик ещё не отработал.
      Должен быть редок (только если процесс упал между insert и dispatch).
    * ``PROCESSED`` — event успешно применён к ORM, дубль будет проигнорирован.
    * ``FAILED`` — обработчик бросил exception. Stripe пере-доставит event,
      и тогда мы попробуем заново (idempotency-чек смотрит на
      ``PROCESSED`` only).
    """

    RECEIVED = "received"
    PROCESSED = "processed"
    FAILED = "failed"
