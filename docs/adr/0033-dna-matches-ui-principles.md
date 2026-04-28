# ADR-0033: DNA matches UI principles (Phase 6.3)

- **Status:** Accepted
- **Date:** 2026-04-28
- **Authors:** @autotreegen
- **Tags:** `dna`, `ui`, `privacy`, `phase-6`

## Контекст

Phase 6.0 закрыл парсеры платформ, Phase 6.1 — алгоритм matching между
двумя `DnaTest`, Phase 6.2 — service-level persistence (consents, kits,
matching, ADR-0020), Phase 6.2.x — audit-логирование (ADR-0023). На
выходе у пользователя есть kit и (после `match_list` импорта) — таблица
`dna_matches` со списком людей, с которыми у него совпадает ДНК. Но
пока **нет UI** — пользователь не может посмотреть свой match-list и
«прицепить» матч к персоне в дереве.

Phase 6.3 закрывает этот gap: HTTP-эндпоинты dna-service для list /
detail / link, фронтенд-страницы `/dna/...`, SVG-визуализация
chromosome painting (22 autosomes + X с подсвеченными shared-сегментами).

Силы давления:

1. **Privacy by design (ADR-0012, ADR-0014).** Matches — это PII; при
   неосторожной выдаче API можно «утечь» факт существования матча,
   raw-сегменты, генотип. UI должен оставаться aggregate-only —
   chromosome / start_bp / end_bp / cM / num_snps; никаких rsid и
   genotypes.
2. **Cross-tree leakage.** У одного user'а может быть несколько trees;
   match — privacy-sensitive, и линковать его к person'е из чужого
   дерева — недопустимо (вектор бокового утечки между деревьями).
3. **Storage.** Сегменты для chromosome painting не хранятся в
   отдельной таблице (Phase 6.0 schema это не сделала). Создавать
   `dna_match_segments` с миграцией ради UI — overkill: импорт
   сегментов опционален, не каждый match их получает. Нужен лёгкий
   storage-vehicle.
4. **Auth ещё нет.** Phase 6.x подключит JWT. До тех пор фронтенд
   передаёт `owner_user_id` query-параметром / env. Это надо отметить,
   чтобы не закладывать его в архитектуру.
5. **Soft delete consent.** ADR-0012 + ADR-0020: после `revoke`
   consent'а связанные blob'ы хард-удаляются, но `DnaKit.deleted_at`
   проставляется. UI не должен светить такие kit/match — рендерим как
   404, как если бы их никогда не было.

## Рассмотренные варианты

### A — Расширить ADR-0014 секцией про UI (отвергнут)

ADR-0014 — про **алгоритм** matching, не про UI. Пихнуть туда
страницы `/dna/...`, SVG-конвенции и privacy для UI слой — размоет
фокус ADR.

### B — Новый ADR-0033 «DNA matches UI principles» (выбран)

Отдельный ADR фиксирует:

- routing pages,
- privacy guards endpoint'ов,
- storage chromosome painting,
- chromosome painting visual contract.

Privacy и UI решения чаще пересматриваются, чем алгоритм; разделение
ADR упростит ревизию (например, когда добавим Bayesian relationship
panel — это апдейт UI, ADR-0033 пересматривается, ADR-0014 — нет).

### C — Хранить сегменты в отдельной таблице `dna_match_segments`

- ✅ Чище структура, индексируем по `(match_id, chromosome)`.
- ✅ Легче считать aggregate-статистику.
- ❌ Лишняя миграция, ещё не в Phase 6.3 scope.
- ❌ Не каждый match получает сегменты (Ancestry exports их обычно не
  отдают, только MyHeritage / 23andMe).
- ✅ **Future option:** Phase 6.5 (imputation + chromosome browser
  features) — там, где сегменты будут first-class, пора будет создать
  таблицу. Phase 6.3 живёт в JSONB.

### D — Хранить сегменты в `DnaMatch.provenance['segments']` (выбран)

JSONB-array внутри `provenance` (поле уже есть благодаря
`ProvenanceMixin`):

```json
{
  "segments": [
    {"chromosome": 7, "start_bp": 50000000, "end_bp": 80000000, "cm": 22.5, "num_snps": 5000}
  ],
  "shared_ancestor_hint": {"label": "Иванов И.И. (1850)", "person_id": null, "source": "user_note"}
}
```

- ✅ Без миграции — `provenance` уже jsonb.
- ✅ Эволюция: если в Phase 6.5 решим достать в отдельную таблицу,
  миграция читает `provenance['segments']` и перекладывает.
- ❌ Невозможно индексировать (`GIN` на jsonb можно, но cost'ы того
  не стоят при N сегментов на match ≤ ~30).
- ❌ Слабая type safety: backend-чтение делает defensive parsing
  (см. `_segments_from_provenance` в `dna_matches.py`).

## Решение

1. **Добавляем endpoints** в dna-service:
   - `GET /dna-kits` — список kit'ов (`owner_user_id` query-параметр).
   - `GET /dna-kits/{kit_id}/matches?limit&offset&min_cm&predicted` —
     постраничный список матчей kit'а с фильтрами.
   - `GET /dna-matches/{match_id}` — детальная карточка с chromosome
     painting (`segments`) и `shared_ancestor_hint`.
   - `PATCH /dna-matches/{match_id}/link` body `{tree_id, person_id}`.
   - `DELETE /dna-matches/{match_id}/link` — unlink, идемпотентен.

2. **Privacy guards**:
   - Soft-deleted kit (consent revoked) → 404 на любой match-эндпоинт.
   - `match.tree_id != person.tree_id` → 409 (cross-tree refusal).
   - `payload.tree_id != match.tree_id` → 409 (фронт не должен
     случайно линкануть match не из активного дерева).
   - Сегменты в response — только агрегаты; rsid/genotype не парсятся
     даже если случайно окажутся в provenance jsonb.

3. **Frontend**:
   - `/dna` — список китов.
   - `/dna/[kitId]/matches` — таблица с фильтрами `min_cm` и
     `predicted` (case-insensitive substring) + пагинация.
   - `/dna/matches/[matchId]` — chromosome painting + link form.
   - `ChromosomePainting` компонент: SVG, 22 autosomes + X, всегда
     все 23 строки независимо от количества сегментов (UX-стабильно).
   - Длины хромосом — GRCh37/hg19 (соответствует ADR-0014 reference
     build); track scaling по самой длинной (chr1).

4. **Storage сегментов** — `DnaMatch.provenance['segments']` jsonb.
   Структура зафиксирована выше. Defensive parsing на backend'е:
   незнакомая/legacy форма → `[]`, не падаем.

5. **Hints — это не facts.** `shared_ancestor_hint` хранится в
   `provenance['shared_ancestor_hint']`, читается UI для подсказки,
   но **не используется как evidence** в hypothesis-engine (Phase 7).
   Чтобы hint стал evidence — нужен явный review через UI.

6. **Auth scaffolding.** Frontend читает `?user=<uuid>` query или
   `NEXT_PUBLIC_DEMO_DNA_USER_ID` env. Когда Phase 6.x подключит JWT —
   обе fallback-ветки убираются за один коммит.

## Последствия

**Положительные:**

- Пользователь видит match-list и chromosome painting прямо в UI,
  без сторонних tools (DNA Painter, GEDmatch).
- Cross-tree privacy-guard zatvodit `tree`-границу как
  privacy-boundary первого класса.
- ChromosomePainting — самостоятельный компонент, переиспользуется
  Phase 6.4+ (triangulation view).
- ADR-0033 фиксирует storage-конвенцию для сегментов; миграция в
  отдельную таблицу — обратимая, контракт jsonb описан здесь же.

**Отрицательные / стоимость:**

- Defensive parsing JSONB-сегментов добавляет ~30 строк парсера
  (стоимость гибкости storage-vehicle'а).
- Без auth — `owner_user_id` в query-параметре (debug UX); не
  production-ready, но фиксируется в ADR как known limitation.
- ChromosomePainting фиксирует длины GRCh37: переход на GRCh38 в
  будущем потребует подменить таблицу длин (~25 LOC).

**Риски:**

- **Production-deploy без auth.** Если кто-то по ошибке выкатит UI
  с DEMO env-var — match-list любого user'а виден всем. Mitigation:
  страница рендерит `?user=...` баннер в проде; CI/CD заведём только
  после Phase 6.x auth (нельзя смержить в `main` пока эта секция
  ADR не помечена как Superseded).
- **Provenance jsonb разрастается.** Match с 30 сегментами + hint —
  ~3 KB. На 10⁵ matches — 300 MB. Допустимо в Phase 6.3; миграция
  на таблицу — Phase 6.5.

## Когда пересмотреть

- **Phase 6.x подключает JWT** → удаляем `?user=...` fallback,
  ADR-0033 §6 superseded.
- **Сегменты становятся first-class** (triangulation, IBD2,
  imputation в Phase 6.5) → миграция `provenance['segments']` →
  `dna_match_segments` table; ADR-0033 §4 superseded.
- **GRCh38 default** → `CHROMOSOME_LENGTHS_BP` меняется на GRCh38;
  упоминание GRCh37 в этом ADR заменяется.
- **Внешний DNA Painter / GEDmatch экспорт-формат** появляется → UI
  получает «Export to DNA Painter» кнопку, ADR обновляется секцией
  про export-контракты.

## Ссылки

- Связанные ADR:
  - ADR-0012 (DNA processing privacy & architecture) — privacy
    boundary, на которую опираются guard'ы Phase 6.3.
  - ADR-0014 (DNA matching algorithm) — какие данные мы получаем
    в `MatchResponse`, какой reference build (GRCh37).
  - ADR-0020 (DNA service architecture) — consent revoke + soft-delete
    behaviour, на котором держится 404-логика Phase 6.3.
  - ADR-0023 (DNA-aware inference) — почему `shared_ancestor_hint`
    остаётся hint, а не evidence.
- ROADMAP §10 (Phase 6 — DNA Analysis Service), §10.4 (Phase 6.3 —
  Matches UI).
- Внешние:
  - [Shared cM Project 4.0 — DNA Painter](https://dnapainter.com/tools/sharedcmv4)
  - [GRCh37/hg19 chromosome lengths](https://www.ncbi.nlm.nih.gov/grc/human)
