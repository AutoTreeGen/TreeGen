# dna-analysis

DNA parsing + analysis primitives for AutoTreeGen (Phase 6).

This package is **pure functions only**: parsers, validators, algorithms.
It does **not** persist DNA data, does **not** speak HTTP, and does **not**
manage encryption keys. Persistence + HTTP go to a future
`services/dna-service/` (Phase 6.1).

## Privacy notice

DNA is special-category personal data under GDPR Art. 9. This package
follows ADR-0012 (`docs/adr/0012-dna-privacy-architecture.md`):

- **Never** logs raw `rsid`, `position`, `chromosome`, or `genotype` values
  for individual SNPs. Logs report aggregates (`parsed N SNPs from
  file [hash-prefix]`) only.
- Test fixtures are **100% synthetic** — generated in `tests/_generators.py`
  with `random.seed(42)` for determinism. No real `rsid`s.
- Real DNA files **must** be encrypted at rest (Phase 6.1, separate ADR).
  This package does not provide encryption helpers yet.
- `.gitignore` blocks real DNA paths repo-wide; do not commit raw files
  even from your own kit.

Read `docs/adr/0012-dna-privacy-architecture.md` before contributing.

## Status (Phase 6.0)

Scaffold + privacy guards. Parsers shipping with this phase:

- 23andMe v5 raw (TSV) — full parser.
- AncestryDNA v2 raw (TSV) — full parser.
- MyHeritage raw (CSV) — stub, raises `UnsupportedFormatError`. Phase 6.1.
- FamilyTreeDNA Family Finder (CSV) — stub. Phase 6.1.

Analysis (shared cM, AutoCluster, triangulation) — Phase 6.2+.

## Quickstart

```python
from dna_analysis.parsers import TwentyThreeAndMeParser

with open("kit.txt", encoding="utf-8") as fh:
    content = fh.read()

parser = TwentyThreeAndMeParser()
if parser.detect(content):
    test = parser.parse(content)
    print(f"{test.provider} {test.version}: {len(test.snps)} SNPs "
          f"on {test.reference_build}")
```

## Layout

```text
src/dna_analysis/
  models.py         # Pydantic: DnaTest, Snp, Genotype, Chromosome, Provider
  errors.py         # DnaParseError, UnsupportedFormatError
  parsers/
    base.py         # BaseDnaParser ABC
    twentythreeand_me.py
    ancestry.py
    myheritage.py        # stub (Phase 6.1)
    family_tree_dna.py   # stub (Phase 6.1)
  analysis/
    shared_cm.py    # stub (Phase 6.2)
```
