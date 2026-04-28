# Archive integrations — research note (Phase 9.0-pre, 2026-04-28)

> **Status:** Research, no code.
> **Author:** Phase 9.0-pre research agent.
> **Audience:** project owner + future Phase 9 engineers; this note may be
> shared externally with potential partners.
> **Scope:** public-API-only landscape survey for Phase 9 archive integrations.
> Aligned with `CLAUDE.md` §5 (no scraping platforms without public API) and
> ROADMAP §13.

## TL;DR

Of 12 surveyed sources, only **5 expose a usable public API** today (April 2026):
FamilySearch (already integrated, Phase 5.1), WikiTree, MyHeritage Family Graph,
Geni, Wikimedia Commons, and BillionGraves. The remaining sources — JewishGen,
JRI-Poland, GenTeam, YIVO, Szukaj w Archiwach, and the Lithuanian / Belarusian
/ Ukrainian national archives — are **search-UI-only** and must be reached
either through a data partnership (multi-month legal track) or through
non-scraping deep-link «smart search» helpers.

For TreeGen's Eastern European Jewish genealogy focus, the highest-value
sources (JewishGen, JRI-Poland, GenTeam) are exactly the ones without an API.
This forces a two-track Phase 9 plan: ship the API-having sources fast in
Phase 9.1–9.3, and start partnership outreach for the no-API ones in parallel
so Phase 9.4+ has something to build on once paperwork lands.

The recommended order is in [§Recommended Phase 9.x order](#recommended-phase-9x-order).

## Methodology

- **Public API only.** Per `CLAUDE.md` §5, scraping platforms without a public
  API is forbidden. Where a source has no API, the only acceptable engineering
  output is a deep-link helper («open this query on JewishGen», «search this
  surname on Szukaj w Archiwach») that hands the user off to the source's own
  UI. No HTML parsing, no headless browsers.
- **Primary sources.** Vendor developer portals and ToS pages; community
  trackers (Tamura Jones genealogy APIs catalogue) only as cross-checks.
- **Cut-off.** April 2026. API surfaces change. Anything in this note must be
  re-verified before signing a contract or shipping.
- **Geographic bias.** TreeGen's domain is XIX–XX century Eastern European,
  predominantly Jewish, genealogy. Sources are weighted by their coverage of
  that corpus, not by global record count.

## Source-by-source

### FamilySearch

- **API URL:** `https://api.familysearch.org` (production),
  `https://api-integ.familysearch.org` (sandbox).
- **Auth:** OAuth 2.0 Authorization Code + PKCE (RFC 7636). Sandbox app key
  is auto-issued on developer-account creation; production access requires
  the **Compatible Solution Program** review (business + engineering passes).
- **Rate limits:** Not publicly documented. Conservative client-side
  throttling recommended (ADR-0011).
- **Data licensing:** Free for non-profit / research use. Production
  redistribution requires Compatible Solution approval. No bulk export.
- **Cost:** Free tier exists for all approved partners; no published price
  list for premium.
- **Coverage for our domain:** Excellent — FamilySearch holds large
  microfilmed corpora from Eastern European archives that are otherwise
  unreachable, including Polish, Lithuanian, Ukrainian Jewish vital records.
- **Effort:** **Already integrated** — Phase 5.1 (PR #111) ships OAuth +
  pedigree import. Compatible Solution review is the open work for production.

**Status for Phase 9:** Done. Treated as the reference integration, not a
new Phase 9 source.

**Sources:**
[developers.familysearch.org](https://developers.familysearch.org/),
[App Approval Considerations](https://www.familysearch.org/innovate/app-approval-considerations),
[2025 Q2 Newsletter](https://developers.familysearch.org/main/changelog/2025-q2-newsletter).

### MyHeritage — Family Graph API

- **API URL:** `https://www.myheritage.com/FP/API/...` (Family Graph
  endpoints; see vendor docs after key issuance).
- **Auth:** Application key, **issued only after manual screening and
  approval** by MyHeritage. Two restricted scopes (`ExportGEDCOM` and
  email-access) require additional privilege grants beyond the basic scope.
- **Rate limits:** Not publicly documented. The platform handles ~hundreds
  of millions of requests at peak (per MyHeritage Engineering blog).
- **Data licensing:** Bound strictly to MyHeritage privacy policy; no
  exceptions to user-level privacy controls. Redistribution outside the app
  is not permitted by default.
- **Cost:** API itself appears free for approved partners; user-facing
  features (Record Matches, Smart Matches) require MyHeritage subscriptions.
- **Coverage for our domain:** High — MyHeritage has strong
  Eastern European, including Russian/Polish/Romanian Jewish, records
  through their consolidation of regional content.
- **Effort:** Medium engineering once the key is issued (OAuth-style + JSON
  Graph API; reuses the FamilySearch client pattern from ADR-0011). The
  blocker is approval: 4–8 weeks for screening based on community reports
  and the documented «screened and approved» workflow.

**Risks:**

- Public docs are predominantly 2011–2017; ongoing maintenance state is
  ambiguous. Verify with MyHeritage developer support before committing.
- ToS reserves the right to disable any application unilaterally.

**Sources:**
[MyHeritage Family Graph API (Tamura Jones)](https://www.tamurajones.net/MyHeritageFamilyGraphAPI.xhtml),
[MyHeritage Engineering — Graph API at scale](https://medium.com/myheritage-engineering/graph-api-in-a-large-scale-environment-f5e1a228dd8a),
[MyHeritage Terms](https://mobileapi.myheritage.com/terms-and-conditions).

### Geni

- **API URL:** `https://www.geni.com/platform/...` (vendor portal at
  `/platform/developer/help`).
- **Auth:** OAuth 2.0 (server-side and client-side flows documented).
- **Rate limits:** Documented section exists; specific quotas not surfaced
  in the public help pages.
- **Data licensing:** Bound to Geni privacy and visibility rules. Geni was
  acquired by MyHeritage in 2012; the products remain technically separate
  but the strategic direction is set by MyHeritage.
- **Cost:** Free Geni API tier historically; Geni Pro membership unrelated
  to API access.
- **Coverage for our domain:** Moderate — Geni's «World Family Tree» is a
  single shared graph and has decent coverage for some Eastern European
  Jewish lineages, but the tree-merging model creates data-quality risk
  (we'd inherit other users' merge errors).
- **Effort:** Low–medium. OAuth flow is similar to FamilySearch; the client
  package can be cloned from `packages/familysearch-client/`.

**Risks:**

- Strategic direction post-MyHeritage acquisition is uncertain. The Geni API
  could be deprecated or merged into the Family Graph at any point.
- World-tree merge quality varies; provenance must be tracked aggressively
  to avoid laundering bad data into our evidence model (ADR-0007 + §3.3).

**Sources:**
[Geni Developer Help](https://www.geni.com/platform/developer/help),
[An Introduction to the Geni API (Tamura Jones)](https://www.tamurajones.net/TheGeniAPI.xhtml),
[Geni.com on Wikipedia](https://en.wikipedia.org/wiki/Geni.com).

### Ancestry.com

- **API URL:** None public.
- **Auth:** No public auth. B2B partnership only; Ancestry has historically
  declined to publish a developer API despite stating intent in 2010. The
  internal `Family Tree Maker SearchService` exists but is not exposed.
- **Rate limits:** N/A.
- **Data licensing:** Tree access through Ancestry is governed by user
  consent and Ancestry's ToS; redistribution outside their platform is
  prohibited. Scraping triggers account bans (ROADMAP §13.1).
- **Cost:** N/A (no public access). B2B contracts are negotiated case-by-case.
- **Coverage for our domain:** High — Ancestry holds significant
  Eastern European Jewish content, both indexed and image-only.
- **Effort:** **Engineering is N/A until partnership exists.** Realistic
  outreach-to-contract timeline is 12+ weeks for a small operation, and
  partnership refusal is the most likely outcome at TreeGen's current scale.

**Recommendation:** Do **not** plan Ancestry integration for Phase 9.x.
Defer until TreeGen has commercial traction sufficient to be an attractive
B2B counterparty. Ship a deep-link helper («search this person on Ancestry»)
in the meantime to keep the user UX coherent.

**Sources:**
[FamilySearch dev wiki — Ancestry resource](https://www.familysearch.org/developers/docs/api/tree/Ancestry_resource),
[Tamura Jones — Genealogy APIs](https://www.tamurajones.net/GenealogyAPIs.xhtml).

### WikiTree

- **API URL:** `https://api.wikitree.com/api.php`.
- **Auth:** None for public profiles (read-only). Session-cookie auth via
  `api.wikitree.com` for private profiles when the caller is a registered
  WikiTree member with trusted-list permission. An `appId` parameter is
  required to avoid strict default rate limits.
- **Rate limits:** Tightened for unidentified callers; with `appId`
  registered through the WikiTree Apps Project the limits are lifted to
  per-app values.
- **Data licensing:** Profile content is CC-BY-SA on WikiTree. Acceptable
  Use Policy **prohibits commercial activity, advertising, and harvesting
  of personal information without prior written consent**. The Honor Code
  emphasises attribution and citation.
- **Cost:** Free.
- **Coverage for our domain:** Moderate — WikiTree has growing
  Eastern European coverage but is U.S.-research-heavy in practice. Useful
  for cross-validation more than primary discovery.
- **Effort:** Low. Read-only HTTP client, JSON. The auth flow for private
  profiles is the only complication; for Phase 9.1 we only need public
  profiles.

**Risks:**

- **Commercial-use restriction.** If TreeGen monetises (Phase 12 paid
  tiers), the AUP requires written consent before WikiTree data is
  exposed in paid features. **Engineering can start; legal must finish
  before public paid launch.** Budget 2–4 weeks of outreach.

**Sources:**
[WikiTree API Help](https://www.wikitree.com/wiki/Help:API_Documentation),
[wikitree-api on GitHub](https://github.com/wikitree/wikitree-api),
[Honor Code](https://www.wikitree.com/wiki/Special:Honor_Code),
[Acceptable Use Policy](https://www.wikitree.com/about/acceptable-use.html).

### JewishGen (incl. JRI-Poland)

- **API URL:** None published.
- **Auth:** N/A. Free JewishGen account required for full UI search; no
  programmatic access.
- **Rate limits:** N/A (UI only).
- **Data licensing:** Mixed. JewishGen aggregates volunteer-indexed and
  partner-supplied data; some collections are co-owned with partner
  organisations (JRI-Poland is administratively independent though hosted
  on JewishGen). Redistribution requires per-collection clearance.
- **Cost:** Free for individual users; partnerships negotiated case-by-case.
- **Coverage for our domain:** **Strategically the highest-value source for
  TreeGen.** Documented holdings as of April 2026:
  - Poland: 2.7M+ records (plus JRI-Poland's 6.1M+ vital-record indices
    from 550+ towns).
  - Lithuania: 2.8M+ records.
  - Ukraine: 5.4M+ records.
  - Belarus: 1M+ records.
  - Romania & Moldova: 1.6M+ records.
  - Hungary (incl. former Hungarian regions): coverage as part of the
    4.8M+ combined Austria/Czechia/Hungary corpus.
  - Latvia: 258K+ records.
- **Effort:** **No engineering possible without a data partnership.** The
  realistic Phase 9 outcome is either:
  1. **Data-partnership outreach** — email JewishGen leadership to negotiate
    bulk-export or query-API access. Calendar: 8–16 weeks for first
    response + agreement, plus engineering once the format is agreed
    (likely CSV/SQL dump rather than live API).
  2. **Deep-link smart-search helper** — for each TreeGen person, generate
    a pre-filled JewishGen All-Poland / All-Lithuania / etc. search URL
    and let the user click through. No scraping, no caching of results.
    Weekend-hackable.

**Risks:**

- **JRI-Poland and JewishGen are administratively separate.** A JewishGen
  partnership does not automatically cover JRI-Poland data; a parallel
  conversation with JRI-Poland is required.
- Volunteer-indexed records have variable provenance quality. Any imported
  match must carry the originating index reference (CLAUDE.md §3.3).

**Sources:**
[JewishGen databases](https://www.jewishgen.org/databases/),
[JRI-Poland](https://jri-poland.org/),
[JewishGen on Wikipedia](https://en.wikipedia.org/wiki/JewishGen).

### GenTeam.eu

- **API URL:** None published.
- **Auth:** Free registration required for the search UI.
- **Rate limits:** UI only.
- **Data licensing:** Volunteer-indexed; redistribution terms not
  documented publicly. Attribution to GenTeam expected on use.
- **Cost:** Free.
- **Coverage for our domain:** High for **Vienna and former
  Austro-Hungarian** records — 8M+ Vienna records including Vienna Jewish
  Community vital records, conversion records, B'nai B'rith membership
  lists, cemetery records, and obituaries. Excellent complement to
  JewishGen Austria/Czechia.
- **Effort:** **No engineering possible without a data partnership.**
  Outreach to the GenTeam volunteer leadership is the path — likely a
  smaller, more direct conversation than JewishGen, but on a similar
  4–12-week calendar.

**Sources:**
[GenTeam — B&F: Jewish Genealogy and More](https://bloodandfrogs.com/compendium/austria/genteam-web-site),
[Center for Jewish History — Austria research guide](https://libguides.cjh.org/genealogyguides/austria/websites).

### YIVO Institute for Jewish Research

- **API URL:** None. Catalogue at
  [archives.cjh.org](https://archives.cjh.org) (ArchivesSpace),
  registration required (7-day rolling sessions). YIVO digital assets are
  also indexed via [libguides.cjh.org/yivodigitalassets](https://libguides.cjh.org/yivodigitalassets).
- **Auth:** Free CJH account.
- **Data licensing:** YIVO holds copyright on most digitised items;
  permissions for derivative use are case-by-case. ~23M records across
  2,500 collections in 12 languages, with strong Yiddish and pre-war
  Eastern European holdings (the «People of 1,000 Towns» photographic
  catalogue alone is 17K images).
- **Cost:** Free for individual research; institutional partnerships
  unspecified publicly.
- **Coverage for our domain:** Very high for Yiddish-language and
  pre-Holocaust shtetl materials, but **archival**, not vital-record
  indexed — most useful for context (photos, letters, community records),
  not pedigree expansion.
- **Effort:** **No API.** ArchivesSpace **does** expose a
  [machine-readable API](https://archivesspace.github.io/archivesspace/api/)
  that some CJH-hosted instances open up, but it requires explicit
  per-instance enablement; verify with `archives@yivo.cjh.org` before
  planning. Calendar: 4–8 weeks of outreach.

**Sources:**
[YIVO Online Resources](https://www.yivo.org/online-resources),
[Guide to the YIVO Archives](https://yivoarchives.yivo.org/),
[YIVO Digital Assets — CJH LibGuide](https://libguides.cjh.org/yivodigitalassets).

### BillionGraves

- **API URL:** `https://api.billiongraves.com/`.
- **Auth:** API key (per developer reports; specific issuance process not
  fully documented on the public landing page).
- **Rate limits:** Tier-dependent.
- **Data licensing:** GPS-tagged headstone images and transcriptions;
  redistribution governed by BillionGraves ToS. Already partnered with
  FamilySearch / MyHeritage / Findmypast — record overlap is significant.
- **Cost:** Tiered (free + paid plans for higher quotas).
- **Coverage for our domain:** Moderate — globally 12M+ headstones, with
  uneven Eastern European coverage. Useful where it does cover, especially
  for U.S. immigrant gravestones that often carry birth-shtetl annotations.
- **Effort:** Low (JSON HTTP). Reuse the Phase 5 client pattern.

**Caveat:** Because FamilySearch already ingests BillionGraves data, the
marginal value of a direct BillionGraves integration for TreeGen users who
are also FamilySearch-connected is reduced. Worth doing for users who
aren't on FamilySearch and for the GPS-coordinates feature, which FS does
not surface in pedigree.

**Sources:**
[BillionGraves API landing page](https://api.billiongraves.com/),
[BillionGraves Support](https://support.billiongraves.com/).

### Wikimedia Commons

- **API URL:** `https://commons.wikimedia.org/w/api.php` (MediaWiki Action
  API).
- **Auth:** Anonymous OK for read; OAuth 2.0 for higher quotas / writes.
- **Rate limits:** Generous for read; per-IP throttling on anonymous
  callers.
- **Data licensing:** Files are CC-licensed (BY or BY-SA most commonly), or
  public domain. **Attribution and license preservation are mandatory.**
  The `iiprop=extmetadata` API returns `Credit`, `LicenseShortName`, and
  `AttributionRequired` fields per file.
- **Cost:** Free.
- **Coverage for our domain:** High for **place imagery** (synagogue
  photos, town views, archival map scans, gravestone photographs uploaded
  by volunteers). Not a vital-record source.
- **Effort:** Trivial. The MediaWiki API is well-documented and stable.

**Sources:**
[Commons:API](https://commons.wikimedia.org/wiki/Commons:API),
[Commons:Licensing](https://commons.wikimedia.org/wiki/Commons:Licensing),
[image-attribution helper (gbv on GitHub)](https://github.com/gbv/image-attribution).

### Szukaj w Archiwach (Polish State Archives)

- **API URL:** None published as of April 2026 on the new
  `szukajwarchiwach.gov.pl` domain. The previous platform exposed limited
  RSS / OAI-PMH endpoints; current status of those is unverified.
- **Auth:** N/A for the UI.
- **Rate limits:** UI only.
- **Data licensing:** Polish state archives material is largely public-domain
  by age; the platform itself permits viewing and download with
  attribution. Bulk programmatic access is not advertised.
- **Cost:** Free.
- **Coverage for our domain:** **Very high** — 55M+ scans of Polish state
  archive holdings, including civil registry records that overlap with
  JRI-Poland's indices but provide the actual page images.
- **Effort:** **No public API.** The supportable engineering path is
  again the deep-link smart-search helper. A data partnership conversation
  with the Polish National Digital Archives (NAC) is realistic and would
  likely succeed (state archives generally favour open access), but
  calendar is unpredictable — 8–16+ weeks. OAI-PMH revival is worth
  asking for explicitly.

**Sources:**
[Szukaj w Archiwach (English)](https://www.szukajwarchiwach.gov.pl/en/archiwa-w-polsce),
[NAC — Search the Archives](https://www.nac.gov.pl/en/digital-archive/szukajwarchiwach-pl-search-the-archives/).

### Lithuanian state archives — EAIS / epaveldas.lt

- **API URL:** None published. EAIS reading room at
  [eais.archyvai.lt](https://eais.archyvai.lt) is browser-only.
  `epaveldas.lt` (the Martynas Mažvydas National Library aggregator)
  exposes some OAI-PMH metadata for some collections — verify per
  collection.
- **Auth:** None for public reading rooms.
- **Data licensing:** Public archival holdings; attribution to Lithuanian
  Archives required.
- **Cost:** Free.
- **Coverage for our domain:** High for Lithuanian Jewish vital records
  (LitvakSIG also indexes from the same source). The Lithuanian State
  Historical Archives holds the LDS-microfilmed parish-register corpus
  through the early 20th century.
- **Effort:** Deep-link helper now; OAI-PMH or partnership conversation
  later. Lithuanian archives are responsive to research-partnership
  inquiries by reputation; budget 6–10 weeks.

**Sources:**
[LitvakSIG — Lithuanian State Historical Archives](https://www.litvaksig.org/information-and-tools/archives-and-repositories/lithuanian-state-historical-archives),
[FamilySearch — Lithuania Archives and Libraries](https://www.familysearch.org/en/wiki/Lithuania_Archives_and_Libraries),
[Electronic surname archive](https://lyapavardes.archyvai.lt/en/about-website/53).

### Belarus — National Historical Archives (NIAB Minsk) and NARB

- **API URL:** None. Inventories partially online at
  [archives.gov.by](https://archives.gov.by/en/) and
  [niab.by](http://niab.by/newsite/en); records access is largely by
  written correspondence.
- **Data licensing:** Public archival holdings; attribution required.
- **Cost:** Free for inventories; per-request fees for genealogical
  research letters.
- **Coverage for our domain:** Very high for Belarusian Jewish vital
  records (NIAB Minsk is the primary repository; NARB covers post-1917
  state material). Access **disrupted in practice** by the geopolitical
  situation since 2020.
- **Effort:** **No API; partnership unrealistic in the current political
  climate.** Recommended: deep-link helper only; revisit when conditions
  permit.

**Sources:**
[Archives of Belarus — genealogy](https://archives.gov.by/en/welcome-to-the-archives-of-belarus-website/genealogy-family-history),
[NIAB Minsk official site](http://niab.by/newsite/en),
[LitvakSIG — NHAB Minsk](https://www.litvaksig.org/information-and-tools/archives-and-repositories/national-historical-archives-of-belarus-minsk).

### Ukraine — State Archival Service (incl. DAZO and 700 institutions)

- **API URL:** None public. Portal at
  [archives.gov.ua](https://archives.gov.ua/en/); per-oblast archive sites
  exist but are inconsistent.
- **Data licensing:** Public archival holdings; attribution required.
  Many records are mirrored on FamilySearch via historical microfilm
  partnerships (effectively the most reliable programmatic path is via
  the FamilySearch API for Ukrainian content).
- **Cost:** Free for inventories; correspondence research has fees.
- **Coverage for our domain:** Very high (5.4M+ records via JewishGen
  Ukraine alone). Wartime conditions have damaged some physical archives
  and disrupted access since 2022; emergency-digitisation efforts are
  ongoing but uneven.
- **Effort:** **Most accessible programmatically through FamilySearch's
  Ukrainian holdings** rather than a direct integration. Direct partnership
  with the State Archival Service is currently unrealistic to plan around.

**Sources:**
[State Archival Service of Ukraine](https://archives.gov.ua/en/),
[FamilySearch — Ukraine Online Genealogy Records](https://www.familysearch.org/en/wiki/Ukraine_Online_Genealogy_Records),
[Gesher Galicia — Ukrainian State Archives](https://www.geshergalicia.org/ukrainian-archives/).

## Comparison table

| Source | Public API? | Auth | Rate limits | License / redistribution | Cost | EE-Jewish coverage | Eng effort | Legal/partnership effort |
|---|---|---|---|---|---|---|---|---|
| FamilySearch | ✅ Yes | OAuth 2.0 + PKCE | Undocumented | Free for non-profit; production needs Compatible Solution | Free tier | Very high (microfilmed EE corpora) | Done (Phase 5.1) | Compatible Solution review (4–8w) |
| MyHeritage Family Graph | ✅ Yes (gated) | App key (manual approval) | Undocumented | Bound to MH privacy; export-GEDCOM scope is privileged | Free tier for partners | High | Medium | App-key approval 4–8w |
| Geni | ✅ Yes | OAuth 2.0 | Documented (values unclear) | Bound to Geni privacy | Free | Moderate (single shared tree) | Low–medium | Low |
| Ancestry | ❌ No public API | B2B only | — | Strict ToS, no redistribution | Contract | High | N/A | 12+w, likely declines |
| WikiTree | ✅ Yes | None (public) / cookie (private) | `appId`-gated quotas | CC-BY-SA + AUP **prohibits commercial use without consent** | Free | Moderate | Low | Commercial consent 2–4w (only if monetising) |
| JewishGen / JRI-Poland | ❌ No API | UI only | — | Volunteer-indexed; per-collection terms | Free | **Highest** for our niche | N/A | Data partnership 8–16w |
| GenTeam.eu | ❌ No API | UI (registration) | — | Volunteer-indexed; attribution | Free | High (Vienna / former AT-HU Jewish) | N/A | Partnership 4–12w |
| YIVO Institute | ❌ No API (ArchivesSpace API may be openable) | CJH account | — | YIVO copyright; case-by-case | Free | High (archival, Yiddish) | Low if ArchivesSpace API enabled | Outreach 4–8w |
| BillionGraves | ✅ Yes | API key | Tier-dependent | ToS; partial overlap with FamilySearch | Free + paid tiers | Moderate (global, uneven EE) | Low | None |
| Wikimedia Commons | ✅ Yes | Anonymous + OAuth | Generous | CC, attribution mandatory | Free | High for place imagery | Trivial | None |
| Szukaj w Archiwach (PL) | ❌ No API (OAI-PMH possibly revivable) | UI | — | Public-domain-by-age; attribution | Free | Very high (55M+ scans) | N/A | Partnership / OAI-PMH 8–16w |
| Lithuania (EAIS / epaveldas) | ⚠️ Partial OAI-PMH on epaveldas | UI | — | Public archival; attribution | Free | High | Per-collection | Partnership 6–10w |
| Belarus (NIAB / NARB) | ❌ No API | Correspondence | — | Public archival; attribution | Per-request fees | Very high | N/A | **Blocked** by political climate |
| Ukraine (State Archival Service) | ❌ No API; mirrored via FamilySearch | Correspondence | — | Public archival; attribution | Per-request fees | Very high | Via FamilySearch | **Blocked** by wartime conditions |

## Recommended Phase 9.x order

Three categories, ordered by readiness, not raw value.

### Tier A — engineering can start today (Phase 9.1–9.3)

These have a public API, no partnership prerequisite, and clear licensing.

1. **Phase 9.1 — Wikimedia Commons place-image adapter.**
   *Rationale:* trivial integration, immediate UX win (synagogue / town
   imagery on event and place pages), proves the multi-source pattern from
   ADR-0011 generalises beyond FamilySearch. **Effort: ~3 days.**
   Establishes the attribution/provenance pipeline used by all later
   adapters.

2. **Phase 9.2 — WikiTree adapter (read-only public profiles).**
   *Rationale:* second source = first time the cross-source dedup pipeline
   is exercised end-to-end; CC-BY-SA license is well-understood. **Effort:
   ~1 week** (clone the FamilySearch client structure; no auth complexity
   for public profiles). **Caveat:** ship behind a feature flag and gate
   public availability behind WikiTree commercial-consent paperwork if
   TreeGen monetises (Phase 12). Otherwise non-commercial use is fine.

3. **Phase 9.3 — BillionGraves cemetery adapter.**
   *Rationale:* GPS-tagged death events round out the FamilySearch
   pedigree; complements existing data rather than duplicating. **Effort:
   ~1 week.** Skippable if FamilySearch-derived BillionGraves data is
   judged sufficient — re-evaluate after 9.1 ships.

### Tier B — engineering blocked on partnership (Phase 9.4–9.5)

Start outreach **in parallel with 9.1** so paperwork is moving while
engineering cycles spend on Tier A.

1. **Phase 9.4 — MyHeritage Family Graph adapter.**
   *Pre-engineering legal:* **request app key, 4–8 weeks.** Email
   `developer@myheritage.com` (verify current address); attach use-case
   summary and TreeGen brand description. Engineering once approved:
   ~1.5 weeks (familiar OAuth-style pattern).

2. **Phase 9.5 — Geni adapter (revisit decision).**
   *Pre-engineering legal:* nominally none, but **explicit confirmation
   from MyHeritage / Geni that the Geni API is supported through the
   end of 2026** is a precondition before we sink engineering time. ~2-week
   conversation. If confirmed, **engineering ~1 week.** If unsupported,
   skip permanently and rely on the Phase 9.4 MyHeritage adapter for
   overlapping data.

### Tier C — partnership-only sources, multi-month timelines (Phase 9.6+)

Highest strategic value but slowest to land. Begin outreach **now**, no
engineering committed until partnership terms exist.

1. **Phase 9.6 — JewishGen + JRI-Poland data partnership.**
   *Pre-engineering legal:* **8–16+ weeks** of outreach + agreement
   negotiation. Two separate conversations (JewishGen and JRI-Poland are
   administratively independent). Engineering once a bulk-export or
   query-API is agreed: ~2–3 weeks per data feed. *In the meantime,*
   ship a deep-link smart-search helper (Phase 9.6a, weekend-hackable).
   Highest strategic priority because this is exactly the corpus
   TreeGen's domain narrows to.

2. **Phase 9.7 — GenTeam.eu data partnership.**
   *Pre-engineering legal:* **4–12 weeks** of outreach to volunteer
   leadership. Smaller and likely faster than JewishGen. Engineering
   estimate dependent on the data-format outcome (CSV dump most likely).

3. **Phase 9.8 — YIVO ArchivesSpace API enablement.**
   *Pre-engineering legal:* **4–8 weeks** of outreach to
   `archives@yivo.cjh.org` to confirm whether the ArchivesSpace REST API
   can be enabled for the YIVO instance on the CJH ArchivesSpace
   deployment. If yes, engineering ~1 week (open standard). Mostly
   archival-context data, not vital-record indexes — value is for
   document-discovery and image enrichment, not pedigree expansion.

4. **Phase 9.9 — Polish State Archives (Szukaj w Archiwach / NAC) OAI-PMH
   revival or partnership.**
   *Pre-engineering legal:* **8–16+ weeks**. Ask explicitly for OAI-PMH
   resumption — Polish state archives have a track record of supporting
   open metadata. If granted, engineering ~1 week (OAI-PMH is a standard).

### Deferred indefinitely (reassess yearly)

- **Ancestry.com.** B2B partnership disproportionate to TreeGen's current
  scale. Ship deep-link helper only.
- **Belarus NIAB / NARB.** Blocked by current political conditions.
- **Ukraine State Archival Service direct.** Blocked by wartime
  conditions; route through FamilySearch instead.

## Pre-engineering legal/partnership work (calendar)

For planning, the items that **must finish paperwork before engineering
can start** in a useful sense:

| Item | Calendar (weeks) | Triggering event |
|---|---|---|
| FamilySearch Compatible Solution Program (production access) | 4–8 | Before public Phase-1 launch; sandbox is enough until then |
| MyHeritage Family Graph app-key approval | 4–8 | Required before any Phase 9.4 engineering |
| WikiTree commercial-use written consent | 2–4 | Required only if TreeGen monetises (Phase 12) |
| JewishGen data partnership | 8–16+ | Required before any Phase 9.6 engineering |
| JRI-Poland data partnership (separate conversation) | 8–16+ | Required before JRI-Poland engineering |
| GenTeam outreach | 4–12 | Required before Phase 9.7 engineering |
| YIVO ArchivesSpace API enablement | 4–8 | Required before Phase 9.8 engineering |
| Szukaj w Archiwach OAI-PMH / partnership | 8–16+ | Required before Phase 9.9 engineering |
| Lithuanian archives partnership | 6–10 | Required if going beyond deep-link helper |

**Recommendation:** start the JewishGen, JRI-Poland, and MyHeritage outreach
threads **now** (Phase 9.0-pre, this week). The other partnership tracks
can begin after Phase 9.1 ships and the project's track record becomes
visible.

## Weekend-hackable items (open data, no contract needed)

Things a single engineer can ship in 1–3 days, useful as Phase 9 quick
wins or as user-facing UX while partnership tracks are still in flight:

- **Wikimedia Commons place-image fetcher** with attribution rendering.
- **Deep-link smart-search helpers** (no scraping) for JewishGen All-Poland,
  All-Lithuania, All-Ukraine, JRI-Poland, Szukaj w Archiwach, GenTeam,
  Ancestry, Geni: for each TreeGen person, generate a pre-filled URL with
  surname + given-name + town variants (transliteration-aware) and let the
  user click through. Establishes the per-person «External searches»
  panel scaffolded in ROADMAP §13.2 item 9.
- **WikiTree public-profile read-only fetch** (skipping the cookie-auth
  path; only `getProfile`/`getAncestors` for public profiles).

## Open questions / followups

1. **WikiTree commercial-use consent — when to start?** Decision deferred
   to Phase 12 paid-tier scoping. ADR candidate at that point.
2. **MyHeritage outreach contact path.** Confirm current developer-relations
   email before sending the app-key request. Possibly via the
   `MyHeritage-External` GitHub org maintainers.
3. **Geni strategic future.** Worth a single low-cost question to
   MyHeritage / Geni dev contact (~1 email) before doing any Phase 9.5
   engineering: «is the public Geni API supported through the end of
   2026?». Outcome materially changes Phase 9.5 vs Phase 9.4 weighting.
4. **Polish OAI-PMH endpoint state.** Verify whether the previous
   `szukajwarchiwach.pl` OAI-PMH endpoint was preserved on the new
   `szukajwarchiwach.gov.pl`. If yes, Phase 9.9 collapses from 8+ weeks
   into a weekend.
5. **YIVO ArchivesSpace API enablement.** Single email; cheap to ask;
   answer determines whether Phase 9.8 is engineering or a pure
   deep-link play.
6. **JRI-Poland records: provenance double-attribution.** Imported
   matches will need to credit both JewishGen (host) and JRI-Poland
   (data administrator). ADR likely required at engineering time
   (treat as input to ADR-0007 §provenance).

## Sources

Vendor and platform documentation:

- [FamilySearch Developers](https://developers.familysearch.org/)
- [FamilySearch — App Approval Considerations](https://www.familysearch.org/innovate/app-approval-considerations)
- [FamilySearch — 2025 Q2 Newsletter](https://developers.familysearch.org/main/changelog/2025-q2-newsletter)
- [MyHeritage Family Graph API — Tamura Jones overview](https://www.tamurajones.net/MyHeritageFamilyGraphAPI.xhtml)
- [MyHeritage Engineering — Graph API at scale](https://medium.com/myheritage-engineering/graph-api-in-a-large-scale-environment-f5e1a228dd8a)
- [MyHeritage Terms](https://mobileapi.myheritage.com/terms-and-conditions)
- [Geni Developer Help](https://www.geni.com/platform/developer/help)
- [An Introduction to the Geni API — Tamura Jones](https://www.tamurajones.net/TheGeniAPI.xhtml)
- [WikiTree API Help](https://www.wikitree.com/wiki/Help:API_Documentation)
- [wikitree-api on GitHub](https://github.com/wikitree/wikitree-api)
- [WikiTree Honor Code](https://www.wikitree.com/wiki/Special:Honor_Code)
- [WikiTree Acceptable Use Policy](https://www.wikitree.com/about/acceptable-use.html)
- [JewishGen — databases](https://www.jewishgen.org/databases/)
- [JRI-Poland](https://jri-poland.org/)
- [JewishGen on Wikipedia](https://en.wikipedia.org/wiki/JewishGen)
- [GenTeam — B&F: Jewish Genealogy and More](https://bloodandfrogs.com/compendium/austria/genteam-web-site)
- [Center for Jewish History — Austria research guide](https://libguides.cjh.org/genealogyguides/austria/websites)
- [YIVO Online Resources](https://www.yivo.org/online-resources)
- [Guide to the YIVO Archives](https://yivoarchives.yivo.org/)
- [BillionGraves API landing](https://api.billiongraves.com/)
- [Commons:API](https://commons.wikimedia.org/wiki/Commons:API)
- [Commons:Licensing](https://commons.wikimedia.org/wiki/Commons:Licensing)
- [Szukaj w Archiwach (English)](https://www.szukajwarchiwach.gov.pl/en/archiwa-w-polsce)
- [NAC — Search the Archives](https://www.nac.gov.pl/en/digital-archive/szukajwarchiwach-pl-search-the-archives/)
- [LitvakSIG — Lithuanian State Historical Archives](https://www.litvaksig.org/information-and-tools/archives-and-repositories/lithuanian-state-historical-archives)
- [FamilySearch — Lithuania Archives and Libraries](https://www.familysearch.org/en/wiki/Lithuania_Archives_and_Libraries)
- [Archives of Belarus — genealogy](https://archives.gov.by/en/welcome-to-the-archives-of-belarus-website/genealogy-family-history)
- [NIAB Minsk official site](http://niab.by/newsite/en)
- [State Archival Service of Ukraine](https://archives.gov.ua/en/)
- [FamilySearch — Ukraine Online Genealogy Records](https://www.familysearch.org/en/wiki/Ukraine_Online_Genealogy_Records)
- [Gesher Galicia — Ukrainian State Archives](https://www.geshergalicia.org/ukrainian-archives/)
- [Genealogy APIs — Tamura Jones catalogue](https://www.tamurajones.net/GenealogyAPIs.xhtml)

Project-internal references:

- [ROADMAP §13](../../ROADMAP.md) — Phase 9 archive integrations
- [ADR-0009](../adr/0009-genealogy-integration-strategy.md) — Phase 5 hybrid B
- [ADR-0011](../adr/0011-familysearch-client-design.md) — FamilySearch client
- [ADR-0017](../adr/0017-familysearch-import-mapping.md) — FS import mapping
- [CLAUDE.md §5](../../CLAUDE.md) — no scraping rule
