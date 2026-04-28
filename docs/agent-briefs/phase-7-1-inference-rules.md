# Agent brief — Phase 7.1: more inference rules + dedup integration

> **Кому:** Агент 2 — после Phase 3.4 (entity resolution).
> **Worktree:** `TreeGen-phase34` или новый.
> **Перед стартом:** `git checkout main && git pull`

---

## Контекст

После Phase 3.4 (твоя работа): `entity-resolution` package с
Daitch-Mokotoff own implementation + place matching + person scoring.
После Phase 7.0 (Агент 6): `inference-engine` framework с
Hypothesis/Evidence/InferenceRule.

Phase 7.1 — **дополнить inference-engine реальными rules + интегрировать
с твоим entity-resolution**.

Это где dedup suggestions становятся **hypotheses with full evidence chain**.

**Параллельно работают** Агенты 1, 3, 4, 5, 6.

**Твоя территория:**

- `packages/inference-engine/src/inference_engine/rules/` — добавить новые rule файлы
- `packages/inference-engine/tests/test_rules_*.py` — тесты
- ADR-0019 (если нужно) — explainability of composite scores

**Что НЕ трогай:**

- `packages/entity-resolution/` ты только что закрыл — стабильное
- Pure inference-engine framework (Агент 6 в Phase 7.0)
- Другие пакеты

---

## Задачи

### Task 1 — feat(inference-engine): SurnameMatchRule (Daitch-Mokotoff)

Использует **твой** entity-resolution.phonetic.daitch_mokotoff.

```python
class SurnameMatchRule:
    rule_id = "surname_dm_match"

    def apply(self, subject_a, subject_b, context):
        from entity_resolution.phonetic import daitch_mokotoff
        a_codes = daitch_mokotoff(subject_a["surname"])
        b_codes = daitch_mokotoff(subject_b["surname"])
        if set(a_codes) & set(b_codes):  # any overlap
            return [Evidence(
                rule_id=self.rule_id,
                direction=EvidenceDirection.SUPPORTS,
                weight=0.5,
                observation=f"Daitch-Mokotoff bucket overlap "
                            f"({a_codes} ∩ {b_codes})",
            )]
        return []
```

Тесты: Zhitnitzky/Zhytnicki/Жytницкий должны overlap → support.

### Task 2 — feat(inference-engine): PlaceMatchRule

Использует `entity_resolution.places.place_match_score`.

```python
class BirthPlaceMatchRule:
    rule_id = "birth_place_match"

    def apply(self, subject_a, subject_b, context):
        from entity_resolution.places import place_match_score
        a_place = subject_a.get("birth_place")
        b_place = subject_b.get("birth_place")
        if not a_place or not b_place:
            return []
        score = place_match_score(a_place, b_place)
        if score >= 0.8:
            return [Evidence(SUPPORTS, weight=0.4 * score, ...)]
        if score < 0.3:
            return [Evidence(CONTRADICTS, weight=0.3, ...)]
        return []
```

### Task 3 — feat(inference-engine): SexConsistencyRule

```python
class SexConsistencyRule:
    rule_id = "sex_consistency"

    def apply(self, subject_a, subject_b, context):
        a, b = subject_a.get("sex"), subject_b.get("sex")
        if a and b and a != b and "U" not in (a, b):
            # Only valid for SAME_PERSON hypothesis — different sex
            # → strong contradiction for "same person"
            if context.get("hypothesis_type") == "same_person":
                return [Evidence(CONTRADICTS, weight=0.95,
                    observation=f"Sex mismatch: {a} vs {b}")]
        return []
```

### Task 4 — feat(inference-engine): integration test "Zhitnitzky duplicates"

Demo на твоём family case:

```python
def test_zhitnitzky_duplicates_get_high_score():
    """Vlad 1945 Dnepro vs Volodya 1945 Dnepro → composite ≥ 0.85."""
    register_rule(SurnameMatchRule())
    register_rule(BirthYearMatchRule())  # already from Phase 7.0
    register_rule(BirthPlaceMatchRule())
    register_rule(SexConsistencyRule())

    a = {"surname": "Zhitnitzky", "birth_year": 1945,
         "birth_place": "Dnepropetrovsk", "sex": "M"}
    b = {"surname": "Жytницкий", "birth_year": 1945,
         "birth_place": "Днепр", "sex": "M"}

    h = compose_hypothesis(HypothesisType.SAME_PERSON, a, b, context={"hypothesis_type": "same_person"})
    assert h.composite_score >= 0.85
    rule_ids = {e.rule_id for e in h.evidences}
    assert "surname_dm_match" in rule_ids
    assert "birth_year_match" in rule_ids
    assert "birth_place_match" in rule_ids
```

### Task 5 (опционально) — docs(adr): ADR-0019 explainability

Объяснить как `evidences[]` → human-readable explanation для UI.

---

## Что НЕ делать

- ❌ ORM persistence (Phase 7.2)
- ❌ HTTP API (Phase 7.3)
- ❌ ML rules (Phase 10)
- ❌ Auto-link entities (CLAUDE.md §5)
- ❌ `git commit --no-verify`

---

## Сигналы успеха

1. ✅ 4 новых rules работают
2. ✅ Zhitnitzky integration test green
3. ✅ Test coverage ≥80% на rules
4. ✅ CI green

Удачи. Это где AutoTreeGen начинает делать **explainable evidence-based
matching** — ровно то что Ancestry's ThruLines не дают.
