"""AI tree-chat endpoints (Phase 10.7c + 10.7d).

* ``POST /trees/{tree_id}/chat/turn`` — один turn разговора (SSE-стрим;
  Phase 10.7c). Резолвит references c обоих сторон (user + assistant)
  через 10.7b ego-resolver, persist'ит обе сообщения вместе с
  source-citation линками в ``chat_messages.references_jsonb``.
* ``GET /trees/{tree_id}/chat/sessions`` — пагинированный список
  чат-сессий пользователя в дереве с агрегатами (message_count,
  last_message_at). Phase 10.7d.
* ``GET /trees/{tree_id}/chat/sessions/{session_id}`` — single-session
  metadata (для resume-URL). Phase 10.7d.
* ``GET /trees/{tree_id}/chat/sessions/{session_id}/messages`` —
  пагинированная история сообщений (UI "load on mount"). Phase 10.7d.

SSE кадры (POST turn):

* ``{"type": "session", "session_id": ..., "anchor_person_id": ...}`` —
  первый кадр.
* ``{"type": "token", "delta": "..."}`` — text-deltas Claude'а.
* ``{"type": "done", "message_id": ..., "referenced_persons": [...],
  "assistant_references": [...]}`` — финальный кадр.
* ``{"type": "error", "detail": "..."}`` — terminal error.

Permission-gate: VIEWER+ на всех эндпоинтах. Чат scoped per-user — list
возвращает только сессии current-user'а; get-эндпоинты 404'ят на чужие
session_id (leak protection через timing).
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

from ai_layer import AILayerConfig, AILayerDisabledError
from ai_layer.clients.anthropic_client import AnthropicClient
from ai_layer.ego_resolver import resolve_reference
from ai_layer.ego_resolver.types import PersonNames, TreeContext
from fastapi import APIRouter, Depends, HTTPException, Query, status
from shared_models import TreeRole
from shared_models.orm import (
    ChatMessage,
    ChatMessageRole,
    ChatSession,
    Name,
    Person,
    Source,
    Tree,
    User,
)
from shared_models.orm.completeness_assertion import sealed_scopes_for_person
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from parser_service.auth import get_current_user
from parser_service.database import get_session, get_session_factory
from parser_service.schemas import (
    ChatMessageListResponse,
    ChatMessageResponse,
    ChatSessionListItem,
    ChatSessionListResponse,
    ChatSessionResponse,
    ChatTurnRequest,
)
from parser_service.services.ego_traversal import load_family_traversal
from parser_service.services.permissions import require_tree_role

logger = logging.getLogger(__name__)

router = APIRouter()


# -----------------------------------------------------------------------------
# Dependencies — overridable в тестах через app.dependency_overrides.
# -----------------------------------------------------------------------------


def get_ai_layer_config() -> AILayerConfig:
    """Свежий ``AILayerConfig`` из ENV — зеркалит normalize.py / ai_extraction.py."""
    return AILayerConfig.from_env()


def get_anthropic_client(
    config: Annotated[AILayerConfig, Depends(get_ai_layer_config)],
) -> AnthropicClient:
    """Сборка ``AnthropicClient``; SDK инициализируется лениво до первого call."""
    return AnthropicClient(config)


# -----------------------------------------------------------------------------
# System prompt template.
# -----------------------------------------------------------------------------


_SYSTEM_PROMPT_TEMPLATE = (
    "You are AutoTreeGen's tree assistant — an evidence-based genealogical "
    "research helper. The user is conversing about a specific family tree.\n\n"
    "Tree context:\n"
    '- Self-anchor ("you" in this tree): {anchor_label}\n'
    "- Tree size: {person_count} persons\n"
    "{anchor_relations}\n\n"
    "Style: concise, factual, lab-notebook tone. Cite person names exactly as "
    "they appear in the tree. If the user asks about someone not in the tree, "
    "say so. Do not invent dates, places, or relationships."
)


# -----------------------------------------------------------------------------
# Helpers — context build, name index, reference parsing.
# -----------------------------------------------------------------------------


async def _load_tree_context(
    session: AsyncSession,
    *,
    tree_id: uuid.UUID,
) -> TreeContext:
    """Собирает ``TreeContext`` для ego_resolver'а: traversal + name-records."""
    traversal = await load_family_traversal(session, tree_id=tree_id)

    persons_res = await session.execute(
        select(
            Name.person_id,
            Name.given_name,
            Name.surname,
            Name.romanized,
            Name.nickname,
            Name.maiden_surname,
            Name.sort_order,
        )
        .join(Person, Person.id == Name.person_id)
        .where(
            Person.tree_id == tree_id,
            Person.deleted_at.is_(None),
            Name.deleted_at.is_(None),
        )
        .order_by(Name.person_id, Name.sort_order)
    )

    by_person: dict[uuid.UUID, dict[str, Any]] = {}
    for row in persons_res.all():
        bucket = by_person.setdefault(
            row.person_id,
            {"given": None, "surname": None, "full_names": [], "aliases": []},
        )
        # Первое (sort_order=0) имя становится primary — given/surname.
        if bucket["given"] is None and row.given_name:
            bucket["given"] = row.given_name
        if bucket["surname"] is None and row.surname:
            bucket["surname"] = row.surname
        # Full-name строки: "Given Surname" если оба есть.
        if row.given_name and row.surname:
            bucket["full_names"].append(f"{row.given_name} {row.surname}")
        # Aliases: nickname + romanized + maiden surname.
        for alias in (row.nickname, row.romanized, row.maiden_surname):
            if alias:
                bucket["aliases"].append(alias)

    persons: dict[uuid.UUID, PersonNames] = {
        pid: PersonNames(
            person_id=pid,
            given=data["given"],
            surname=data["surname"],
            full_names=tuple(data["full_names"]),
            aliases=tuple(data["aliases"]),
        )
        for pid, data in by_person.items()
    }

    return TreeContext(traversal=traversal, persons=persons)


def _person_label(names: PersonNames | None) -> str:
    """Удобочитаемое имя из PersonNames для system-prompt'а / refs."""
    if names is None:
        return "(unknown person)"
    if names.full_names:
        return names.full_names[0]
    parts = [p for p in (names.given, names.surname) if p]
    if parts:
        return " ".join(parts)
    return "(unnamed)"


# Простой extractor candidate-фраз для post-hoc reference resolution.
# Splits on punctuation/conjunctions and yields max 5 phrases — каждую
# прогоняем через resolve_reference. Это MVP-эвристика; Phase 10.7d
# заменит на structured-output Claude (tool-use или JSON-mode).
_PHRASE_SPLIT_RE = re.compile(r"[,.;?!\n]+|\bи\b|\band\b", re.IGNORECASE)
_MAX_PHRASES = 5


def _candidate_phrases(text: str) -> list[str]:
    """Эвристически разбивает user-input на phrase-кандидаты для resolver'а."""
    phrases: list[str] = []
    seen: set[str] = set()
    for chunk in _PHRASE_SPLIT_RE.split(text):
        phrase = chunk.strip()
        if not phrase:
            continue
        if phrase.lower() in seen:
            continue
        seen.add(phrase.lower())
        phrases.append(phrase)
        if len(phrases) >= _MAX_PHRASES:
            break
    if not phrases:
        phrases.append(text.strip())
    return phrases


def _resolve_person_references(
    text: str,
    *,
    tree: TreeContext,
    anchor_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """Прогоняет phrase-кандидаты через 10.7b ego_resolver и собирает hit'ы.

    Phase 10.7d: переименовано из ``_resolve_user_references`` — функция
    симметрично используется для user-input'а и assistant-output'а;
    «user» в имени мешало.

    Возвращает list dict'ов вида ``{"kind": "person", "person_id": str,
    "mention_text": str, "confidence": float}`` — формат, в котором они
    persist'ятся в ``chat_messages.references_jsonb`` и улетают клиенту.
    """
    refs: list[dict[str, Any]] = []
    seen_ids: set[uuid.UUID] = set()
    for phrase in _candidate_phrases(text):
        try:
            resolved = resolve_reference(tree, anchor_id, phrase)
        except Exception:
            # Резолвер строго pure-functions, но защищаемся от грамматических
            # corner-case'ов — refs не критичны для работы chat'а.
            logger.exception("ego_resolver failed on phrase %r", phrase)
            continue
        if resolved is None or resolved.person_id in seen_ids:
            continue
        seen_ids.add(resolved.person_id)
        refs.append(
            {
                "kind": "person",
                "person_id": str(resolved.person_id),
                "mention_text": phrase,
                "confidence": float(resolved.confidence),
            }
        )
    return refs


# Phase 10.7d source-citation extractor — substring-match по labels (title /
# abbreviation / author) в скоупе дерева. V1: case-insensitive exact-substring
# с пограничной проверкой word-boundary'я (избегаем матча «1900» внутри
# «1900-е» когда title — «1900 census»). Future-work: fuzzy + LLM tool-use.
_MIN_LABEL_LEN = 4  # отсекаем 1-2-символьные abbrev'ы (false-positive шум)
_MAX_SOURCE_REFS = 5


async def _load_source_labels(
    session: AsyncSession,
    *,
    tree_id: uuid.UUID,
) -> list[tuple[uuid.UUID, str]]:
    """Загружает (source_id, label) пары для substring-citation'а.

    Один источник может пройти под несколькими labels (title + abbreviation
    + author) — возвращаем все, caller дедупит по source_id.
    """
    res = await session.execute(
        select(Source.id, Source.title, Source.abbreviation, Source.author).where(
            Source.tree_id == tree_id,
            Source.deleted_at.is_(None),
        )
    )
    out: list[tuple[uuid.UUID, str]] = []
    for sid, title, abbreviation, author in res.all():
        for label in (title, abbreviation, author):
            if label and len(label.strip()) >= _MIN_LABEL_LEN:
                out.append((sid, label.strip()))
    return out


def _resolve_source_citations(
    text: str,
    *,
    source_labels: list[tuple[uuid.UUID, str]],
) -> list[dict[str, Any]]:
    """Substring-find source labels в тексте; возвращает source-references.

    Дедупим по ``source_id`` (первый match выигрывает); cap на
    ``_MAX_SOURCE_REFS`` чтобы JSONB-row не разрастался на длинных текстах
    с множеством упоминаний.
    """
    refs: list[dict[str, Any]] = []
    seen_ids: set[uuid.UUID] = set()
    text_lower = text.lower()
    for source_id, label in source_labels:
        if source_id in seen_ids:
            continue
        # Word-boundary check: \b ловит «1900 census» в «based on 1900 census»
        # но не в «in1900census». re.escape защищает от regex-метасимволов
        # в title'е (точки в «U.S. Census»).
        pattern = re.compile(rf"\b{re.escape(label.lower())}\b")
        if pattern.search(text_lower):
            seen_ids.add(source_id)
            refs.append(
                {
                    "kind": "source",
                    "source_id": str(source_id),
                    "mention_text": label,
                    "confidence": 1.0,
                }
            )
            if len(refs) >= _MAX_SOURCE_REFS:
                break
    return refs


# Auto-title из первого user-сообщения. Берём первые ~60 символов первой
# непустой линии. UI отрисовывает в sessions list; полный текст сообщения
# доступен через GET /messages.
_TITLE_MAX_LEN = 60


def _derive_session_title(message: str) -> str | None:
    """First line trimmed to ``_TITLE_MAX_LEN`` chars; ``None`` для пустого ввода."""
    for raw_line in message.splitlines():
        line = raw_line.strip()
        if line:
            if len(line) > _TITLE_MAX_LEN:
                return line[: _TITLE_MAX_LEN - 1].rstrip() + "…"
            return line
    return None


def _build_system_prompt(
    *,
    anchor_label: str,
    person_count: int,
    anchor_relations: str,
    sealed_scopes_note: str = "",
) -> str:
    base = _SYSTEM_PROMPT_TEMPLATE.format(
        anchor_label=anchor_label,
        person_count=person_count,
        anchor_relations=anchor_relations or "- (no close relations indexed)",
    )
    # Phase 15.11c: append опечатанные scope'ы как явное do-not-suggest
    # указание для LLM. Пусто → ничего не добавляется (нет лишнего шума
    # для деревьев без active assertions).
    if sealed_scopes_note:
        return f"{base}\n\n{sealed_scopes_note}"
    return base


def _format_sealed_scopes(sealed: frozenset[str]) -> str:
    """Аннотация для system-prompt'а: какие scope'ы анкора уже опечатаны.

    Phase 15.11c (ADR-0082): LLM получает явное «do-not-suggest»-указание
    для исчерпанных scope'ов, чтобы не предлагать «search for another
    sibling/spouse/parent/child» когда owner уже зафиксировал полноту.
    Пустой ``frozenset`` → пустая строка (chat'у нечего добавить в prompt).
    """
    if not sealed:
        return ""
    listed = ", ".join(sorted(sealed))
    return (
        f"Sealed scopes for anchor: {listed}. "
        "Do NOT suggest searching for additional members of these scopes "
        "(owner has marked them exhaustive)."
    )


def _format_anchor_relations(
    *,
    anchor_id: uuid.UUID,
    tree: TreeContext,
    limit: int = 8,
) -> str:
    """Однострочные labels для нескольких ближайших родственников anchor'а.

    Пробегаем по spouse / parents / children rebpёрах traversal'а — UI
    показывает их как «контекст на скриншоте», но и LLM полезно увидеть
    круг "знакомых имён" для grounding'а.
    """
    related: list[tuple[str, uuid.UUID]] = []
    fams = tree.traversal.person_to_spouse_families.get(anchor_id, ())
    for fam_id in fams:
        node = tree.traversal.families.get(fam_id)
        if node is None:
            continue
        for sup in (node.husband_id, node.wife_id):
            if sup is not None and sup != anchor_id:
                related.append(("spouse", sup))
        for child in node.child_ids:
            related.append(("child", child))
    parent_fams = tree.traversal.person_to_parent_families.get(anchor_id, ())
    for fam_id in parent_fams:
        node = tree.traversal.families.get(fam_id)
        if node is None:
            continue
        for sup in (node.husband_id, node.wife_id):
            if sup is not None:
                related.append(("parent", sup))
        for child in node.child_ids:
            if child != anchor_id:
                related.append(("sibling", child))
    if not related:
        return ""
    lines: list[str] = []
    seen: set[uuid.UUID] = set()
    for kind, pid in related:
        if pid in seen:
            continue
        seen.add(pid)
        label = _person_label(tree.persons.get(pid))
        lines.append(f"- {kind}: {label}")
        if len(lines) >= limit:
            break
    return "Close relations:\n" + "\n".join(lines)


# -----------------------------------------------------------------------------
# Endpoint.
# -----------------------------------------------------------------------------


@router.post(
    "/trees/{tree_id}/chat/turn",
    response_class=EventSourceResponse,
    summary="One conversational turn — SSE-streamed assistant reply (Phase 10.7c).",
    dependencies=[Depends(require_tree_role(TreeRole.VIEWER))],
)
async def chat_turn(
    tree_id: uuid.UUID,
    body: ChatTurnRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
    anthropic: Annotated[AnthropicClient, Depends(get_anthropic_client)],
) -> EventSourceResponse:
    """Один turn — резолвит references, стримит ответ Claude, persist'ит обе stороны."""
    tree = await session.get(Tree, tree_id)
    if tree is None:  # pragma: no cover — gate уже отдал 404
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Tree {tree_id} not found")

    chat_session = await _load_or_create_session(
        session,
        tree=tree,
        user_id=user.id,
        request=body,
    )
    # Resolve anchor: snapshot сессии — source of truth.
    anchor_id = chat_session.anchor_person_id
    if anchor_id is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=(
                f"Tree {tree_id} has no self-anchor; "
                f"PATCH /trees/{tree_id}/owner-person before starting chat."
            ),
        )

    # Persist user message immediately — даже если LLM упадёт, history будет
    # консистентен (assistant-сообщение появится только при успехе).
    tree_ctx = await _load_tree_context(session, tree_id=tree_id)
    source_labels = await _load_source_labels(session, tree_id=tree_id)
    user_refs = _resolve_person_references(body.message, tree=tree_ctx, anchor_id=anchor_id)
    user_refs.extend(_resolve_source_citations(body.message, source_labels=source_labels))
    user_msg = ChatMessage(
        session_id=chat_session.id,
        role=ChatMessageRole.USER.value,
        content=body.message,
        references=user_refs,
    )
    session.add(user_msg)
    # Phase 10.7d: auto-derive title из первого user-сообщения. Только если
    # title ещё пустой — повторные turn'ы не переписывают (UI хочет
    # стабильный label в sessions list'е).
    if chat_session.title is None:
        chat_session.title = _derive_session_title(body.message)
    await session.flush()

    # Build system prompt + load prior turns (capped) for multi-turn coherence.
    history = await _load_session_history(session, session_id=chat_session.id)
    anchor_label = _person_label(tree_ctx.persons.get(anchor_id))
    person_count = len(tree_ctx.persons)
    anchor_relations = _format_anchor_relations(anchor_id=anchor_id, tree=tree_ctx)
    # Phase 15.11c: query sealed scopes для анкора (одним SQL); пусто если
    # owner ничего не закреплял. Передаём как готовый prompt-fragment,
    # чтобы _build_system_prompt оставался чистым строко-форматтером.
    sealed_for_anchor = await sealed_scopes_for_person(session, anchor_id)
    system_prompt = _build_system_prompt(
        anchor_label=anchor_label,
        person_count=person_count,
        anchor_relations=anchor_relations,
        sealed_scopes_note=_format_sealed_scopes(frozenset(s.value for s in sealed_for_anchor)),
    )

    # Commit user-side state; assistant-side flushed после стрима.
    await session.commit()

    return EventSourceResponse(
        _stream_turn(
            anthropic=anthropic,
            system_prompt=system_prompt,
            history=history,
            session_id=chat_session.id,
            anchor_person_id=anchor_id,
            user_refs=user_refs,
            tree_ctx=tree_ctx,
            source_labels=source_labels,
        ),
        ping=15,
    )


# -----------------------------------------------------------------------------
# Internal — session lookup/create, history load, streaming generator.
# -----------------------------------------------------------------------------


async def _load_or_create_session(
    session: AsyncSession,
    *,
    tree: Tree,
    user_id: uuid.UUID,
    request: ChatTurnRequest,
) -> ChatSession:
    """Достаёт существующую сессию (validation owned by caller) или создаёт новую.

    Если ``request.session_id`` указан, но не принадлежит ``(tree.id, user_id)``,
    возвращаем 404 — leak protection (нельзя спросить «существует ли чужой
    session_id» через timing).
    """
    if request.session_id is not None:
        existing = await session.scalar(
            select(ChatSession).where(
                ChatSession.id == request.session_id,
                ChatSession.tree_id == tree.id,
                ChatSession.user_id == user_id,
            )
        )
        if existing is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail=f"Chat session {request.session_id} not found",
            )
        return existing

    # Создаём новую — anchor может быть в request, иначе fallback на tree owner.
    anchor_id = request.anchor_person_id or tree.owner_person_id
    if request.anchor_person_id is not None:
        # Validate провайденный anchor принадлежит дереву.
        anchor_tree_id = await session.scalar(
            select(Person.tree_id).where(
                Person.id == request.anchor_person_id,
                Person.deleted_at.is_(None),
            )
        )
        if anchor_tree_id is None or anchor_tree_id != tree.id:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Person {request.anchor_person_id} is not in tree {tree.id}; "
                    f"cannot anchor chat session."
                ),
            )

    new_session = ChatSession(
        tree_id=tree.id,
        user_id=user_id,
        anchor_person_id=anchor_id,
        title=None,
    )
    session.add(new_session)
    await session.flush()
    return new_session


_HISTORY_LIMIT = 20


async def _load_session_history(
    session: AsyncSession,
    *,
    session_id: uuid.UUID,
) -> list[dict[str, str]]:
    """Загружает последние ``_HISTORY_LIMIT`` сообщений сессии в Anthropic-формате.

    Возвращает list ``{"role": "user"|"assistant", "content": "..."}``.
    Системные сообщения исключаются — они UI-only и в Anthropic не отправляются.
    """
    res = await session.execute(
        select(ChatMessage.role, ChatMessage.content)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at)
        .limit(_HISTORY_LIMIT)
    )
    return [
        {"role": row.role, "content": row.content}
        for row in res.all()
        if row.role in (ChatMessageRole.USER.value, ChatMessageRole.ASSISTANT.value)
    ]


async def _stream_turn(
    *,
    anthropic: AnthropicClient,
    system_prompt: str,
    history: list[dict[str, str]],
    session_id: uuid.UUID,
    anchor_person_id: uuid.UUID,
    user_refs: list[dict[str, Any]],
    tree_ctx: TreeContext,
    source_labels: list[tuple[uuid.UUID, str]],
) -> AsyncIterator[dict[str, str]]:
    """Async-генератор SSE-кадров: session → tokens → done | error.

    Phase 10.7d: post-stream резолвит references в assistant-output'е (person
    через ego_resolver, source через substring) и persist'ит в
    ``ChatMessage.references_jsonb`` рядом с user-side references из
    ``user_refs``. Persists assistant-сообщение в свою transaction после
    полного ответа, чтобы partial response не оставался в истории при
    разрыве connection'а.
    """
    yield {
        "data": json.dumps(
            {
                "type": "session",
                "session_id": str(session_id),
                "anchor_person_id": str(anchor_person_id),
            }
        )
    }

    full_text_parts: list[str] = []
    try:
        async for delta in anthropic.stream_completion(
            system=system_prompt,
            messages=history,
        ):
            full_text_parts.append(delta)
            yield {"data": json.dumps({"type": "token", "delta": delta})}
    except AILayerDisabledError as exc:
        yield {"data": json.dumps({"type": "error", "detail": str(exc)})}
        return
    except Exception as exc:
        logger.exception("anthropic stream failed for session %s", session_id)
        yield {"data": json.dumps({"type": "error", "detail": f"LLM error: {exc}"})}
        return

    full_text = "".join(full_text_parts).strip()
    if not full_text:
        yield {"data": json.dumps({"type": "error", "detail": "Empty assistant response"})}
        return

    # Phase 10.7d: символично у assistant'а тоже резолвим references —
    # tree_ctx + source_labels уже загружены в request-scope'е.
    assistant_refs = _resolve_person_references(
        full_text, tree=tree_ctx, anchor_id=anchor_person_id
    )
    assistant_refs.extend(_resolve_source_citations(full_text, source_labels=source_labels))

    # Persist assistant-сообщение в отдельной session — генератор живёт за
    # пределами request scope, нужно открыть новую DB session.
    factory = get_session_factory()
    assistant_msg_id: uuid.UUID
    async with factory() as persist_session:
        assistant_msg = ChatMessage(
            session_id=session_id,
            role=ChatMessageRole.ASSISTANT.value,
            content=full_text,
            references=assistant_refs,
        )
        persist_session.add(assistant_msg)
        await persist_session.flush()
        assistant_msg_id = assistant_msg.id
        await persist_session.commit()

    yield {
        "data": json.dumps(
            {
                "type": "done",
                "message_id": str(assistant_msg_id),
                "referenced_persons": user_refs,
                "assistant_references": assistant_refs,
            }
        )
    }


# -----------------------------------------------------------------------------
# Phase 10.7d — history endpoints (sessions list, session get, messages list).
# -----------------------------------------------------------------------------


_DEFAULT_SESSIONS_LIMIT = 20
_MAX_SESSIONS_LIMIT = 100
_DEFAULT_MESSAGES_LIMIT = 50
_MAX_MESSAGES_LIMIT = 200


@router.get(
    "/trees/{tree_id}/chat/sessions",
    response_model=ChatSessionListResponse,
    summary="Paginated list of current user's chat sessions in a tree (Phase 10.7d).",
    dependencies=[Depends(require_tree_role(TreeRole.VIEWER))],
)
async def list_chat_sessions(
    tree_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
    limit: int = Query(_DEFAULT_SESSIONS_LIMIT, ge=1, le=_MAX_SESSIONS_LIMIT),
    offset: int = Query(0, ge=0),
) -> ChatSessionListResponse:
    """Список чат-сессий пользователя в дереве c message_count + last_message_at.

    Сортировка — по ``last_message_at DESC NULLS LAST, created_at DESC``,
    чтобы активные диалоги были сверху и пустые-новые сессии не вытесняли
    их в конец. Permission: VIEWER+.

    Pagination: limit/offset (репо-конвенция, см. /trees/{id}/sources).
    """
    # Subquery: per-session aggregates (count + max created_at).
    # LEFT JOIN — пустая сессия (без сообщений ещё) тоже попадает в выдачу.
    msg_count = func.count(ChatMessage.id).label("message_count")
    last_msg_at = func.max(ChatMessage.created_at).label("last_message_at")

    base_filter = (
        ChatSession.tree_id == tree_id,
        ChatSession.user_id == user.id,
    )

    total = (
        await session.execute(select(func.count(ChatSession.id)).where(*base_filter))
    ).scalar_one()

    q = (
        select(ChatSession, msg_count, last_msg_at)
        .outerjoin(ChatMessage, ChatMessage.session_id == ChatSession.id)
        .where(*base_filter)
        .group_by(ChatSession.id)
        .order_by(last_msg_at.desc().nullslast(), ChatSession.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await session.execute(q)).all()

    items = [
        ChatSessionListItem.model_validate(
            {
                "id": s.id,
                "tree_id": s.tree_id,
                "anchor_person_id": s.anchor_person_id,
                "title": s.title,
                "created_at": s.created_at,
                "updated_at": s.updated_at,
                "message_count": int(count or 0),
                "last_message_at": last_at,
            }
        )
        for s, count, last_at in rows
    ]

    return ChatSessionListResponse(
        tree_id=tree_id,
        total=int(total or 0),
        limit=limit,
        offset=offset,
        items=items,
    )


@router.get(
    "/trees/{tree_id}/chat/sessions/{session_id}",
    response_model=ChatSessionResponse,
    summary="Single chat session metadata (Phase 10.7d).",
    dependencies=[Depends(require_tree_role(TreeRole.VIEWER))],
)
async def get_chat_session(
    tree_id: uuid.UUID,
    session_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> ChatSessionResponse:
    """Single-session metadata. 404 на чужие session_id (leak-protection)."""
    chat_session = await session.scalar(
        select(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.tree_id == tree_id,
            ChatSession.user_id == user.id,
        )
    )
    if chat_session is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Chat session {session_id} not found"
        )
    return ChatSessionResponse.model_validate(chat_session)


@router.get(
    "/trees/{tree_id}/chat/sessions/{session_id}/messages",
    response_model=ChatMessageListResponse,
    summary="Paginated message history for a chat session (Phase 10.7d).",
    dependencies=[Depends(require_tree_role(TreeRole.VIEWER))],
)
async def list_chat_session_messages(
    tree_id: uuid.UUID,
    session_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
    limit: int = Query(_DEFAULT_MESSAGES_LIMIT, ge=1, le=_MAX_MESSAGES_LIMIT),
    offset: int = Query(0, ge=0),
) -> ChatMessageListResponse:
    """История сообщений сессии в хронологическом порядке.

    Permission: VIEWER+ (router-level) + ownership-check (404 на чужой
    session_id). Сортировка — ``created_at ASC`` (старые сверху, как
    обычно UI рисует chat-thread).
    """
    # Ownership check: убеждаемся, что session принадлежит (tree_id, user_id).
    owned = await session.scalar(
        select(ChatSession.id).where(
            ChatSession.id == session_id,
            ChatSession.tree_id == tree_id,
            ChatSession.user_id == user.id,
        )
    )
    if owned is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Chat session {session_id} not found"
        )

    total = (
        await session.execute(
            select(func.count(ChatMessage.id)).where(ChatMessage.session_id == session_id)
        )
    ).scalar_one()

    msgs_res = await session.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
        .limit(limit)
        .offset(offset)
    )
    items = [ChatMessageResponse.model_validate(m) for m in msgs_res.scalars().all()]

    return ChatMessageListResponse(
        session_id=session_id,
        total=int(total or 0),
        limit=limit,
        offset=offset,
        items=items,
    )


__all__ = ["get_ai_layer_config", "get_anthropic_client", "router"]
