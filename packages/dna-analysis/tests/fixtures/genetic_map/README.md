# Synthetic genetic-map fixtures

These files imitate the SHAPEIT / HapMap GRCh37 genetic-map format
(`position COMBINED_rate(cM/Mb) Genetic_Map(cM)`) but contain
**fully synthetic** recombination data, generated deterministically
with `random.seed=42`.

Purpose: lets `tests/test_genetic_map.py` exercise the loader and
interpolation logic without committing the ~50 MB real HapMap dataset
(which is `.gitignore`d and downloaded via
`scripts/download_genetic_map.py`).

The synthetic curve uses:

- chr22 only (smallest autosome, real range ~16 Mb–51 Mb).
- ~100 points, monotonically increasing `position` and `cumulative cM`.
- Average rate ~1 cM/Mb (typical autosomal recombination).
- Exact ground-truth values asserted in the tests — do not regenerate
  without updating the assertions.

See `_make_synthetic_genetic_map.py` next to this README for the
generator (run `uv run python -m
packages.dna_analysis.tests.fixtures.genetic_map._make_synthetic_genetic_map`
to recreate). Privacy-by-design: this fixture contains no human DNA
data of any kind.
