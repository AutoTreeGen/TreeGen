# ADR-0047 — Public tree share: privacy model

**Status:** accepted
**Date:** 2026-04-29
**Phase:** 11.2

## Context

After Phase 11.0/11.1 (per-recipient sharing — invitations + memberships, ADR-0036),
owners asked for one extra channel: a single read-only URL they can paste into
a family chat or email thread without inviting each cousin individually.

This is a different threat model from per-recipient sharing:

* The link is a bearer token — anyone holding it can open the page.
* There is no recipient identity, no per-user revocation, no audit of who saw what.
* The link is likely to be forwarded, archived in chat history, or screenshotted.

Naive "expose the same UI as authenticated owner" leaks PII of living relatives
(names, exact birth dates, sometimes addresses), and exposes DNA data — which we
classify as GDPR Art. 9 special category (see ADR-0012). That is unacceptable
even with explicit owner consent: living relatives have not consented to public
exposure of their data, and DNA data of close kin reveals information about the
owner's blood relatives who never opted in.

## Decision

A public-share endpoint exists, but the response is **stripped down on the
server**, not just hidden in the UI:

1. **DNA data is fully cut.** No matches, kits, consents, segments, ethnicity —
   nothing DNA-derived appears in the response. Even if a future UI bug renders
   raw fields, they are absent from the wire format.
2. **Living relatives are anonymized server-side.** A person is treated as
   "likely alive" if there is no `DEAT` event AND (no birth date OR birth date
   within `_MAX_PLAUSIBLE_AGE_YEARS=110` of now). For these persons, the
   response substitutes `display_name="Living relative"`, drops birth/death
   years, and sets `is_anonymized=true`. Sex is kept (low identifiability).
3. **Date precision is reduced to year for everyone.** Even deceased persons
   have only `birth_year`/`death_year`, never full dates. Day/month precision
   is unnecessary for genealogical context and reduces fingerprint surface.
4. **Sources, notes, places, and provenance are not returned at all.** Notes
   often contain PII (addresses, marriage details, hospital info); places can
   identify residences. Phase 11.2 ships without these; if owners want to share
   sources, that is a separate decision and a separate phase.
5. **No per-recipient identification.** No accept-flow, no email logging, no
   `viewed_at`. Owner sees: token created, token revoked. That's it.

## Token mechanics

* Generated server-side with `secrets.token_urlsafe(15)` — ~120 bits of entropy,
  brute-force is infeasible. Stored plaintext (rotation = create new + revoke
  old; hash storage deferred to Phase 11.3 if threat model demands).
* One active share per tree (idempotent POST). Token rotation is explicit —
  owner POST'ing again while one is active gets the existing one back; to
  rotate, DELETE then POST.
* Optional `expires_at` (capped at 10 years). NULL means "until manual revoke".
* Revocation is soft-delete (`revoked_at` timestamp) so we keep an audit trail.
  Lookups always check `revoked_at IS NULL AND (expires_at IS NULL OR expires_at > now())`.
* The public endpoint returns 404 for unknown / revoked / expired tokens —
  the same code intentionally. We do not leak "this used to exist" vs "never
  existed".

## Rate limiting

The public endpoint has no auth and no per-user identity, so abuse mitigation
is per-IP rate-limiting. Phase 11.2 ships an in-memory sliding-window limiter
(60 requests / minute / IP) co-located with the parser-service process. This is
deliberately scoped to the single-pod staging deployment; multi-replica
correctness requires a Redis-backed limiter (slowapi or equivalent), which
will be added when production scale-out happens. The 60/min budget is generous
enough for navigation but quickly chokes a scraper.

## Why not just hide DNA in the UI

Two reasons:

* **Defense in depth.** UI bugs happen — a future change to a shared component
  could accidentally surface a hidden field. Cutting at the wire boundary is
  much harder to regress.
* **Accidental leakage to non-UI consumers.** The public URL returns JSON; a
  curl-driven scraper or a third-party indexing tool gets the same data the UI
  does. If sensitive fields aren't in the response at all, they can't be
  scraped.

## Consequences

* The public view is genuinely minimal: name + sex + year-range for the
  deceased; "Living relative" placeholders for the living. This is a feature,
  not a limitation — the owner asked for "show what I'd put on a public family
  blog", not "expose my full database".
* DNA-aware features (chromosome painting, match clusters) are unavailable on
  the public view by design. If an owner wants to share DNA findings publicly,
  that is a separate feature requiring per-person consent collection — not in
  scope for 11.2.
* Token rotation requires owner action; we don't auto-rotate. If an owner
  suspects a leak, they DELETE and POST a new one.
* If the owner is removed from the tree (Phase 11.0 transfer + revocation),
  the share they created remains active under the new owner — `created_by_user_id`
  is audit, not a permission. New owner can revoke it via the same endpoint.

## Alternatives considered

* **Per-recipient public links with email tracking.** Rejected: defeats the
  point of "paste in family chat", and email tracking is itself a privacy issue.
* **Sign-up gate ("anyone with a Clerk account can view").** Rejected: forces
  cousins to create accounts, contradicts the sharing UX request.
* **Hash-stored tokens with constant-time comparison.** Deferred to Phase 11.3.
  At 120 bits of entropy, the practical attack surface is the link being
  forwarded, not brute-forced. Hashing adds operational complexity (lookup
  cost, no plaintext recovery for re-display) for marginal benefit at this
  threat level.
* **Encrypted client-side storage of share metadata** (so revocation doesn't
  need DB lookup). Rejected: revocation must be server-authoritative.

## Open follow-ups

* Phase 11.3: hash-storage for tokens, optional password gate, per-share view
  counter (privacy-aware: aggregate only, no IP retention).
* Distributed rate-limiter (Redis-backed) when production scale-out lands.
* Decide whether places (anonymized to country/region only) should be exposed.
