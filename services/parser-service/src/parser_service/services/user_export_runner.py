"""GDPR data-export runner (Phase 4.11a, ADR-0046).

Worker-side обработка ``user_action_requests``-row с ``kind='export'``:

1. Перевести row в ``status='processing'`` + audit-entry
   (``EXPORT_PROCESSING``).
2. Собрать данные user'а (профиль, owned trees + содержимое, DNA-records,
   audit history, action requests).
3. Сериализовать в ZIP (один файл на категорию + ``manifest.json``).
4. Загрузить ZIP в object-storage по канонической раскладке
   ``gdpr-exports/{user_id}/{request_id}.zip``.
5. Сгенерировать short-lived signed-URL (15 мин) и положить idempotent
   email через ``send_transactional_email("export_ready", ...)``.
6. Финализировать row (``status='done'``, ``processed_at``,
   ``request_metadata`` с bucket-key + size + signed_url snapshot)
   + audit-entry (``EXPORT_COMPLETED``).
7. На любом исключении — rollback к ``status='failed'`` с ``error``,
   audit ``EXPORT_FAILED``. Без auto-retry (manual intervention).

Privacy / GDPR notes:

* Encrypted DNA-blobs **не** включаются в export — ZIP содержит только
  metadata (kit name, провайдер, размер blob'а, hash). Расшифровка
  требует disclosure key, которого worker не имеет на runtime.
* OAuth-токены (``users.fs_token_encrypted``) **не** включаются —
  они secret и не относятся к user-data в смысле Art. 15.
* Чужие данные не утекают: tree-contents выгружаются только для
  trees, где user является OWNER (через ``tree_memberships.role='owner'``
  ИЛИ ``trees.owner_user_id == user_id`` для backwards-compat).
* Audit-log entries фильтруются по ``actor_user_id == user_id`` —
  user видит свои действия, не действия других в shared trees.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import logging
import uuid
import zipfile
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from shared_models.enums import ActorKind, AuditAction, EmailKind
from shared_models.orm import (
    AuditLog,
    Citation,
    DnaConsent,
    DnaImport,
    DnaKit,
    DnaMatch,
    DnaTestRecord,
    Event,
    EventParticipant,
    Family,
    FamilyChild,
    Hypothesis,
    Name,
    Person,
    Place,
    Source,
    Tree,
    TreeMembership,
    User,
    UserActionRequest,
)
from shared_models.storage import (
    ObjectStorage,
    SignedUrl,
    gdpr_export_key,
)
from shared_models.types import new_uuid
from sqlalchemy import select

from parser_service.services.email_dispatcher import send_transactional_email

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from parser_service.config import Settings


_LOG: Final = logging.getLogger(__name__)

# Версия manifest-формата. Bump при ЛЮБОМ breaking-изменении layout'а
# (например, переименование папки, расщепление JSON-файла). Reader-side
# tooling (если появится) проверяет это поле.
MANIFEST_VERSION: Final = "1.0"

# Категории, которые worker всегда пишет (даже пустые) для предсказуемого
# layout'а. Empty-list — валидное значение.
_REQUIRED_CATEGORIES: Final = ("profile", "trees", "dna", "audit_log", "action_requests")


@dataclass(frozen=True, slots=True)
class ExportResult:
    """Output run_user_export — для worker'а / тестов."""

    request_id: uuid.UUID
    bucket_key: str
    size_bytes: int
    signed_url: SignedUrl
    email_idempotency_key: str


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def run_user_export(
    session: AsyncSession,
    request_id: uuid.UUID,
    *,
    storage: ObjectStorage,
    settings: Settings,
) -> ExportResult:
    """Полный pipeline экспорта одного user_action_request'а.

    Caller (arq job или test) держит сессию, владеет commit/rollback'ом.

    Контракты:

    * Если row не существует или ``kind != 'export'`` — поднимает
      ``LookupError`` / ``ValueError``. Caller обязан проверять.
    * Если row уже ``done`` или ``failed`` — early-return текущим
      состоянием (idempotent re-enqueue).
    * Если ``processing`` → продолжаем (resume after worker crash —
      безопасно, так как ZIP пере-генерируется и storage.put перезаписывает).

    Returns:
        :class:`ExportResult` — для логов/тестов; sterile (без PII).

    Raises:
        Любое исключение пробрасывается caller'у. Перед raise — row
        переведён в ``status='failed'`` с ``error=str(exc)``, audit-entry
        ``EXPORT_FAILED`` записан в ту же session (caller commit'ит).
    """
    request = await _load_request_or_raise(session, request_id)
    user = await _load_user_or_raise(session, request.user_id)
    now = dt.datetime.now(dt.UTC)

    # Idempotent early-return для terminal статусов.
    if request.status in ("done", "failed", "cancelled"):
        _LOG.info(
            "run_user_export: row %s already terminal (status=%s) — no-op",
            request.id,
            request.status,
        )
        # Если done — мы можем re-issue signed-URL для list endpoint'а.
        # Но run_user_export — это worker entry. Для re-sign используем
        # отдельный helper :func:`build_signed_url_for_existing_export`.
        return await _result_from_done_row(request, storage=storage, settings=settings)

    try:
        # ---- 1. processing transition + audit ----
        request.status = "processing"
        request.error = None
        session.add(
            _build_user_action_audit(
                user_id=user.id,
                request_id=request.id,
                action=AuditAction.EXPORT_PROCESSING,
                metadata={"started_at": now.isoformat()},
                now=now,
            )
        )
        await session.flush()

        # ---- 2. collect data ----
        bundle = await _collect_export_bundle(session, user=user)

        # ---- 3. serialize ZIP ----
        zip_bytes, manifest = _build_zip(bundle=bundle, request=request, user=user, now=now)
        size_bytes = len(zip_bytes)
        if size_bytes > settings.export_max_zip_size_mb * 1024 * 1024:
            msg = (
                f"Export ZIP exceeds soft cap "
                f"({size_bytes / 1024 / 1024:.1f} MiB > "
                f"{settings.export_max_zip_size_mb} MiB). "
                f"Contact support or use filtered export (Phase 4.11b)."
            )
            raise ValueError(msg)

        # ---- 4. upload ----
        bucket_key = gdpr_export_key(user_id=user.id, request_id=request.id)
        await storage.put(bucket_key, zip_bytes, content_type="application/zip")

        # ---- 5. signed URL + email ----
        signed = await storage.signed_download_url(
            bucket_key,
            expires_in_seconds=settings.export_url_ttl_seconds,
        )
        idempotency_key = f"export_ready:{request.id}"
        await send_transactional_email(
            kind=EmailKind.EXPORT_READY.value,
            recipient_user_id=user.id,
            idempotency_key=idempotency_key,
            params={
                "export_url": signed.url,
                "export_size_bytes": size_bytes,
                "export_format": "zip_v1",
            },
        )

        # ---- 6. finalize ----
        finished = dt.datetime.now(dt.UTC)
        request.status = "done"
        request.processed_at = finished
        request.request_metadata = {
            **(request.request_metadata or {}),
            "bucket_key": bucket_key,
            "size_bytes": size_bytes,
            "manifest_version": MANIFEST_VERSION,
            "signed_url_last_issued_at": finished.isoformat(),
            "signed_url_ttl_seconds": settings.export_url_ttl_seconds,
            "object_ttl_days": settings.export_object_ttl_days,
            "categories": list(manifest["categories"].keys()),
        }
        session.add(
            _build_user_action_audit(
                user_id=user.id,
                request_id=request.id,
                action=AuditAction.EXPORT_COMPLETED,
                metadata={
                    "completed_at": finished.isoformat(),
                    "size_bytes": size_bytes,
                    "categories": list(manifest["categories"].keys()),
                    "email_idempotency_key": idempotency_key,
                },
                now=finished,
            )
        )
        await session.flush()

        return ExportResult(
            request_id=request.id,
            bucket_key=bucket_key,
            size_bytes=size_bytes,
            signed_url=signed,
            email_idempotency_key=idempotency_key,
        )

    except Exception as exc:
        # Failure path: пишем status=failed + audit, потом re-raise.
        # Используем отдельный try/except чтобы не маскировать original error.
        try:
            failed_at = dt.datetime.now(dt.UTC)
            request.status = "failed"
            request.error = f"{type(exc).__name__}: {exc}"
            request.processed_at = failed_at
            session.add(
                _build_user_action_audit(
                    user_id=user.id,
                    request_id=request.id,
                    action=AuditAction.EXPORT_FAILED,
                    metadata={
                        "error_kind": type(exc).__name__,
                        "error_message": str(exc),
                        "failed_at": failed_at.isoformat(),
                    },
                    now=failed_at,
                )
            )
            await session.flush()
        except Exception:
            # Defensive — audit-write не должен скрыть оригинальное исключение.
            _LOG.exception("Failed to write EXPORT_FAILED audit for request %s", request_id)
        raise


# ---------------------------------------------------------------------------
# Re-sign helper (для list endpoint'а — выдаёт fresh signed URL)
# ---------------------------------------------------------------------------


async def build_signed_url_for_existing_export(
    request: UserActionRequest,
    *,
    storage: ObjectStorage,
    settings: Settings,
) -> SignedUrl | None:
    """Issue fresh signed-URL для уже завершённого export'а.

    Bucket-key хранится в ``request_metadata['bucket_key']`` после
    успешного run. Если row не ``done`` или metadata пустая — вернёт
    ``None`` (caller не показывает signed_url).

    Каждый вызов даёт свежий 15-минутный URL — это OK, бекенды S3/GCS
    не stateful (presigned URL — pure function от key + expires).
    """
    if request.status != "done":
        return None
    bucket_key = (request.request_metadata or {}).get("bucket_key")
    if not isinstance(bucket_key, str) or not bucket_key:
        return None
    return await storage.signed_download_url(
        bucket_key,
        expires_in_seconds=settings.export_url_ttl_seconds,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _load_request_or_raise(session: AsyncSession, request_id: uuid.UUID) -> UserActionRequest:
    """Load row; raise LookupError/ValueError на отсутствие/неверный kind."""
    row = (
        await session.execute(select(UserActionRequest).where(UserActionRequest.id == request_id))
    ).scalar_one_or_none()
    if row is None:
        msg = f"UserActionRequest {request_id} not found"
        raise LookupError(msg)
    if row.kind != "export":
        msg = f"UserActionRequest {request_id} has kind={row.kind!r}, expected 'export'"
        raise ValueError(msg)
    return row


async def _load_user_or_raise(session: AsyncSession, user_id: uuid.UUID) -> User:
    """Load User; raise LookupError если row уже удалён hard-delete'ом."""
    user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        msg = f"User {user_id} not found (already erased?)"
        raise LookupError(msg)
    return user


async def _result_from_done_row(
    request: UserActionRequest,
    *,
    storage: ObjectStorage,
    settings: Settings,
) -> ExportResult:
    """Synthesize ExportResult для idempotent re-call с status=done."""
    bucket_key = (request.request_metadata or {}).get("bucket_key", "")
    size_bytes = (request.request_metadata or {}).get("size_bytes", 0)
    signed = (
        await storage.signed_download_url(
            bucket_key,
            expires_in_seconds=settings.export_url_ttl_seconds,
        )
        if bucket_key
        else SignedUrl(url="", expires_at=dt.datetime.now(dt.UTC))
    )
    return ExportResult(
        request_id=request.id,
        bucket_key=str(bucket_key),
        size_bytes=int(size_bytes),
        signed_url=signed,
        email_idempotency_key=f"export_ready:{request.id}",
    )


def _build_user_action_audit(
    *,
    user_id: uuid.UUID,
    request_id: uuid.UUID,
    action: AuditAction,
    metadata: dict[str, Any],
    now: dt.datetime,
) -> AuditLog:
    """Сконструировать audit_log row для GDPR-action user-уровня.

    Конвенция (см. ADR-0046):

    * ``tree_id = NULL`` — это user-action, не tree-action.
    * ``entity_type = 'user_action_request'``, ``entity_id = request.id``.
    * ``actor_user_id = user_id`` (тот же user, что инициировал).
    * ``actor_kind = USER`` для request/processing/completed (на user'я
      и за user'я), ``SYSTEM`` для failed (system-side error).
    * ``diff`` хранит metadata-payload — конкретика action'а.
    """
    actor_kind = ActorKind.SYSTEM if action == AuditAction.EXPORT_FAILED else ActorKind.USER
    return AuditLog(
        id=new_uuid(),
        tree_id=None,
        entity_type="user_action_request",
        entity_id=request_id,
        action=action.value,
        actor_user_id=user_id,
        actor_kind=actor_kind.value,
        import_job_id=None,
        reason=None,
        diff={"action": action.value, "metadata": metadata},
        created_at=now,
    )


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _ExportBundle:
    """In-memory bundle перед сериализацией в ZIP."""

    profile: dict[str, Any]
    trees: list[dict[str, Any]]  # каждый dict = one tree + nested entities
    dna: dict[str, list[dict[str, Any]]]  # kits/test_records/consents/imports/matches
    audit_log: list[dict[str, Any]]
    action_requests: list[dict[str, Any]]
    memberships: list[dict[str, Any]]


async def _collect_export_bundle(session: AsyncSession, *, user: User) -> _ExportBundle:
    """Собрать все данные пользователя в memory bundle."""
    profile = _serialize_user_profile(user)
    owned_tree_ids = await _list_owned_tree_ids(session, user_id=user.id)
    trees = await _collect_trees(session, tree_ids=owned_tree_ids)
    dna = await _collect_dna(session, user_id=user.id)
    audit = await _collect_audit_log(session, user_id=user.id)
    requests = await _collect_action_requests(session, user_id=user.id)
    memberships = await _collect_memberships(session, user_id=user.id)
    return _ExportBundle(
        profile=profile,
        trees=trees,
        dna=dna,
        audit_log=audit,
        action_requests=requests,
        memberships=memberships,
    )


def _serialize_user_profile(user: User) -> dict[str, Any]:
    """Sanitized snapshot of users-row.

    Excluded fields (per ADR-0046 §«What is NOT included»):

    * ``external_auth_id`` / ``clerk_user_id`` — internal auth identifiers,
      не personal-data в смысле Art. 15.
    * ``fs_token_encrypted`` — secret encrypted blob, бесполезен без key.
    """
    return {
        "id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "locale": user.locale,
        "timezone": user.timezone,
        "email_opt_out": user.email_opt_out,
        "created_at": _iso(user.created_at),
        "updated_at": _iso(user.updated_at),
    }


async def _list_owned_tree_ids(session: AsyncSession, *, user_id: uuid.UUID) -> list[uuid.UUID]:
    """Trees где user является OWNER.

    Источник истины для Phase 11.0+ — ``tree_memberships.role='owner' AND
    revoked_at IS NULL``. Для backwards-compat дополнительно ловим
    legacy-trees где ``trees.owner_user_id == user_id`` без membership-row.
    """
    membership_ids = (
        (
            await session.execute(
                select(TreeMembership.tree_id).where(
                    TreeMembership.user_id == user_id,
                    TreeMembership.role == "owner",
                    TreeMembership.revoked_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    legacy_ids = (
        (await session.execute(select(Tree.id).where(Tree.owner_user_id == user_id)))
        .scalars()
        .all()
    )
    seen: set[uuid.UUID] = set()
    out: list[uuid.UUID] = []
    for tid in (*membership_ids, *legacy_ids):
        if tid not in seen:
            seen.add(tid)
            out.append(tid)
    return out


async def _collect_trees(
    session: AsyncSession, *, tree_ids: list[uuid.UUID]
) -> list[dict[str, Any]]:
    """Bulk-load tree contents для серилизации."""
    if not tree_ids:
        return []
    out: list[dict[str, Any]] = []
    for tree_id in tree_ids:
        tree = (await session.execute(select(Tree).where(Tree.id == tree_id))).scalar_one_or_none()
        if tree is None:
            continue
        persons = (
            (await session.execute(select(Person).where(Person.tree_id == tree_id))).scalars().all()
        )
        person_ids = [p.id for p in persons]
        names = (
            await session.execute(select(Name).where(Name.person_id.in_(person_ids)))
            if person_ids
            else None
        )
        families = (
            (await session.execute(select(Family).where(Family.tree_id == tree_id))).scalars().all()
        )
        family_ids = [f.id for f in families]
        family_children = (
            await session.execute(select(FamilyChild).where(FamilyChild.family_id.in_(family_ids)))
            if family_ids
            else None
        )
        events = (
            (await session.execute(select(Event).where(Event.tree_id == tree_id))).scalars().all()
        )
        event_ids = [e.id for e in events]
        participants = (
            await session.execute(
                select(EventParticipant).where(EventParticipant.event_id.in_(event_ids))
            )
            if event_ids
            else None
        )
        places = (
            (await session.execute(select(Place).where(Place.tree_id == tree_id))).scalars().all()
        )
        sources = (
            (await session.execute(select(Source).where(Source.tree_id == tree_id))).scalars().all()
        )
        citations = (
            (await session.execute(select(Citation).where(Citation.tree_id == tree_id)))
            .scalars()
            .all()
        )
        hypotheses = (
            (await session.execute(select(Hypothesis).where(Hypothesis.tree_id == tree_id)))
            .scalars()
            .all()
        )

        out.append(
            {
                "tree_id": str(tree.id),
                "name": tree.name,
                "description": tree.description,
                "visibility": tree.visibility,
                "default_locale": tree.default_locale,
                "settings": tree.settings or {},
                "created_at": _iso(tree.created_at),
                "updated_at": _iso(tree.updated_at),
                "persons": [_serialize_person(p) for p in persons],
                "names": [_serialize_name(n) for n in (names.scalars().all() if names else [])],
                "families": [_serialize_family(f) for f in families],
                "family_children": [
                    _serialize_family_child(fc)
                    for fc in (family_children.scalars().all() if family_children else [])
                ],
                "events": [_serialize_event(e) for e in events],
                "event_participants": [
                    _serialize_participant(p)
                    for p in (participants.scalars().all() if participants else [])
                ],
                "places": [_serialize_place(p) for p in places],
                "sources": [_serialize_source(s) for s in sources],
                "citations": [_serialize_citation(c) for c in citations],
                "hypotheses": [_serialize_hypothesis(h) for h in hypotheses],
            }
        )
    return out


async def _collect_dna(
    session: AsyncSession, *, user_id: uuid.UUID
) -> dict[str, list[dict[str, Any]]]:
    """Все DNA-связанные записи user'а (БЕЗ encrypted blob bytes).

    Encrypted segment data — special category (CLAUDE.md §3.5). Не
    включаем в export ни в plaintext, ни в ciphertext: ciphertext без
    key бесполезен (user всё равно не сможет ничего с ним сделать), а
    plaintext требует decryption pipeline которого worker не имеет.
    Включаем только metadata (kit name, провайдер, размер blob'а, hash,
    consent timestamps).
    """
    # DnaKit owner_user_id (а не user_id — отличие от DnaTestRecord/DnaConsent).
    kits = (
        (await session.execute(select(DnaKit).where(DnaKit.owner_user_id == user_id)))
        .scalars()
        .all()
    )
    kit_ids = [k.id for k in kits]
    # DnaTestRecord — есть собственный user_id (загрузивший); собираем по нему,
    # не через kit (DnaTestRecord не имеет kit_id — связь через consent_id).
    test_records = (
        (await session.execute(select(DnaTestRecord).where(DnaTestRecord.user_id == user_id)))
        .scalars()
        .all()
    )
    consents = (
        (await session.execute(select(DnaConsent).where(DnaConsent.user_id == user_id)))
        .scalars()
        .all()
    )
    # DnaImport — created_by_user_id (а не user_id).
    imports = (
        (await session.execute(select(DnaImport).where(DnaImport.created_by_user_id == user_id)))
        .scalars()
        .all()
    )
    # DnaMatch tree-scoped через kit_id; берём матчи только для kits user'а.
    matches = (
        (await session.execute(select(DnaMatch).where(DnaMatch.kit_id.in_(kit_ids))))
        .scalars()
        .all()
        if kit_ids
        else []
    )

    return {
        "kits": [_serialize_dna_kit(k) for k in kits],
        "test_records": [_serialize_dna_test_record(r) for r in test_records],
        "consents": [_serialize_dna_consent(c) for c in consents],
        "imports": [_serialize_dna_import(i) for i in imports],
        "matches": [_serialize_dna_match(m) for m in matches],
    }


async def _collect_audit_log(session: AsyncSession, *, user_id: uuid.UUID) -> list[dict[str, Any]]:
    """Audit-entries где actor_user_id == user_id (свои действия)."""
    rows = (
        (
            await session.execute(
                select(AuditLog)
                .where(AuditLog.actor_user_id == user_id)
                .order_by(AuditLog.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": str(r.id),
            "tree_id": str(r.tree_id) if r.tree_id else None,
            "entity_type": r.entity_type,
            "entity_id": str(r.entity_id),
            "action": r.action,
            "actor_kind": r.actor_kind,
            "import_job_id": str(r.import_job_id) if r.import_job_id else None,
            "reason": r.reason,
            "diff": r.diff,
            "created_at": _iso(r.created_at),
        }
        for r in rows
    ]


async def _collect_action_requests(
    session: AsyncSession, *, user_id: uuid.UUID
) -> list[dict[str, Any]]:
    """История user_action_requests (export/erasure) этого user'а."""
    rows = (
        (
            await session.execute(
                select(UserActionRequest)
                .where(UserActionRequest.user_id == user_id)
                .order_by(UserActionRequest.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": str(r.id),
            "kind": r.kind,
            "status": r.status,
            "request_metadata": r.request_metadata or {},
            "processed_at": _iso(r.processed_at),
            "error": r.error,
            "created_at": _iso(r.created_at),
            "updated_at": _iso(r.updated_at),
        }
        for r in rows
    ]


async def _collect_memberships(
    session: AsyncSession, *, user_id: uuid.UUID
) -> list[dict[str, Any]]:
    """Tree memberships user'а (включая non-owner: invited/viewer)."""
    rows = (
        (await session.execute(select(TreeMembership).where(TreeMembership.user_id == user_id)))
        .scalars()
        .all()
    )
    return [
        {
            "tree_id": str(r.tree_id),
            "role": r.role,
            "invited_by": str(r.invited_by) if r.invited_by else None,
            "accepted_at": _iso(r.accepted_at),
            "revoked_at": _iso(r.revoked_at),
            "created_at": _iso(r.created_at),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# ZIP serialization
# ---------------------------------------------------------------------------


def _build_zip(
    *,
    bundle: _ExportBundle,
    request: UserActionRequest,
    user: User,
    now: dt.datetime,
) -> tuple[bytes, dict[str, Any]]:
    """Сериализовать bundle в ZIP-bytes + manifest dict.

    Layout:

    * ``manifest.json`` — top-level metadata.
    * ``profile.json``
    * ``trees/{tree_id}.json`` — per-tree (один файл со всем содержимым).
    * ``dna/{category}.json`` — kits / test_records / consents / imports / matches.
    * ``audit_log.json``
    * ``action_requests.json``
    * ``memberships.json``

    Возвращаем bytes (для storage.put) и manifest dict (для request_metadata).
    """
    files: dict[str, bytes] = {}
    files["profile.json"] = _to_json_bytes(bundle.profile)
    for tree in bundle.trees:
        files[f"trees/{tree['tree_id']}.json"] = _to_json_bytes(tree)
    for category, rows in bundle.dna.items():
        files[f"dna/{category}.json"] = _to_json_bytes(rows)
    files["audit_log.json"] = _to_json_bytes(bundle.audit_log)
    files["action_requests.json"] = _to_json_bytes(bundle.action_requests)
    files["memberships.json"] = _to_json_bytes(bundle.memberships)

    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "generated_at": _iso(now),
        "user_id": str(user.id),
        "user_email": user.email,
        "request_id": str(request.id),
        "format": "zip_v1",
        "categories": {
            "profile": {"file": "profile.json"},
            "trees": {
                "files": [f"trees/{t['tree_id']}.json" for t in bundle.trees],
                "count": len(bundle.trees),
            },
            "dna": {
                "files": [f"dna/{cat}.json" for cat in bundle.dna],
                "counts": {cat: len(rows) for cat, rows in bundle.dna.items()},
            },
            "audit_log": {"file": "audit_log.json", "count": len(bundle.audit_log)},
            "action_requests": {
                "file": "action_requests.json",
                "count": len(bundle.action_requests),
            },
            "memberships": {
                "file": "memberships.json",
                "count": len(bundle.memberships),
            },
        },
        "excluded": [
            "Encrypted DNA segment blobs (no decryption key available to worker)",
            "OAuth tokens (users.fs_token_encrypted)",
            "Internal auth identifiers (external_auth_id, clerk_user_id)",
            "Audit-log entries from other actors (only your own actions are included)",
        ],
        "required_categories": list(_REQUIRED_CATEGORIES),
    }
    files["manifest.json"] = _to_json_bytes(manifest)

    buffer = io.BytesIO()
    # ZIP_DEFLATED — приличная компрессия, поддерживается всеми клиентами.
    # Один pass write — не stream, потому что worker уже всё в памяти.
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        # manifest первым — чтобы reader мог увидеть версию до распаковки rest'а.
        zf.writestr("manifest.json", files["manifest.json"])
        for name, payload in sorted(files.items()):
            if name == "manifest.json":
                continue
            zf.writestr(name, payload)
    return buffer.getvalue(), manifest


def _to_json_bytes(payload: Any) -> bytes:
    """JSON serialize → utf-8 bytes; sort keys для determinism тестов."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")


def _iso(value: dt.datetime | None) -> str | None:
    """datetime → ISO-8601 string или None."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.UTC)
    return value.isoformat()


# ---------------------------------------------------------------------------
# Per-entity serializers (column subsets — никаких relationship traversal)
# ---------------------------------------------------------------------------


def _serialize_person(p: Person) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "gedcom_xref": p.gedcom_xref,
        "sex": p.sex,
        "status": p.status,
        "confidence_score": p.confidence_score,
        "version_id": p.version_id,
        "provenance": p.provenance or {},
        "surname_dm": list(p.surname_dm or []),
        "given_name_dm": list(p.given_name_dm or []),
        "created_at": _iso(p.created_at),
        "updated_at": _iso(p.updated_at),
    }


def _serialize_name(n: Name) -> dict[str, Any]:
    return {
        "id": str(n.id),
        "person_id": str(n.person_id),
        "given_name": n.given_name,
        "surname": n.surname,
        "sort_order": n.sort_order,
        "name_type": n.name_type,
        "created_at": _iso(n.created_at),
        "updated_at": _iso(n.updated_at),
    }


def _serialize_family(f: Family) -> dict[str, Any]:
    return {
        "id": str(f.id),
        "gedcom_xref": f.gedcom_xref,
        "husband_id": str(f.husband_id) if f.husband_id else None,
        "wife_id": str(f.wife_id) if f.wife_id else None,
        "status": f.status,
        "confidence_score": f.confidence_score,
        "provenance": f.provenance or {},
        "created_at": _iso(f.created_at),
        "updated_at": _iso(f.updated_at),
    }


def _serialize_family_child(fc: FamilyChild) -> dict[str, Any]:
    return {
        "id": str(fc.id),
        "family_id": str(fc.family_id),
        "child_person_id": str(fc.child_person_id),
        "created_at": _iso(fc.created_at),
    }


def _serialize_event(e: Event) -> dict[str, Any]:
    return {
        "id": str(e.id),
        "event_type": e.event_type,
        "custom_type": e.custom_type,
        "place_id": str(e.place_id) if e.place_id else None,
        "date_raw": e.date_raw,
        "date_start": _iso(e.date_start)
        if isinstance(e.date_start, dt.datetime)
        else (e.date_start.isoformat() if e.date_start else None),
        "date_end": _iso(e.date_end)
        if isinstance(e.date_end, dt.datetime)
        else (e.date_end.isoformat() if e.date_end else None),
        "date_qualifier": e.date_qualifier,
        "date_calendar": e.date_calendar,
        "description": e.description,
        "status": e.status,
        "provenance": e.provenance or {},
        "created_at": _iso(e.created_at),
        "updated_at": _iso(e.updated_at),
    }


def _serialize_participant(p: EventParticipant) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "event_id": str(p.event_id),
        "person_id": str(p.person_id) if p.person_id else None,
        "family_id": str(p.family_id) if p.family_id else None,
        "role": p.role,
        "created_at": _iso(p.created_at),
    }


def _serialize_place(p: Place) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "canonical_name": p.canonical_name,
        "status": p.status,
        "provenance": p.provenance or {},
        "created_at": _iso(p.created_at),
        "updated_at": _iso(p.updated_at),
    }


def _serialize_source(s: Source) -> dict[str, Any]:
    return {
        "id": str(s.id),
        "title": s.title,
        "author": s.author,
        "abbreviation": s.abbreviation,
        "publication": s.publication,
        "text_excerpt": s.text_excerpt,
        "gedcom_xref": s.gedcom_xref,
        "source_type": s.source_type,
        "repository": s.repository,
        "url": s.url,
        "publication_date": _iso(s.publication_date)
        if isinstance(s.publication_date, dt.datetime)
        else (s.publication_date.isoformat() if s.publication_date else None),
        "status": s.status,
        "provenance": s.provenance or {},
        "created_at": _iso(s.created_at),
        "updated_at": _iso(s.updated_at),
    }


def _serialize_citation(c: Citation) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "source_id": str(c.source_id),
        "entity_type": c.entity_type,
        "entity_id": str(c.entity_id),
        "page_or_section": c.page_or_section,
        "quoted_text": c.quoted_text,
        "quality": c.quality,
        "quay_raw": c.quay_raw,
        "event_type": c.event_type,
        "role": c.role,
        "note": c.note,
        "provenance": c.provenance or {},
        "created_at": _iso(c.created_at),
        "updated_at": _iso(c.updated_at),
    }


def _serialize_hypothesis(h: Hypothesis) -> dict[str, Any]:
    return {
        "id": str(h.id),
        "kind": getattr(h, "kind", None) or getattr(h, "hypothesis_type", None),
        "status": getattr(h, "status", None),
        "confidence_score": getattr(h, "confidence_score", None),
        "rationale": getattr(h, "rationale", None),
        "provenance": getattr(h, "provenance", None) or {},
        "created_at": _iso(h.created_at),
        "updated_at": _iso(h.updated_at),
    }


def _serialize_dna_kit(k: DnaKit) -> dict[str, Any]:
    return {
        "id": str(k.id),
        "tree_id": str(k.tree_id),
        "source_platform": k.source_platform,
        "external_kit_id": k.external_kit_id,
        "display_name": k.display_name,
        "person_id": str(k.person_id) if k.person_id else None,
        "test_date": k.test_date.isoformat() if k.test_date else None,
        "ethnicity_population": k.ethnicity_population,
        "consent_signed_at": _iso(k.consent_signed_at),
        "created_at": _iso(k.created_at),
        "updated_at": _iso(k.updated_at),
    }


def _serialize_dna_test_record(r: DnaTestRecord) -> dict[str, Any]:
    # NOTE: encrypted blob — только metadata; storage_path и sha256 включаем
    # как идентификаторы для traceability, но raw bytes не передаём.
    return {
        "id": str(r.id),
        "tree_id": str(r.tree_id),
        "consent_id": str(r.consent_id),
        "storage_path": r.storage_path,
        "size_bytes": r.size_bytes,
        "sha256": r.sha256,
        "snp_count": r.snp_count,
        "provider": r.provider,
        "encryption_scheme": r.encryption_scheme,
        "uploaded_at": _iso(r.uploaded_at),
    }


def _serialize_dna_consent(c: DnaConsent) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "tree_id": str(c.tree_id),
        "kit_owner_email": c.kit_owner_email,
        "consent_text": c.consent_text,
        "consent_version": c.consent_version,
        "consented_at": _iso(c.consented_at),
        "revoked_at": _iso(c.revoked_at),
    }


def _serialize_dna_import(i: DnaImport) -> dict[str, Any]:
    return {
        "id": str(i.id),
        "tree_id": str(i.tree_id),
        "kit_id": str(i.kit_id) if i.kit_id else None,
        "source_platform": i.source_platform,
        "import_kind": i.import_kind,
        "source_filename": i.source_filename,
        "source_size_bytes": i.source_size_bytes,
        "source_sha256": i.source_sha256,
        "status": i.status,
        "created_at": _iso(i.created_at),
    }


def _serialize_dna_match(m: DnaMatch) -> dict[str, Any]:
    # Сегменты (per-chromosome painting) хранятся в shared_match — здесь
    # только агрегированные cM stats и predicted relationship.
    return {
        "id": str(m.id),
        "kit_id": str(m.kit_id),
        "external_match_id": m.external_match_id,
        "display_name": m.display_name,
        "total_cm": m.total_cm,
        "largest_segment_cm": m.largest_segment_cm,
        "segment_count": m.segment_count,
        "predicted_relationship": m.predicted_relationship,
        "confidence": m.confidence,
        "shared_match_count": m.shared_match_count,
    }


__all__ = [
    "MANIFEST_VERSION",
    "ExportResult",
    "build_signed_url_for_existing_export",
    "run_user_export",
]
