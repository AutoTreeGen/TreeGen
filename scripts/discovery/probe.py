"""Phase 5.11a discovery probe — sections A/B/D/E/F per single GED file.

Запускается через ``run_all.py`` в subprocess'е с hard-timeout и наблюдением
peak RSS извне. Сам probe:

* секция A — файловые метрики (размер, encoding, line count, entity counts,
  custom-tag count, max line length);
* секция B — wall time парсера + валидатора, in-process peak RSS как fallback;
* секция D — xref integrity (dangling, orphans, ancestor cycles via Tarjan);
* секция E — encoding-specific (BOM, sample non-ASCII, round-trip check для
  UTF-16);
* секция F — память и проекция импорта (peak in-process RSS, est DB row count,
  est line→row throughput).

Вывод — JSON в stdout. Exit code:
* 0 — секции A/D/E/F успешно собраны (B может содержать parse exception).
* 2 — сам probe упал до парса (например, не открылся файл).

Probe НЕ принимает решений «good/bad» — только числа. Интерпретация
делается в discovery report.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import threading
import time
import traceback

# Принудительный UTF-8 stdout: на Windows default — cp1252, который ломается
# на не-Latin1 символах (emoji в _CUSTOM-тегах, кириллица в именах из RR/GM).
# Без этого `json.dump(ensure_ascii=False)` крашится UnicodeEncodeError'ом и
# probe возвращает пустой результат.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )
from collections import defaultdict, deque
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import psutil  # transitive dep, см. uv.lock

# ---------------------------------------------------------------------------
# Внутренние утилиты
# ---------------------------------------------------------------------------


def _proc_rss() -> int:
    """RSS текущего процесса в байтах."""
    return psutil.Process().memory_info().rss


class _RssSampler:
    """In-process сэмплер peak RSS на отдельном thread'е.

    Не замена внешнему наблюдателю: если процесс OOM-нет, мы не успеем
    записать пик. Но для случаев когда parse завершается — полезен как
    backup и как cheap measurement.
    """

    def __init__(self, interval: float = 0.05) -> None:
        self._interval = interval
        self._stop = threading.Event()
        self._peak = _proc_rss()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._peak = max(self._peak, _proc_rss())
            self._stop.wait(self._interval)

    def stop(self) -> int:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        # Финальная проба — на случай если thread не успел.
        self._peak = max(self._peak, _proc_rss())
        return self._peak


def _safe_count_lines(path: Path) -> tuple[int, int]:
    """Считает строки и максимальную длину строки в файле.

    Декодинг — латин1 (1 байт = 1 char), чтобы не падать на смешанных или
    UTF-16 кодировках. Длина строки — в декодированных char'ах. Для UTF-16
    результат завышен (latin1 видит каждый байт как символ), но цели секции —
    обнаружить аномально длинные строки (потенциальные lexer-проблемы) — это
    допустимо, и мы маркируем encoding отдельно в секции A.
    """
    line_count = 0
    max_len = 0
    with path.open("rb") as f:
        for raw_line in f:
            line_count += 1
            max_len = max(max_len, len(raw_line))
    return line_count, max_len


# Канонические теги GEDCOM 5.5.1 верхнего уровня (упрощённая выборка для
# подсчёта custom-tag count). Любой тег с префиксом ``_`` или не из этого
# множества помечается как custom.
_KNOWN_TAGS: frozenset[str] = frozenset(
    {
        "HEAD",
        "TRLR",
        "INDI",
        "FAM",
        "SOUR",
        "REPO",
        "OBJE",
        "NOTE",
        "SUBM",
        "SUBN",
        # event tags inside INDI/FAM
        "BIRT",
        "DEAT",
        "BURI",
        "CREM",
        "BAPM",
        "BARM",
        "BASM",
        "BLES",
        "CHR",
        "CHRA",
        "CONF",
        "FCOM",
        "ORDN",
        "NATU",
        "EMIG",
        "IMMI",
        "CENS",
        "PROB",
        "WILL",
        "GRAD",
        "RETI",
        "EVEN",
        "MARR",
        "DIV",
        "DIVF",
        "ENGA",
        "MARB",
        "MARC",
        "MARL",
        "MARS",
        "ANUL",
        # attribute tags
        "ADDR",
        "AGE",
        "AGNC",
        "CAUS",
        "CITN",
        "DSCR",
        "EDUC",
        "EMAIL",
        "FAX",
        "FACT",
        "IDNO",
        "NATI",
        "NCHI",
        "NMR",
        "OCCU",
        "PHON",
        "PROP",
        "RELI",
        "RESI",
        "SSN",
        "TITL",
        "WWW",
        # structure tags
        "NAME",
        "GIVN",
        "SURN",
        "NPFX",
        "NSFX",
        "NICK",
        "SPFX",
        "FONE",
        "ROMN",
        "TYPE",
        "SEX",
        "FAMS",
        "FAMC",
        "HUSB",
        "WIFE",
        "CHIL",
        "DATE",
        "PLAC",
        "MAP",
        "LATI",
        "LONG",
        "ADR1",
        "ADR2",
        "ADR3",
        "CITY",
        "STAE",
        "POST",
        "CTRY",
        "TEXT",
        "PAGE",
        "QUAY",
        "ROLE",
        "RELA",
        "REFN",
        "RIN",
        "RFN",
        "AFN",
        "FILE",
        "FORM",
        "MEDI",
        "ABBR",
        "AUTH",
        "PUBL",
        "DATA",
        "CALN",
        "CHAR",
        "VERS",
        "CORP",
        "DEST",
        "GEDC",
        "LANG",
        "COPR",
        "CONT",
        "CONC",
        "CHAN",
        "TIME",
        "BLOB",
        "ANCI",
        "DESI",
        "_UID",  # treat _UID as known custom (Ancestry/MyHeritage standard)
    }
)


# Регулярка GEDCOM строки: ``LEVEL [@XREF@] TAG [VALUE]``.
_GED_LINE_RE = re.compile(
    r"^\s*(?P<level>\d+)\s+(?:@(?P<xref>[^@]+)@\s+)?(?P<tag>[A-Za-z_][A-Za-z0-9_]*)"
)


def _quick_tag_scan(path: Path, encoding: str) -> dict[str, Any]:
    """Один проход: считает теги, INDI/FAM/SOUR/NOTE/OBJE record-count'ы,
    custom-tag set, OBJE inline и xref-only OBJE — без построения AST.

    Используется для секции A (метрики) когда полный parse неудобен или
    мы хотим cross-check с парсером.
    """
    indi = fam = sour = note = obje_top = 0
    custom_tags: dict[str, int] = defaultdict(int)
    total_records = 0
    total_indi_obje = 0  # OBJE как child INDI/FAM (мультимедиа-link)
    with path.open("rb") as fh:
        # Декодим лениво по строкам.
        for raw in fh:
            try:
                line = raw.decode(encoding, errors="replace")
            except LookupError:
                line = raw.decode("latin1", errors="replace")
            m = _GED_LINE_RE.match(line)
            if m is None:
                continue
            level = int(m["level"])
            tag = m["tag"].upper()
            if level == 0:
                total_records += 1
                if tag == "INDI":
                    indi += 1
                elif tag == "FAM":
                    fam += 1
                elif tag == "SOUR":
                    sour += 1
                elif tag == "NOTE":
                    note += 1
                elif tag == "OBJE":
                    obje_top += 1
            else:
                if tag == "OBJE":
                    total_indi_obje += 1
                if tag.startswith("_") or tag not in _KNOWN_TAGS:
                    custom_tags[tag] += 1
    # Ограничиваем top custom для отчёта.
    top_custom = sorted(custom_tags.items(), key=lambda x: -x[1])[:30]
    return {
        "total_l0_records": total_records,
        "individuals": indi,
        "families": fam,
        "sources": sour,
        "notes_top": note,
        "objects_top": obje_top,
        "objects_inline": total_indi_obje,
        "custom_tag_total": sum(custom_tags.values()),
        "custom_tag_distinct": len(custom_tags),
        "custom_tag_top30": top_custom,
    }


# ---------------------------------------------------------------------------
# Section E: encoding deep-dive
# ---------------------------------------------------------------------------


def _section_e(path: Path, encoding_info: Any, decoded_text: str | None) -> dict[str, Any]:
    """Encoding-specific findings: BOM, non-ASCII sample, round-trip."""
    with path.open("rb") as fh:
        head = fh.read(8)
    bom = head[:2].hex().upper(), head[:3].hex().upper()
    bom_class = None
    if head.startswith(b"\xef\xbb\xbf"):
        bom_class = "UTF-8 BOM"
    elif head.startswith(b"\xff\xfe"):
        bom_class = "UTF-16-LE BOM"
    elif head.startswith(b"\xfe\xff"):
        bom_class = "UTF-16-BE BOM"
    else:
        bom_class = "no BOM"

    non_ascii_sample: list[str] = []
    if decoded_text is not None:
        # Берём первые 50 NAME-строк с не-ASCII содержимым.
        seen = 0
        for line in decoded_text.splitlines():
            if seen >= 50:
                break
            stripped = line.strip()
            # Грубо: ищем `1 NAME ...`. Не строим AST.
            if " NAME " in stripped[:8]:
                value = stripped.split(" NAME ", 1)[1] if " NAME " in stripped else ""
                if any(ord(ch) > 127 for ch in value):
                    non_ascii_sample.append(value)
                    seen += 1

    # Round-trip: re-encode → decode and compare. Возвращаем число
    # символов, которые не сохранились в выбранной encoding (для UTF-16
    # это всегда 0, для UTF-8 — тоже; для CP1251/ANSEL — может быть >0).
    rt_lossy_chars = None
    if decoded_text is not None and encoding_info is not None:
        try:
            enc_name = (
                encoding_info.name
                if not str(encoding_info.name).upper().startswith("ANSEL")
                else "latin1"
            )
            roundtrip = decoded_text.encode(enc_name, errors="replace").decode(
                enc_name, errors="replace"
            )
            mismatches = sum(1 for a, b in zip(decoded_text, roundtrip, strict=False) if a != b)
            if len(decoded_text) != len(roundtrip):
                mismatches += abs(len(decoded_text) - len(roundtrip))
            rt_lossy_chars = mismatches
        except (LookupError, UnicodeError) as exc:
            rt_lossy_chars = f"ERROR: {exc!r}"

    return {
        "bom_2bytes_hex": bom[0],
        "bom_3bytes_hex": bom[1],
        "bom_class": bom_class,
        "encoding_name": getattr(encoding_info, "name", None) if encoding_info else None,
        "encoding_method": getattr(encoding_info, "method", None) if encoding_info else None,
        "encoding_confidence": getattr(encoding_info, "confidence", None)
        if encoding_info
        else None,
        "encoding_head_char_raw": getattr(encoding_info, "head_char_raw", None)
        if encoding_info
        else None,
        "non_ascii_name_sample_size": len(non_ascii_sample),
        "non_ascii_name_sample": non_ascii_sample[:20],
        "roundtrip_lossy_chars": rt_lossy_chars,
    }


# ---------------------------------------------------------------------------
# Section D: cross-reference integrity + Tarjan SCC
# ---------------------------------------------------------------------------


def _tarjan_scc(graph: dict[str, list[str]]) -> list[list[str]]:
    """Iterative Tarjan SCC. Возвращает списки SCC длиной > 1 (потенциальные
    циклы) — одиночки нам не интересны.
    """
    index_counter = [0]
    stack: list[str] = []
    lowlinks: dict[str, int] = {}
    index: dict[str, int] = {}
    on_stack: dict[str, bool] = {}
    result: list[list[str]] = []

    def strongconnect(node: str) -> None:
        # Iterative emulation: используем явный стек состояний.
        work_stack: deque[tuple[str, Iterable[str]]] = deque()
        work_stack.append((node, iter(graph.get(node, []))))
        index[node] = index_counter[0]
        lowlinks[node] = index_counter[0]
        index_counter[0] += 1
        stack.append(node)
        on_stack[node] = True
        while work_stack:
            v, it = work_stack[-1]
            try:
                w = next(it)
            except StopIteration:
                work_stack.pop()
                if work_stack:
                    parent = work_stack[-1][0]
                    lowlinks[parent] = min(lowlinks[parent], lowlinks[v])
                if lowlinks[v] == index[v]:
                    component = []
                    while True:
                        u = stack.pop()
                        on_stack[u] = False
                        component.append(u)
                        if u == v:
                            break
                    if len(component) > 1:
                        result.append(component)
                continue
            if w not in index:
                index[w] = index_counter[0]
                lowlinks[w] = index_counter[0]
                index_counter[0] += 1
                stack.append(w)
                on_stack[w] = True
                work_stack.append((w, iter(graph.get(w, []))))
            elif on_stack.get(w):
                lowlinks[v] = min(lowlinks[v], index[w])

    for v in graph:
        if v not in index:
            strongconnect(v)
    return result


def _section_d(doc: Any) -> dict[str, Any]:
    """Cross-reference integrity: dangling, orphans, ancestor cycles."""
    broken = doc.verify_references()
    persons = set(doc.persons.keys())
    families = set(doc.families.keys())

    # Persons referenced by FAM (HUSB/WIFE/CHIL).
    referenced_persons: set[str] = set()
    parent_to_child: dict[str, list[str]] = defaultdict(list)
    for fam in doc.families.values():
        if fam.husband_xref:
            referenced_persons.add(fam.husband_xref)
            for c in fam.children_xrefs:
                parent_to_child[fam.husband_xref].append(c)
        if fam.wife_xref:
            referenced_persons.add(fam.wife_xref)
            for c in fam.children_xrefs:
                parent_to_child[fam.wife_xref].append(c)
        for c in fam.children_xrefs:
            referenced_persons.add(c)

    orphan_persons = persons - referenced_persons

    # Ancestor cycles: Tarjan SCC на parent→child графе.
    # Включаем только узлы-персоны, которые реально присутствуют в графе.
    scc_graph: dict[str, list[str]] = {p: parent_to_child.get(p, []) for p in persons}
    cycles = _tarjan_scc(scc_graph)

    return {
        "person_count": len(persons),
        "family_count": len(families),
        "broken_refs_total": len(broken),
        "broken_refs_sample": [
            {
                "owner_xref": br.owner_xref,
                "owner_kind": br.owner_kind,
                "field": br.field,
                "target_xref": br.target_xref,
                "expected_kind": br.expected_kind,
            }
            for br in broken[:20]
        ],
        "orphan_person_count": len(orphan_persons),
        "ancestor_cycle_scc_count": len(cycles),
        "ancestor_cycle_total_nodes": sum(len(c) for c in cycles),
        "ancestor_cycle_sample_first3": [c[:5] for c in cycles[:3]],
    }


# ---------------------------------------------------------------------------
# Главный probe
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("ged", type=Path, help="Путь к GED-файлу")
    parser.add_argument(
        "--skip-validator",
        action="store_true",
        help="Пропустить запуск validator (Phase 5.8) — для огромных файлов",
    )
    parser.add_argument(
        "--skip-compat",
        action="store_true",
        help="Пропустить запуск compat-simulator (Phase 5.6)",
    )
    parser.add_argument(
        "--no-deep-parse",
        action="store_true",
        help="Только секции A и E (по сырым байтам). Никакого AST.",
    )
    args = parser.parse_args(argv)

    path: Path = args.ged
    if not path.exists():
        json.dump({"error": "file_not_found", "path": str(path)}, sys.stdout)
        return 2

    sampler = _RssSampler()
    sampler.start()
    started_at = time.time()
    rss_at_start = _proc_rss()

    result: dict[str, Any] = {
        "path": str(path),
        "started_at_unix": started_at,
        "skipped_validator": args.skip_validator,
        "skipped_compat": args.skip_compat,
        "no_deep_parse": args.no_deep_parse,
    }

    # ----- Section A: file-level metrics (без полного парсера) -----
    file_size = path.stat().st_size
    line_count, max_line_len = _safe_count_lines(path)
    with path.open("rb") as fh:
        head_4k = fh.read(4096)
    from gedcom_parser import detect_encoding

    enc = detect_encoding(head_4k)
    py_codec = enc.name if enc.name != "ANSEL" else "latin1"
    quick = _quick_tag_scan(path, py_codec)

    result["section_A"] = {
        "file_size_bytes": file_size,
        "file_size_mb": round(file_size / (1024 * 1024), 2),
        "line_count": line_count,
        "max_line_length_bytes": max_line_len,
        "encoding": {
            "name": enc.name,
            "method": enc.method,
            "confidence": enc.confidence,
            "head_char_raw": enc.head_char_raw,
        },
        **quick,
    }

    # ----- Section E: encoding deep-dive (BOM + RT) -----
    decoded_text = None
    if not args.no_deep_parse:
        from gedcom_parser import decode_gedcom_file

        try:
            t0 = time.time()
            decoded_text, _ = decode_gedcom_file(path)
            result["section_B_decode_secs"] = round(time.time() - t0, 3)
        except Exception as exc:
            result["section_B_decode_error"] = repr(exc)
            result["section_B_decode_traceback"] = traceback.format_exc().splitlines()[-5:]

    result["section_E"] = _section_e(path, enc, decoded_text)

    # ----- Section B: parse perf -----
    if args.no_deep_parse or decoded_text is None:
        result["section_B_parse_skipped"] = True
    else:
        from gedcom_parser import parse_text
        from gedcom_parser.document import GedcomDocument

        try:
            t0 = time.time()
            records = parse_text(decoded_text)
            t1 = time.time()
            doc = GedcomDocument.from_records(records, encoding=enc)
            t2 = time.time()
            result["section_B"] = {
                "parse_records_secs": round(t1 - t0, 3),
                "build_document_secs": round(t2 - t1, 3),
                "parse_total_secs": round(t2 - t0, 3),
                "rss_after_parse_bytes": _proc_rss(),
                "person_count": len(doc.persons),
                "family_count": len(doc.families),
                "source_count": len(doc.sources),
                "note_count": len(doc.notes),
                "object_count": len(doc.objects),
                "submitter_count": len(doc.submitters),
                "repository_count": len(doc.repositories),
                "unknown_tag_blocks": len(doc.unknown_tags),
            }
        except Exception as exc:
            result["section_B"] = {
                "parse_failed": True,
                "exception": repr(exc),
                "traceback_tail": traceback.format_exc().splitlines()[-8:],
                "rss_at_failure_bytes": _proc_rss(),
                "elapsed_secs_at_failure": round(time.time() - t0, 3),
            }
            doc = None

        # ----- Section D: xref integrity -----
        if doc is not None:
            try:
                result["section_D"] = _section_d(doc)
            except Exception as exc:
                result["section_D"] = {
                    "failed": True,
                    "exception": repr(exc),
                    "traceback_tail": traceback.format_exc().splitlines()[-5:],
                }

        # ----- Validator (Phase 5.8) — finding counts only -----
        if doc is not None and not args.skip_validator:
            from gedcom_parser.validator import validate_document

            try:
                tv = time.time()
                findings = validate_document(doc)
                tv_end = time.time()
                rule_counts: dict[str, int] = defaultdict(int)
                for f in findings:
                    rule_counts[f.rule_id] += 1
                result["section_B_validator"] = {
                    "validator_secs": round(tv_end - tv, 3),
                    "findings_total": len(findings),
                    "findings_by_rule": dict(rule_counts),
                }
            except Exception as exc:
                result["section_B_validator"] = {
                    "validator_failed": True,
                    "exception": repr(exc),
                    "traceback_tail": traceback.format_exc().splitlines()[-5:],
                }

        # ----- Compat sim (Phase 5.6) -----
        if doc is not None and not args.skip_compat:
            from gedcom_parser.compatibility import simulate

            sim_results: dict[str, Any] = {}
            for target in ("ancestry", "myheritage", "familysearch", "gramps"):
                try:
                    tc = time.time()
                    rep = simulate(doc, target=target)
                    sim_results[target] = {
                        "secs": round(time.time() - tc, 3),
                        "tag_drops": len(rep.tag_drops),
                        "encoding_warnings": len(rep.encoding_warnings),
                        "structure_changes": len(rep.structure_changes),
                        "estimated_loss_pct": rep.estimated_loss_pct,
                    }
                except Exception as exc:
                    sim_results[target] = {
                        "failed": True,
                        "exception": repr(exc),
                    }
            result["section_B_compat"] = sim_results

    # ----- Section F: memory & import projection -----
    peak_rss = sampler.stop()
    result["section_F"] = {
        "rss_at_start_bytes": rss_at_start,
        "rss_at_end_bytes": _proc_rss(),
        "peak_rss_in_process_bytes": peak_rss,
        "peak_rss_in_process_mb": round(peak_rss / (1024 * 1024), 2),
        "elapsed_total_secs": round(time.time() - started_at, 3),
    }
    # Грубая проекция: если каждый INDI = 1 row, FAM = 1 row, SOUR = 1 row,
    # NOTE = 1 row, OBJE = 1 row — это нижняя граница.
    a = result["section_A"]
    naive_rows = a["individuals"] + a["families"] + a["sources"] + a["notes_top"] + a["objects_top"]
    result["section_F"]["est_db_rows_naive_lower_bound"] = naive_rows

    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
