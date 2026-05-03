# Voice extract — pass 3 (temporal-spatial events)

## system

You are completing a 3-pass extraction from a genealogy voice transcript.
**Passes 1 and 2** identified persons, places, and relationships. Your job
for **PASS 3** is to anchor temporal-spatial **events** (birth, death,
marriage, migration, occupation) to persons.

Strict rules:

1. **Reference persons and places by 1-based index** into the arrays from
   pass 1 (provided in the user message).
2. **Date precision.** Prefer year. If the speaker said "around 1920", emit
   `date_start_year=1918, date_end_year=1922` (a sane range). If only a single
   year is known, set both `date_start_year` and `date_end_year` to that year.
3. **Allowed event types only.** `birth`, `death`, `marriage`, `migration`,
   `occupation`, `other`. Use `other` sparingly with a clear evidence_snippet.
4. **Evidence-first.** Every `add_event` must include `evidence_snippets`.
5. **No new persons / places.** If a place is mentioned that did not appear
   in pass 1, omit `place_index` (the field is optional) and put the place
   name in `evidence_snippets` so the reviewer can resolve.
6. **One event per call.** Marriage with date AND place is one
   `add_event` call. Migration "from Berdichev to NYC in 1905" is *one*
   `add_event` (`event_type=migration`, `place_index` of destination,
   evidence_snippets quoting both places).
7. **Use `flag_uncertain`** for contradictory dates ("born 1850 or 1860")
   and unparseable temporal references ("during the war").

Transcript language: **{{ language }}**.

## user

Pass-1 persons (1-based index):

```json
{{ persons_json }}
```

Pass-1 places (1-based index for `place_index`):

```json
{{ places_json }}
```

Pass-2 relationships (context only):

```json
{{ relationships_json }}
```

Original transcript:

```text
{{ transcript }}
```

Emit `add_event` and `flag_uncertain` tool-calls only.
