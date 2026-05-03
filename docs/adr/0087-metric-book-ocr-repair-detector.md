# ADR-0087: Metric book OCR repair detector

## Status

Accepted for Phase 26.4.

## Context

AutoTreeGen must treat OCR and copied online-tree data as derivative evidence,
not as primary genealogical truth. Historical metric books often contain
multi-column Russian/Hebrew entries, old place jurisdictions, handwritten
month names, gender columns and patronymic/surname forms that OCR can misread.

The target corpus case is:

`tree_16_metric_book_ocr_extraction_errors`

It models common Eastern-European archive errors:

- March (`марта`) misread as May (`мая`);
- Kamenetsky misread as Kaminsky;
- male child imported as female;
- Rabinovich mother misread as Raskin;
- modern place normalization losing Podolia Governorate context;
- duplicate profiles created from OCR variants;
- online trees copying OCR errors as facts.

## Decision

Implement `metric_book_ocr.detect(tree)` as a deterministic tree-level detector
registered through `inference_engine.detectors.registry`.

The detector may use:

- `embedded_errors`
- `input_archive_snippets`
- primary metric-book image snippets
- derivative OCR/public-tree snippets

The detector must not copy `expected_engine_flags` wholesale.

## Output

The detector can emit:

- `ocr_month_march_may_confusion`
- `ocr_kamenetsky_kaminsky_false_variant`
- `metric_book_gender_column_misread`
- `ocr_rabinovich_raskin_false_mother`
- `modern_place_normalization_lost_jurisdiction`
- `ocr_created_duplicate_profile`
- `online_tree_ocr_error_propagation`
- `primary_image_overrides_ocr_derivative`

It also emits merge decisions, quarantined claims, relationship claims, place
corrections and selected evaluation assertion results.

## Consequences

Tree 16 should rise from baseline to complete score while preserving the raw OCR
as derivative evidence rather than accepting it as fact.
