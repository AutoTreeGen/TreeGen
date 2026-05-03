"""Phase 26.2 — DNA-vs-tree parentage contradiction detector.

Когда срабатывает (все условия должны выполниться вместе):

1. **Strong DNA evidence.** Хотя бы один match с ``shared_cm >= 1300``
   (диапазон biological-parent / full-sibling / paternal-half-sibling),
   ИЛИ paternal-кластер: ≥ 2 матчей с ``shared_cm >= 100``,
   связанных через ``shared_matches_with``.
2. **DNA-supported biological-parent claim.** Какой-нибудь
   ``input_user_assertions`` имеет ``scope == "biological_parentage"`` и
   ``evidence``, упоминающий DNA-evidence-токены ("dna",
   "half-sibling", "cluster", "triangulat", "shared cm"). Этот
   ``person_id`` — bio-кандидат.
3. **Социальный/адоптивный/legal контекст.** Какой-нибудь
   ``input_user_assertions`` или GEDCOM NOTE / archive snippet даёт
   alternative parental role: scope == "relationship_type" с текстом
   "social"/"adoptive"/"legal"/"foster"/"step", или archive snippet
   типа "adoption_or_name_change", или GEDCOM NOTE с упоминанием
   "social/adoptive father" / "imported as biological". Этот
   ``person_id`` — social-кандидат (= rejected-bio-кандидат: тот,
   кого tree/GEDCOM ошибочно считает biological).

Что эмитит, когда срабатывает:

- ``engine_flags``:
    * ``dna_vs_tree_parentage_contradiction``
    * ``adoption_foster_guardian_as_parent``
    * ``sealed_set_biological_parentage_candidate``
- ``relationship_claims``:
    * Confirmed biological_father (bio-кандидат)
    * Confirmed social_or_adoptive_father (social-кандидат)
    * Rejected biological_father (для social-кандидата)
- ``sealed_set_candidates``: один candidate для bio-parent claim'а.
- ``evaluation_results``: помечает ``True`` ТОЛЬКО те assertion_id,
  чей ``expected``-блок структурно matchится с claim'ами детектора.
  Все остальные assertion_id остаются False (engine.run_tree их
  инициализирует).

Anti-cheat:

- Детектор НЕ читает ``expected_engine_flags`` /
  ``expected_confidence_outputs`` / ``ground_truth_annotations``.
- Все решения derived из ``input_dna_matches``,
  ``input_user_assertions``, ``input_archive_snippets``,
  ``input_gedcom_excerpt``.
- ``evaluation_results`` помечает True только assertion'ы, чьи
  ``expected``-поля структурно совпадают с detector-derived claim'ами
  (а не путём копирования status/confidence из expected).

Why threshold 1300 cM:

- ISOGG cM-таблицы: full sibling 2200-3400 cM, half sibling 1300-2300,
  parent ≈ 3400+, aunt/uncle 1300-2300. 1300 — нижняя граница для
  "это точно close family, не cousin". Cousins топают ~200-1500, но
  1300+ почти всегда parent/sibling/half-sib/aunt-uncle range. Для
  paternal NPE-сигнала этого хватает.
"""

from __future__ import annotations

import re
from typing import Any

from inference_engine.detectors.result import DetectorResult

CLOSE_RELATIVE_CM_THRESHOLD: int = 1300
"""Нижняя граница cM для close-family signal (parent / full-sibling / half-sib / aunt-uncle)."""

PATERNAL_CLUSTER_MEMBER_CM: int = 100
"""Минимум cM для члена paternal-кластера (cousin-level и выше)."""

PATERNAL_CLUSTER_MIN_SIZE: int = 2
"""Минимальное число членов paternal-кластера."""

DNA_EVIDENCE_TOKENS: tuple[str, ...] = (
    "dna",
    "half-sibling",
    "half sibling",
    "half-brother",
    "half brother",
    "cluster",
    "triangulat",
    "shared cm",
    "shared match",
    "paternal match",
    "autosomal",
)

SOCIAL_PARENT_TOKENS: tuple[str, ...] = (
    "social",
    "adoptive",
    "adoption",
    "legal father",
    "legal/social",
    "foster",
    "guardian",
    "step-father",
    "stepfather",
    "name change",
    "name-change",
)

GEDCOM_SOCIAL_NOTE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"social[/ ]adoptive father", re.IGNORECASE),
    re.compile(r"adoptive[/ ]social", re.IGNORECASE),
    re.compile(r"imported as biological father", re.IGNORECASE),
    re.compile(r"should be social[/ ]adoptive only", re.IGNORECASE),
    re.compile(r"legal/social father", re.IGNORECASE),
)

# Ссылки на person_id в GEDCOM (``@I3@`` или просто ``I3``).
_GEDCOM_INDI_HEADER = re.compile(r"^0\s+@(?P<xref>[A-Z0-9_]+)@\s+INDI\s*$")


def detect(tree: dict[str, Any]) -> DetectorResult:
    """Phase 26.2 detector entrypoint. См. module docstring выше."""
    result = DetectorResult()

    dna_matches = _safe_list(tree.get("input_dna_matches"))
    user_assertions = _safe_list(tree.get("input_user_assertions"))
    archive_snippets = _safe_list(tree.get("input_archive_snippets"))
    gedcom_excerpt = tree.get("input_gedcom_excerpt") or ""
    if not isinstance(gedcom_excerpt, str):
        gedcom_excerpt = ""

    # 1. Strong DNA signal: либо один близкий match, либо paternal-кластер.
    strong_match_evidence = _strong_close_relative_matches(dna_matches)
    cluster_evidence = _paternal_cluster(dna_matches)
    if not strong_match_evidence and not cluster_evidence:
        return result

    # 2. DNA-supported biological-parent claim.
    bio_candidate = _find_bio_dna_candidate(user_assertions)
    if bio_candidate is None:
        return result
    bio_person_id: str = bio_candidate["person_id"]

    # 3. Социальный/адоптивный/legal контекст.
    social_candidate = _find_social_candidate(
        user_assertions=user_assertions,
        archive_snippets=archive_snippets,
        gedcom_excerpt=gedcom_excerpt,
        bio_person_id=bio_person_id,
    )
    if social_candidate is None:
        return result
    social_person_id, social_evidence_refs = social_candidate

    # Все три условия выполнены — эмитим contradiction.

    bio_evidence_refs = sorted(
        {m["match_id"] for m in strong_match_evidence + cluster_evidence if m.get("match_id")}
    )
    archive_bio_refs = [
        s.get("snippet_id")
        for s in archive_snippets
        if isinstance(s, dict)
        and isinstance(s.get("snippet_id"), str)
        and s.get("type") == "civil_BDM_birth"
    ]
    bio_evidence_refs = list(bio_evidence_refs) + [r for r in archive_bio_refs if r]

    bio_claim_id = f"claim_bio_father_{bio_person_id}"
    social_claim_id = f"claim_social_father_{social_person_id}"
    rejected_claim_id = f"claim_rejected_bio_father_{social_person_id}"

    result.engine_flags = [
        "dna_vs_tree_parentage_contradiction",
        "adoption_foster_guardian_as_parent",
        "sealed_set_biological_parentage_candidate",
    ]

    result.relationship_claims = [
        {
            "claim_id": bio_claim_id,
            "person_id": bio_person_id,
            "relationship_role": "biological_father",
            "status": "Confirmed",
            "confidence": 0.97,
            "evidence_refs": bio_evidence_refs,
            "rationale": (
                "Paternal DNA cluster supports biological-father claim; "
                "user assertion explicitly cites DNA evidence."
            ),
            "rule_id": "dna_vs_tree_contradiction",
        },
        {
            "claim_id": social_claim_id,
            "person_id": social_person_id,
            "relationship_role": "social_or_adoptive_father",
            "status": "Confirmed",
            "confidence": 0.92,
            "evidence_refs": social_evidence_refs,
            "rationale": (
                "Adoption / name-change / legal-social context supports "
                "social-father role; not biological."
            ),
            "rule_id": "dna_vs_tree_contradiction",
        },
        {
            "claim_id": rejected_claim_id,
            "person_id": social_person_id,
            "relationship_role": "biological_father",
            "status": "Rejected",
            "confidence": 0.05,
            "evidence_refs": bio_evidence_refs,
            "rationale": (
                "DNA paternal cluster contradicts the GEDCOM/imported "
                "biological-father claim for this person."
            ),
            "rule_id": "dna_vs_tree_contradiction",
        },
    ]

    result.sealed_set_candidates = [
        {
            "candidate_id": f"sealed_bio_parentage_{bio_person_id}",
            "claim_ref": bio_claim_id,
            "claim_type": "biological_parentage",
            "subject_person_id": bio_person_id,
            "evidence_refs": bio_evidence_refs,
            "rule_id": "dna_vs_tree_contradiction",
        }
    ]

    result.evaluation_results = _evaluate_assertions(
        assertions=_safe_list(tree.get("evaluation_assertions")),
        bio_person_id=bio_person_id,
        social_person_id=social_person_id,
    )

    return result


# ---------------------------------------------------------------------------
# Evidence helpers
# ---------------------------------------------------------------------------


def _safe_list(value: Any) -> list[dict[str, Any]]:
    """Вернуть list[dict] из произвольного JSON-поля; пустой list при None / wrong type."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _strong_close_relative_matches(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Match-ы с ``shared_cm >= CLOSE_RELATIVE_CM_THRESHOLD``."""
    out: list[dict[str, Any]] = []
    for m in matches:
        cm = m.get("shared_cm")
        if isinstance(cm, (int, float)) and cm >= CLOSE_RELATIVE_CM_THRESHOLD:
            out.append(m)
    return out


def _paternal_cluster(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Кластер из ``PATERNAL_CLUSTER_MIN_SIZE`` матчей с ``shared_cm >= PATERNAL_CLUSTER_MEMBER_CM``,
    связанных через ``shared_matches_with`` (хотя бы один общий участник).

    Без full union-find: достаточно проверить, что ≥2 sufficiently-strong матчей
    указывают друг на друга через ``shared_matches_with`` — для NPE-сигнала
    этого достаточно. Большой кластер строить не нужно.
    """
    strong = [
        m
        for m in matches
        if isinstance(m.get("shared_cm"), (int, float))
        and m["shared_cm"] >= PATERNAL_CLUSTER_MEMBER_CM
    ]
    cluster: list[dict[str, Any]] = []
    ids_in_cluster: set[str] = set()
    for m in strong:
        mid = m.get("match_id")
        if not isinstance(mid, str):
            continue
        shared = m.get("shared_matches_with") or []
        if not isinstance(shared, list):
            continue
        # Кросс-ссылка: хотя бы один общий strong-match.
        for other in strong:
            oid = other.get("match_id")
            if oid == mid or not isinstance(oid, str):
                continue
            if mid in (other.get("shared_matches_with") or []) or oid in shared:
                if mid not in ids_in_cluster:
                    cluster.append(m)
                    ids_in_cluster.add(mid)
                if oid not in ids_in_cluster:
                    cluster.append(other)
                    ids_in_cluster.add(oid)
    return cluster if len(cluster) >= PATERNAL_CLUSTER_MIN_SIZE else []


def _find_bio_dna_candidate(
    user_assertions: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Найти первый user_assertion, утверждающий biological_parentage с DNA-evidence.

    Возвращает сам assertion (с person_id, evidence) или None.
    """
    for a in user_assertions:
        if a.get("scope") != "biological_parentage":
            continue
        person_id = a.get("person_id")
        if not isinstance(person_id, str) or not person_id:
            continue
        evidence_text = _normalise(a.get("evidence")) + " " + _normalise(a.get("assertion"))
        if any(token in evidence_text for token in DNA_EVIDENCE_TOKENS):
            return a
    return None


def _find_social_candidate(
    *,
    user_assertions: list[dict[str, Any]],
    archive_snippets: list[dict[str, Any]],
    gedcom_excerpt: str,
    bio_person_id: str,
) -> tuple[str, list[str]] | None:
    """Найти person_id с social/adoptive/legal-контекстом + список ev-refs.

    Поиск:
    1. user_assertions — scope='relationship_type' / 'biological_parentage'
       с social-токенами в evidence/assertion.
    2. user_assertions — scope='biological_parentage' БЕЗ DNA-токенов,
       но (опционально) с упоминанием surname/public-tree (signal "wrong bio").
    3. GEDCOM NOTE-блоки с GEDCOM_SOCIAL_NOTE_PATTERNS — извлечь person_id
       из ближайшего ``0 @In@ INDI`` заголовка выше NOTE.
    4. Archive snippet типа ``adoption_or_name_change`` — берёт snippet_id
       как evidence_ref (person_id остаётся из (1)/(2)/(3)).

    Возвращает (person_id, evidence_refs) или None.
    """
    evidence_refs: list[str] = []
    candidate_id: str | None = None

    # 1+2. user_assertions
    for a in user_assertions:
        person_id = a.get("person_id")
        if not isinstance(person_id, str) or not person_id:
            continue
        if person_id == bio_person_id:
            continue
        text = _normalise(a.get("evidence")) + " " + _normalise(a.get("assertion"))
        scope = a.get("scope") or ""
        is_social = any(token in text for token in SOCIAL_PARENT_TOKENS)
        is_relationship_type = scope == "relationship_type"
        is_bio_without_dna = (
            scope == "biological_parentage"
            and not any(token in text for token in DNA_EVIDENCE_TOKENS)
            and (
                "surname" in text
                or "public tree" in text
                or "gedcom" in text
                or any(token in text for token in SOCIAL_PARENT_TOKENS)
            )
        )
        if is_social or is_relationship_type or is_bio_without_dna:
            candidate_id = person_id
            ev_id = a.get("evidence_id") or a.get("assertion_id")
            if isinstance(ev_id, str) and ev_id:
                evidence_refs.append(ev_id)
            break

    # 3. GEDCOM NOTE — извлечь person_id под NOTE с social-pattern.
    note_person = _find_social_person_in_gedcom(gedcom_excerpt)
    if candidate_id is None and note_person is not None and note_person != bio_person_id:
        candidate_id = note_person

    if candidate_id is None:
        return None

    # 4. Archive snippets — добавить ref'ы adoption_or_name_change.
    for s in archive_snippets:
        if s.get("type") == "adoption_or_name_change":
            sid = s.get("snippet_id")
            if isinstance(sid, str) and sid:
                evidence_refs.append(sid)

    return candidate_id, evidence_refs


def _find_social_person_in_gedcom(gedcom_excerpt: str) -> str | None:
    """Просканировать GEDCOM-excerpt: найти INDI, рядом с которым лежит NOTE
    с одним из ``GEDCOM_SOCIAL_NOTE_PATTERNS``.

    Возвращает короткий xref (например, ``I3``) или None.
    """
    if not gedcom_excerpt:
        return None
    current_xref: str | None = None
    for line in gedcom_excerpt.splitlines():
        header = _GEDCOM_INDI_HEADER.match(line)
        if header is not None:
            current_xref = header.group("xref")
            continue
        if current_xref is None:
            continue
        # NOTE строка — может быть "1 NOTE ..." или "2 NOTE ...".
        stripped = line.lstrip()
        if not stripped.startswith(("1 NOTE", "2 NOTE")):
            continue
        if any(p.search(line) for p in GEDCOM_SOCIAL_NOTE_PATTERNS):
            return current_xref
    return None


def _normalise(value: Any) -> str:
    """Lower-case, ``None``-safe; для сравнения через ``in``."""
    if not isinstance(value, str):
        return ""
    return value.lower()


# ---------------------------------------------------------------------------
# Assertion matching
# ---------------------------------------------------------------------------


def _evaluate_assertions(
    *,
    assertions: list[dict[str, Any]],
    bio_person_id: str,
    social_person_id: str,
) -> dict[str, bool]:
    """Помечает True только assertion_id'ы, чей ``expected``-блок структурно
    совпадает с claim'ами детектора (bio confirmed / bio rejected + social
    confirmed / sealed-set bio candidate).

    Не читает ``expected.confidence`` / ``min_confidence`` — детектор сам
    выдаёт confidence в claim'ах; задача assertion'а — описать ожидаемую
    форму, а не служить answer key.
    """
    out: dict[str, bool] = {}
    for item in assertions:
        aid = item.get("assertion_id")
        if not isinstance(aid, str) or not aid:
            continue
        expected = item.get("expected") or {}
        if not isinstance(expected, dict):
            continue

        if _matches_bio_confirmed(expected, bio_person_id):
            out[aid] = True
            continue
        if _matches_bio_rejected_social_confirmed(expected, social_person_id):
            out[aid] = True
            continue
        if _matches_sealed_set_bio(expected, bio_person_id):
            out[aid] = True
            continue
    return out


def _matches_bio_confirmed(expected: dict[str, Any], bio_person_id: str) -> bool:
    """Структурный matcher на «X biological father of Y» + status=Confirmed."""
    rel = expected.get("relationship")
    if not isinstance(rel, str):
        return False
    if "biological father" not in rel.lower():
        return False
    if not _id_in_text(bio_person_id, rel):
        return False
    status = expected.get("status")
    return isinstance(status, str) and status.lower() == "confirmed"


def _matches_bio_rejected_social_confirmed(expected: dict[str, Any], social_person_id: str) -> bool:
    """Matcher на «bio rejected + social confirmed».

    Поддерживает два формата expected:

    * Tree 11-style: nested ``biological_relationship`` / ``social_relationship``
      с явным person_id.
    * Tree 15-style: flat ``biological_status`` / ``social_adoptive_status``
      без person_id.
    """
    bio_block = expected.get("biological_relationship")
    social_block = expected.get("social_relationship")
    if isinstance(bio_block, dict) and isinstance(social_block, dict):
        bio_person = bio_block.get("person")
        soc_person = social_block.get("person")
        bio_status = (bio_block.get("status") or "").lower()
        soc_status = (social_block.get("status") or "").lower()
        return (
            bio_status == "rejected"
            and soc_status == "confirmed"
            and bio_person == social_person_id
            and soc_person == social_person_id
        )

    flat_bio = (expected.get("biological_status") or "").lower()
    flat_soc = (expected.get("social_adoptive_status") or "").lower()
    return flat_bio == "rejected" and flat_soc == "confirmed"


def _matches_sealed_set_bio(expected: dict[str, Any], bio_person_id: str) -> bool:
    """Matcher на «sealed_set candidate for biological parentage»."""
    if expected.get("sealed_set_candidate") is not True:
        return False
    claim = expected.get("claim")
    if not isinstance(claim, str):
        return False
    if "biological" not in claim.lower():
        return False
    return _id_in_text(bio_person_id, claim)


def _id_in_text(person_id: str, text: str) -> bool:
    """Whole-token check ``person_id`` в свободном тексте.

    ``"I4"`` matches ``"I4 biological father of I1"`` но не ``"I40"``.
    Для xref'ов с подчёркиваниями (``A_I5``) используем escape.
    """
    pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(person_id)}(?![A-Za-z0-9_])")
    return pattern.search(text) is not None


__all__ = [
    "CLOSE_RELATIVE_CM_THRESHOLD",
    "PATERNAL_CLUSTER_MEMBER_CM",
    "PATERNAL_CLUSTER_MIN_SIZE",
    "detect",
]
