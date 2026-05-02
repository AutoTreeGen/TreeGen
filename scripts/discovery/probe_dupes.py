"""Phase 5.11a — Section C: duplicate analysis на одном GED-файле.

Считает collision-группы по ключу ``(surname_normalized, given_normalized,
birth_year, birth_place_normalized)``. Возвращает:

* total person count;
* dup-группы (size>=2): количество групп, общее число дублей;
* top-10 most-duped collision-keys с counts;
* sample 50 dup-pairs с минимальной информацией для ручной проверки
  (xref, full name, birth date, birth place).

**NO MUTATIONS.** Этот probe только считает. Он НЕ предлагает merge,
НЕ создаёт recommendation, НЕ помечает «дубли как баг». Геноминимум:
для GM317 30K+ дублей — ИНТЕНЦИОНАЛЬНЫ (Geoffrey помечает всех «related»).
Probe лишь количественно описывает картину.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import time
import traceback

# Force UTF-8 stdout (см. probe.py).
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )
from collections import defaultdict
from pathlib import Path
from typing import Any

_WHITESPACE_RE = re.compile(r"\s+")
# Год — первое 4-значное число в дате-строке. Достаточно для группировки;
# полноценный parse дат — отдельная задача.
_YEAR_RE = re.compile(r"\b(\d{4})\b")


def _norm(s: str | None) -> str:
    if s is None:
        return ""
    return _WHITESPACE_RE.sub(" ", s.strip().casefold())


def _extract_year(date_raw: str | None) -> str:
    if date_raw is None:
        return ""
    m = _YEAR_RE.search(date_raw)
    return m.group(1) if m else ""


def _norm_place(place_raw: str | None) -> str:
    """Нормализация: lowercased, stripped, выкидываем trailing запятые/пустые
    уровни. Достаточно грубо для collision detection.
    """
    if place_raw is None:
        return ""
    parts = [p.strip() for p in place_raw.split(",")]
    parts = [p for p in parts if p]
    return _norm(", ".join(parts))


def _person_key(person: Any) -> tuple[str, str, str, str]:
    name = person.names[0] if person.names else None
    given = _norm(getattr(name, "given", None) if name else None)
    surname = _norm(getattr(name, "surname", None) if name else None)
    birth_year = ""
    birth_place = ""
    for ev in person.events:
        if ev.tag == "BIRT":
            birth_year = _extract_year(ev.date_raw)
            birth_place = _norm_place(ev.place_raw)
            break
    return surname, given, birth_year, birth_place


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("ged", type=Path)
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="Сколько top-collision keys показать (default 10)",
    )
    parser.add_argument(
        "--pairs",
        type=int,
        default=50,
        help="Сколько dup-pairs sample показать (default 50)",
    )
    args = parser.parse_args(argv)

    if not args.ged.exists():
        json.dump({"error": "file_not_found", "path": str(args.ged)}, sys.stdout)
        return 2

    started = time.time()
    try:
        from gedcom_parser import parse_document_file
    except ImportError as exc:
        json.dump({"error": "import_failed", "exception": repr(exc)}, sys.stdout)
        return 2

    try:
        t0 = time.time()
        doc = parse_document_file(args.ged)
        parse_secs = round(time.time() - t0, 3)
    except Exception as exc:
        json.dump(
            {
                "parse_failed": True,
                "exception": repr(exc),
                "traceback_tail": traceback.format_exc().splitlines()[-8:],
                "elapsed_secs": round(time.time() - started, 3),
            },
            sys.stdout,
            ensure_ascii=False,
        )
        return 0

    persons = list(doc.persons.values())
    groups: dict[tuple[str, str, str, str], list[Any]] = defaultdict(list)
    for p in persons:
        k = _person_key(p)
        # Полностью пустой ключ — не информативен (например, person без NAME).
        if any(k):
            groups[k].append(p)

    dup_groups = [(k, members) for k, members in groups.items() if len(members) >= 2]
    dup_groups.sort(key=lambda kv: -len(kv[1]))

    # Top collision keys.
    top = []
    for k, members in dup_groups[: args.top]:
        top.append(
            {
                "key": {
                    "surname": k[0],
                    "given": k[1],
                    "birth_year": k[2],
                    "birth_place": k[3],
                },
                "collision_count": len(members),
                "sample_xrefs": [m.xref_id for m in members[:5]],
            }
        )

    # Sample dup-pairs. Берём первые N групп, по 1 паре с группы (чтобы не
    # упереться в 30K дублей одной фамилии).
    sample_pairs = []
    for k, members in dup_groups[: args.pairs]:
        a = members[0]
        b = members[1] if len(members) > 1 else members[0]
        sample_pairs.append(
            {
                "a_xref": a.xref_id,
                "b_xref": b.xref_id,
                "key_surname": k[0],
                "key_given": k[1],
                "key_birth_year": k[2],
                "key_birth_place": k[3],
                "a_full_value": a.names[0].value if a.names else None,
                "b_full_value": b.names[0].value if b.names else None,
                "a_n_events": len(a.events),
                "b_n_events": len(b.events),
                "a_n_families_as_spouse": len(a.families_as_spouse),
                "b_n_families_as_spouse": len(b.families_as_spouse),
            }
        )

    total_dup_persons = sum(len(m) for _, m in dup_groups)
    # «Лишние» персоны = всего в дуп-группах минус по одному уникальному из каждой группы.
    excess_dup_persons = total_dup_persons - len(dup_groups)

    out = {
        "path": str(args.ged),
        "parse_secs": parse_secs,
        "elapsed_secs": round(time.time() - started, 3),
        "person_count": len(persons),
        "dup_group_count": len(dup_groups),
        "persons_in_dup_groups": total_dup_persons,
        "excess_dup_persons": excess_dup_persons,
        "top_collision_keys": top,
        "sample_pairs": sample_pairs,
        "key_definition": "(surname_norm, given_norm, birth_year, birth_place_norm)",
    }
    json.dump(out, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
