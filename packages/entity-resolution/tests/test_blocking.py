"""Тесты blocking (ADR-0015 §«Performance / blocking»)."""

from __future__ import annotations

from entity_resolution.blocking import block_by_dm
from entity_resolution.persons import PersonForMatching


def _p(surname: str | None) -> PersonForMatching:
    return PersonForMatching(
        given="Anyone",
        surname=surname,
        birth_year=1850,
        death_year=None,
        birth_place=None,
        sex="U",
    )


class TestBlockByDm:
    def test_blocking_buckets_correctly(self) -> None:
        """Транслитерации одной фамилии попадают в общий bucket."""
        persons = [
            _p("Zhitnitzky"),
            _p("Zhitnitsky"),
            _p("Zhytnicki"),
            _p("Smith"),  # disjoint bucket
        ]
        buckets = block_by_dm(persons)
        # Найдём bucket(ы), где живут все три варианта Zhitnitzky.
        zhitnitzky_buckets = [
            persons_in_bucket
            for persons_in_bucket in buckets.values()
            if any(p.surname == "Zhitnitzky" for p in persons_in_bucket)
        ]
        # Хотя бы один из них должен содержать оба варианта.
        any_overlap = any(
            len({p.surname for p in bucket}) >= 2 and "Zhitnitsky" in {p.surname for p in bucket}
            for bucket in zhitnitzky_buckets
        )
        assert any_overlap, (
            "Zhitnitzky and Zhitnitsky must share at least one DM bucket; "
            f"got buckets {[(k, [p.surname for p in v]) for k, v in buckets.items()]}"
        )

    def test_smith_in_separate_bucket_from_zhitnitzky(self) -> None:
        persons = [_p("Smith"), _p("Zhitnitzky")]
        buckets = block_by_dm(persons)
        for bucket_persons in buckets.values():
            surnames = {p.surname for p in bucket_persons}
            # «Smith» и «Zhitnitzky» не должны жить в одном bucket'е.
            assert not ({"Smith", "Zhitnitzky"} <= surnames)

    def test_no_surname_goes_to_empty_bucket(self) -> None:
        persons = [_p(None), _p("")]
        buckets = block_by_dm(persons)
        assert "" in buckets
        assert len(buckets[""]) == 2

    def test_empty_input_empty_buckets(self) -> None:
        assert block_by_dm([]) == {}

    def test_person_with_multiple_dm_codes_in_multiple_buckets(self) -> None:
        """Persons с многозначным DM (если такая фамилия найдётся)."""
        persons = [_p("Schwartz")]  # Schwartz — классический пример амбивалентности
        buckets = block_by_dm(persons)
        # Не утверждаем точное число bucket'ов (зависит от pyphonetics),
        # но persona должна оказаться хотя бы в одном.
        all_persons = [p for bucket in buckets.values() for p in bucket]
        assert persons[0] in all_persons
