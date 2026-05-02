# scripts/discovery — Phase 5.11a probes

Reusable diagnostic probes for huge / weird GEDCOM files. **Read-only**:
никаких мутаций ни на диске, ни в БД, ни в самом GED-файле. Вывод — JSON
(стдаут) + per-file копии в `_discovery_runs/<timestamp>/` (gitignored).

## Files

| File | What it does |
|---|---|
| `probe.py` | Sections **A / B / D / E / F** на одном GED. Метрики, parse perf, xref integrity, encoding deep-dive, memory. |
| `probe_dupes.py` | Section **C** — collision-группы по `(surname, given, birth_year, birth_place)`. **NO MUTATIONS**, только числа. |
| `run_all.py` | Orchestrator: subprocess + hard-timeout + external peak-RSS sampler. |

## Usage

Файлы и corpus передаются через CLI или `GEDCOM_TEST_CORPUS` env var.
**Никаких hardcoded путей.**

```powershell
# Один файл, full probe:
$env:GEDCOM_TEST_CORPUS = "<path-to-your-GED-corpus>"
uv run python scripts/discovery/probe.py "$env:GEDCOM_TEST_CORPUS/GM317_utf-16.ged"

# Орчестратор на конкретные файлы + dup-pass на GM317:
uv run python scripts/discovery/run_all.py `
    "$env:GEDCOM_TEST_CORPUS/GM317_utf-16.ged" `
    "$env:GEDCOM_TEST_CORPUS/RR.ged" `
    --dupes-on GM317_utf-16.ged

# Все *.ged в корпусе:
uv run python scripts/discovery/run_all.py --corpus $env:GEDCOM_TEST_CORPUS

# Skip validator/compat (для самых тяжёлых):
uv run python scripts/discovery/run_all.py "$ged" --probe-args --skip-validator --skip-compat
```

## Constraints (Phase 5.11a invariants)

* **No production code touched.** Только `scripts/discovery/`, `docs/`.
* **No GED bytes leak.** raw JSON outputs хранятся в `_discovery_runs/`,
  который исключён из git. В sample-полях probe выкидывает только NAMES
  и xref'ы — никакого privacy-чувствительного контента.
* **No "fix dedup" recommendations.** Section C только считает.
  GM317 имеет 30K+ дублей INTENTIONALLY (Geoffrey помечает «related»).
