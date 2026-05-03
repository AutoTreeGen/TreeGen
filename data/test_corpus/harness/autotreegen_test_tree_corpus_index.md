# AutoTreeGen Test Tree Corpus + Evaluation Suite — Corpus Index

**Version:** 0.1
**Generated UTC:** 2026-05-02T16:17:04.437497+00:00
**Trees found:** 20 / 20

## Purpose

Synthetic but realistic deterministic genealogy test trees for validating AutoTreeGen AI features: evidence scoring, GEDCOM repair, DNA interpretation, archive routing, fabrication detection, place/name resolution, NPE handling, endogamy, OCR and sealed-set logic.

## Coverage Summary

- Total evaluation assertions: **101**
- Total embedded errors: **119**
- Total DNA matches: **97**
- Total archive snippets: **113**

## Trees

| # | Tree ID | Category | Complexity | Assertions | Main validation targets |
|---:|---|---|---|---:|---|
| 1 | `tree_01_pale_levitin_npe_resolution` | npe_via_dna | high | 3 | 15.x, 16.x, 24.x, 25.x, 5.10 |
| 2 | `tree_02_mennonite_batensky_fictional_bridge` | fictional_bridge_and_endogamy | high | 3 | 15.10, 16.x, 19.x, 5.10, 25.x |
| 3 | `tree_03_friedman_raskes_identity_resolution` | maiden_name_identity_resolution | medium | 3 | 15.10, 16.x, 19.x, 24.x, 25.x |
| 4 | `tree_04_voikhansky_kamenetsky_viral_tree_contamination` | fabrication_detection_and_same_name_separation | high | 5 | 5.10_fantasy_filter, 15.10_name_disambiguation, 15.11_sealed_set_assertions, 16.x_dna_cluster_interpretation, 19.x_place_resolution, ... |
| 5 | `tree_05_brest_litovsk_holocaust_gap_reconstruction` | holocaust_gap_reconstruction | high | 5 | 5.10_fantasy_filter, 15.10_name_disambiguation, 16.x_dna_cluster_interpretation, 19.x_place_resolution, 22.x_archive_routing, ... |
| 6 | `tree_06_rabbi_kamenetsky_hypothesis_not_confirmed` | rabbinical_hypothesis_control | high | 6 | 5.10_fantasy_filter, 15.10_name_disambiguation, 15.11_sealed_set_assertions, 16.x_dna_cluster_interpretation, 19.x_place_resolution, ... |
| 7 | `tree_07_patronymic_vs_surname_disambiguation` | name_parsing_patronymic_surname | medium | 5 | 15.10_name_disambiguation, GEDCOM_doctor, duplicate_detection, metric_book_parser, revision_list_parser, ... |
| 8 | `tree_08_maiden_vs_married_name_resolution` | maiden_married_name_resolution | medium | 5 | 15.10_name_disambiguation, GEDCOM_doctor, duplicate_detection, 16.x_dna_cluster_interpretation, 24.x_evidence_summary, ... |
| 9 | `tree_09_cross_platform_dna_match_resolver` | cross_platform_dna_identity_resolution | high | 5 | 16.x_dna_cluster_interpretation, cross_platform_dna_resolver, 15.10_name_disambiguation, 24.x_evidence_summary, 25.x_hypothesis_sandbox, ... |
| 10 | `tree_10_historical_place_jurisdiction_resolution` | historical_place_resolution | high | 6 | 19.x_place_resolution, 22.x_archive_routing, 16.x_dna_cluster_interpretation, 15.10_name_disambiguation, GEDCOM_doctor, ... |
| 11 | `tree_11_unknown_father_npe_dna_contradiction` | npe_unknown_parentage | high | 6 | NPE_mode, DNA_vs_tree_contradiction, 15.11_sealed_set_assertions, 16.x_dna_cluster_interpretation, 24.x_evidence_summary, ... |
| 12 | `tree_12_ashkenazi_endogamy_multi_path_relationship` | endogamy_multi_path_relationship | high | 5 | 16.x_dna_cluster_interpretation, endogamy_aware_relationship_engine, multi_evidence_combination_algebra, 24.x_evidence_summary, 25.x_hypothesis_sandbox, ... |
| 13 | `tree_13_mennonite_colony_founder_loop_ambiguity` | mennonite_endogamy_founder_loop | high | 5 | 16.x_dna_cluster_interpretation, endogamy_aware_relationship_engine, 5.10_fantasy_filter, 19.x_place_resolution, 22.x_archive_routing, ... |
| 14 | `tree_14_sephardic_mizrahi_crossover_false_ashkenazi_merge` | non_ashkenazi_jewish_crossover | high | 5 | 16.x_dna_cluster_interpretation, non_ashkenazi_jewish_community_detection, 15.10_name_disambiguation, 19.x_place_resolution, 22.x_archive_routing, ... |
| 15 | `tree_15_gedcom_safe_merge_conflicting_sources` | gedcom_doctor_safe_merge | high | 6 | GEDCOM_doctor, safe_merge, source_preservation, media_loss_detection, DNA_vs_tree_contradiction, ... |
| 16 | `tree_16_metric_book_ocr_extraction_errors` | ocr_metric_book_extraction | high | 5 | OCR_metric_book_parser, metric_book_parsing_template, 15.10_name_disambiguation, 19.x_place_resolution, 22.x_archive_routing, ... |
| 17 | `tree_17_revision_list_household_interpretation` | revision_list_household_parsing | high | 5 | revision_list_parser, household_continuity_engine, negative_evidence_handling, 15.10_name_disambiguation, 19.x_place_resolution, ... |
| 18 | `tree_18_immigration_name_change_myth_and_wrong_origin` | migration_name_change_resolution | high | 5 | migration_pathway_patterns, 15.10_name_disambiguation, 19.x_place_resolution, 22.x_archive_routing, 16.x_dna_cluster_interpretation, ... |
| 19 | `tree_19_famous_relative_royal_rabbinical_overclaim_filter` | famous_relative_fabrication_detection | high | 5 | 5.10_fantasy_filter, pale_fabrication_patterns, 15.10_name_disambiguation, 16.x_dna_cluster_interpretation, 24.x_evidence_summary, ... |
| 20 | `tree_20_full_pipeline_sealed_set_contradiction_resolution` | full_pipeline_evaluation | high | 8 | full_pipeline, GPS_plus, evidence_rubric, multi_evidence_combination_algebra, NPE_mode, ... |

## Categories

- **cross_platform_dna_identity_resolution**: 1
- **endogamy_multi_path_relationship**: 1
- **fabrication_detection_and_same_name_separation**: 1
- **famous_relative_fabrication_detection**: 1
- **fictional_bridge_and_endogamy**: 1
- **full_pipeline_evaluation**: 1
- **gedcom_doctor_safe_merge**: 1
- **historical_place_resolution**: 1
- **holocaust_gap_reconstruction**: 1
- **maiden_married_name_resolution**: 1
- **maiden_name_identity_resolution**: 1
- **mennonite_endogamy_founder_loop**: 1
- **migration_name_change_resolution**: 1
- **name_parsing_patronymic_surname**: 1
- **non_ashkenazi_jewish_crossover**: 1
- **npe_unknown_parentage**: 1
- **npe_via_dna**: 1
- **ocr_metric_book_extraction**: 1
- **rabbinical_hypothesis_control**: 1
- **revision_list_household_parsing**: 1

## Population Contexts

- **Ashkenazi**: 6
- **Ashkenazi + Bukharian + Mountain Jewish**: 1
- **Ashkenazi + Slavic**: 1
- **Ashkenazi Pale of Settlement**: 2
- **Ashkenazi diaspora**: 1
- **Ashkenazi endogamy**: 1
- **Ashkenazi endogamy + diaspora**: 1
- **Ashkenazi endogamy + public-tree contamination**: 1
- **Mennonite + Slavic adjacency**: 1
- **Mixed Ashkenazi + Mennonite + Slavic**: 1
- **Mixed Ashkenazi + Slavic**: 2
- **Mixed Ashkenazi + Slavic + Mennonite-adjacent**: 1
- **Mixed Ukrainian Orthodox + Mennonite**: 1
