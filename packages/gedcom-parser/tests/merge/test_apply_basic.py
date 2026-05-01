"""Phase 5.7b — pure-логика Safe Merge applier'а.

Эти тесты не трогают БД: проверяют ``apply_diff_pure`` на in-memory
TreeSnapshot'ах. Интеграционные тесты с реальной транзакцией —
``services/parser-service/tests/test_safe_merge_api.py``.
"""

from __future__ import annotations

from gedcom_parser.merge import (
    Audit,
    Change,
    Conflict,
    DiffReport,
    FieldChange,
    MergePolicy,
    PersonAdd,
    PersonModify,
    PersonRecord,
    PersonRemove,
    RelationAdd,
    RelationRecord,
    RelationRemove,
    TreeSnapshot,
    apply_diff_pure,
)


def _empty_target() -> TreeSnapshot:
    return TreeSnapshot()


def _person(xref: str, **fields: object) -> PersonRecord:
    return PersonRecord(xref=xref, fields=dict(fields))


# ---------------------------------------------------------------------------
# Базовая семантика
# ---------------------------------------------------------------------------


def test_person_added_to_empty_tree() -> None:
    """1 person_added в пустое дерево → 1 applied, 0 skipped, не aborted."""
    target = _empty_target()
    diff = DiffReport(persons_added=[PersonAdd(xref="@I1@", fields={"sex": "M"})])
    policy = MergePolicy(on_conflict="manual")

    result = apply_diff_pure(target, diff, policy)

    assert not result.aborted
    assert len(result.applied) == 1
    assert result.applied[0].kind == "person_added"
    assert result.applied[0].xref == "@I1@"
    assert result.skipped == []
    # log должен содержать одну applied-запись.
    actions = [a.action for a in result.log]
    assert actions == ["applied"]


def test_person_added_idempotent_when_identical() -> None:
    """Повторный persons_added на идентичную персону — no-op (не applied, не skipped)."""
    target = TreeSnapshot(persons={"@I1@": _person("@I1@", sex="M")})
    diff = DiffReport(persons_added=[PersonAdd(xref="@I1@", fields={"sex": "M"})])
    policy = MergePolicy(on_conflict="manual")

    result = apply_diff_pure(target, diff, policy)

    assert not result.aborted
    assert result.applied == []
    assert result.skipped == []


# ---------------------------------------------------------------------------
# field_overlap + policy
# ---------------------------------------------------------------------------


def test_field_overlap_prefer_left_keeps_target_value() -> None:
    """policy=prefer_left + конфликт sex → applied=[], skipped=[], audit=prefer_left."""
    target = TreeSnapshot(persons={"@I1@": _person("@I1@", sex="M")})
    diff = DiffReport(
        persons_modified=[
            PersonModify(
                target_xref="@I1@",
                field_changes={"sex": FieldChange(before="F", after="F")},
            )
        ]
    )
    policy = MergePolicy(on_conflict="prefer_left")

    result = apply_diff_pure(target, diff, policy)

    assert not result.aborted
    assert result.applied == []
    # prefer_left решён без конфликта в skipped — owner's value win'ит молча.
    assert result.skipped == []
    actions = [a.action for a in result.log]
    assert "applied_prefer_left" in actions


def test_field_overlap_prefer_right_overwrites() -> None:
    """policy=prefer_right → applied содержит изменение, skipped пусто."""
    target = TreeSnapshot(persons={"@I1@": _person("@I1@", sex="M")})
    diff = DiffReport(
        persons_modified=[
            PersonModify(
                target_xref="@I1@",
                field_changes={"sex": FieldChange(before="F", after="F")},
            )
        ]
    )
    policy = MergePolicy(on_conflict="prefer_right")

    result = apply_diff_pure(target, diff, policy)

    assert not result.aborted
    assert len(result.applied) == 1
    assert result.applied[0].field == "sex"
    assert result.applied[0].new_value == "F"
    assert result.skipped == []
    actions = [a.action for a in result.log]
    assert "applied_prefer_right" in actions


def test_field_overlap_manual_skips_and_records_conflict() -> None:
    """policy=manual → applied=[], skipped имеет field_overlap."""
    target = TreeSnapshot(persons={"@I1@": _person("@I1@", sex="M")})
    diff = DiffReport(
        persons_modified=[
            PersonModify(
                target_xref="@I1@",
                field_changes={"sex": FieldChange(before="F", after="F")},
            )
        ]
    )
    policy = MergePolicy(on_conflict="manual")

    result = apply_diff_pure(target, diff, policy)

    assert not result.aborted
    assert result.applied == []
    assert len(result.skipped) == 1
    c: Conflict = result.skipped[0]
    assert c.kind == "field_overlap"
    assert c.target_xref == "@I1@"
    assert c.field == "sex"
    assert c.left_value == "M"
    assert c.right_value == "F"


def test_field_modify_no_conflict_when_target_matches_before() -> None:
    """target=M, before=M, after=F → applied (свежее изменение, не конфликт)."""
    target = TreeSnapshot(persons={"@I1@": _person("@I1@", sex="M")})
    diff = DiffReport(
        persons_modified=[
            PersonModify(
                target_xref="@I1@",
                field_changes={"sex": FieldChange(before="M", after="F")},
            )
        ]
    )
    policy = MergePolicy(on_conflict="manual")

    result = apply_diff_pure(target, diff, policy)

    assert not result.aborted
    assert result.skipped == []
    assert len(result.applied) == 1
    assert result.applied[0].new_value == "F"


# ---------------------------------------------------------------------------
# missing_anchor → atomic abort
# ---------------------------------------------------------------------------


def test_missing_anchor_aborts_entire_merge() -> None:
    """Один relation_added на несуществующий xref → applied=[] (даже если
    другой persons_added в том же diff'е был бы валиден). aborted=True."""
    target = TreeSnapshot(persons={"@I1@": _person("@I1@", sex="M")})
    diff = DiffReport(
        persons_added=[PersonAdd(xref="@I9@", fields={"sex": "F"})],
        relations_added=[
            RelationAdd(
                relation_type="parent_child",
                person_a_xref="@I_GHOST@",  # не существует
                person_b_xref="@I1@",
            ),
        ],
    )
    policy = MergePolicy(on_conflict="prefer_right")

    result = apply_diff_pure(target, diff, policy)

    assert result.aborted is True
    assert result.applied == []
    assert any(c.kind == "missing_anchor" for c in result.skipped)
    assert result.abort_reason is not None
    assert "missing_anchor" in result.abort_reason
    actions = [a.action for a in result.log]
    assert "aborted_missing_anchor" in actions


def test_missing_anchor_modify_targets_unknown_person() -> None:
    """persons_modified для xref'а, которого нет в target → missing_anchor."""
    target = _empty_target()
    diff = DiffReport(
        persons_modified=[
            PersonModify(
                target_xref="@I_NOT_THERE@",
                field_changes={"sex": FieldChange(before=None, after="M")},
            )
        ]
    )
    policy = MergePolicy(on_conflict="manual")

    result = apply_diff_pure(target, diff, policy)

    assert result.aborted is True
    assert result.applied == []
    assert len(result.skipped) == 1
    assert result.skipped[0].kind == "missing_anchor"
    assert result.skipped[0].target_xref == "@I_NOT_THERE@"


# ---------------------------------------------------------------------------
# relation_added — happy + idempotent
# ---------------------------------------------------------------------------


def test_relation_added_with_persons_added_in_same_diff() -> None:
    """Связь между двумя новыми персонами (обе в persons_added) — анchored
    OK, обе персоны и связь применены."""
    target = _empty_target()
    diff = DiffReport(
        persons_added=[
            PersonAdd(xref="@I1@", fields={"sex": "M"}),
            PersonAdd(xref="@I2@", fields={"sex": "F"}),
        ],
        relations_added=[
            RelationAdd(
                relation_type="spouse",
                person_a_xref="@I1@",
                person_b_xref="@I2@",
            )
        ],
    )
    policy = MergePolicy(on_conflict="manual")

    result = apply_diff_pure(target, diff, policy)

    assert not result.aborted
    kinds = sorted(c.kind for c in result.applied)
    assert kinds == ["person_added", "person_added", "relation_added"]


def test_relation_added_already_present_is_noop() -> None:
    """spouse(A,B) уже в target.relations → applied не содержит relation_added."""
    target = TreeSnapshot(
        persons={
            "@I1@": _person("@I1@", sex="M"),
            "@I2@": _person("@I2@", sex="F"),
        },
        relations=[
            RelationRecord(relation_type="spouse", person_a="@I1@", person_b="@I2@"),
        ],
    )
    diff = DiffReport(
        relations_added=[
            # Тот же spouse, но порядок xref'ов перевёрнут — должен матчиться
            # симметрично.
            RelationAdd(
                relation_type="spouse",
                person_a_xref="@I2@",
                person_b_xref="@I1@",
            )
        ]
    )
    policy = MergePolicy(on_conflict="manual")

    result = apply_diff_pure(target, diff, policy)

    assert not result.aborted
    assert all(c.kind != "relation_added" for c in result.applied)


# ---------------------------------------------------------------------------
# persons_removed
# ---------------------------------------------------------------------------


def test_person_removed_existing() -> None:
    """soft-delete существующей персоны → applied=[person_removed]."""
    target = TreeSnapshot(persons={"@I1@": _person("@I1@", sex="M")})
    diff = DiffReport(persons_removed=[PersonRemove(target_xref="@I1@")])
    policy = MergePolicy(on_conflict="manual")

    result = apply_diff_pure(target, diff, policy)

    assert not result.aborted
    assert len(result.applied) == 1
    assert result.applied[0].kind == "person_removed"
    assert result.applied[0].xref == "@I1@"


def test_person_removed_missing_aborts() -> None:
    """remove на несуществующий xref → missing_anchor + aborted."""
    target = _empty_target()
    diff = DiffReport(persons_removed=[PersonRemove(target_xref="@I_GONE@")])
    policy = MergePolicy(on_conflict="manual")

    result = apply_diff_pure(target, diff, policy)

    assert result.aborted is True
    assert result.applied == []
    assert any(c.kind == "missing_anchor" for c in result.skipped)


# ---------------------------------------------------------------------------
# Audit log integrity
# ---------------------------------------------------------------------------


def test_audit_log_carries_actor_user_id() -> None:
    """policy.actor_user_id протянут во все Audit-записи."""
    target = _empty_target()
    diff = DiffReport(persons_added=[PersonAdd(xref="@I1@", fields={"sex": "M"})])
    actor = "user-uuid-abc"
    policy = MergePolicy(on_conflict="manual", actor_user_id=actor)

    result = apply_diff_pure(target, diff, policy)

    assert all(isinstance(a, Audit) for a in result.log)
    assert all(a.actor_user_id == actor for a in result.log)


def test_relations_removed_no_op_when_absent() -> None:
    """relation_removed на отсутствующую связь — no-op, не aborted."""
    target = TreeSnapshot(
        persons={
            "@I1@": _person("@I1@", sex="M"),
            "@I2@": _person("@I2@", sex="F"),
        }
    )
    diff = DiffReport(
        relations_removed=[
            RelationRemove(
                relation_type="spouse",
                person_a_xref="@I1@",
                person_b_xref="@I2@",
            )
        ]
    )
    policy = MergePolicy(on_conflict="manual")

    result = apply_diff_pure(target, diff, policy)

    assert not result.aborted
    # Никаких applied — связи не было; никаких skipped — это не конфликт.
    assert result.applied == []
    assert result.skipped == []


def test_change_objects_serialise_via_pydantic() -> None:
    """Sanity: Change/Conflict/Audit — Pydantic-модели, model_dump работает."""
    ch = Change(kind="person_added", xref="@I1@", new_value={"sex": "M"})
    assert ch.model_dump()["kind"] == "person_added"
    c = Conflict(kind="field_overlap", target_xref="@I1@", field="sex")
    assert c.model_dump()["kind"] == "field_overlap"
