# ADR-0013: Tree visualization tech choice (Phase 4.3 — pedigree chart)

- **Status:** Accepted
- **Date:** 2026-04-27
- **Authors:** @autotreegen
- **Tags:** `web`, `frontend`, `visualization`, `phase-4`

## Контекст

Phase 4.3 — первый «wow»-момент для пользователя: человек открывает
сайт и видит свою семью графически, а не списком. До сих пор `apps/web/`
рендерит:

- `/trees/[id]/persons` — пагинированный список карточек (Phase 4.1).
- `/persons/[id]` — карточка с именами и событиями (Phase 4.1).

Phase 4.3 добавляет **pedigree chart** (предки): корневая персона слева,
родители-бабушки-прадеды раскручиваются вправо/вверх. Минимум до 5–6
поколений (≤63 узла), pan + zoom, клик на узле → переход на его pedigree.
Editing, descendant chart, DNA-overlay, граф с циклами — всё это
out-of-scope для 4.3, идут в более поздние фазы.

Stack уже зафиксирован ADR-0001 (Next.js 15, React 19, TS strict, Tailwind 4).
Нужен слой `apps/web/src/app/persons/[id]/tree/page.tsx` + библиотека
для дерева. Главные требования:

1. **Интерактив:** pan, zoom, hover, click — без ручной возни.
2. **React-friendly:** declarative API, рендер узлов через React-компоненты
   (нужны Tailwind-стили, ссылки Next.js, focus-ring).
3. **Tree-layout из коробки:** corner-case'ы (overlap, spacing, centering)
   решены, не пишем layout-алгоритм с нуля.
4. **Размер бандла приемлем:** до ~100 KB gzipped — мы уже отдаём ~100 KB
   shared chunks, frontend-job в CI пока не пинит лимит, но не хочется
   добавлять «второй React».
5. **Будущее:** при росте до 10⁴–10⁶ узлов (DNA cluster, descendants
   глубоких деревьев) уйдём на canvas/WebGL. Решение для 4.3 не должно
   ломать этот переход.

## Рассмотренные варианты

### Вариант A — React Flow (`reactflow` v11)

Популярный (~20k★) фреймворк для node/edge графов: declarative, React-нативный
рендер узлов, pan/zoom/minimap из коробки, плагины (background, controls).

- ✅ Большое community, хорошая документация, регулярные релизы.
- ✅ Узлы — обычные React-компоненты, можно tailwind/Next Link/foreignObject.
- ✅ Pan / zoom / drag — нативные, accessibility-friendly.
- ✅ Подходит для будущих фаз (DNA-cluster — это произвольный граф,
  не дерево; React Flow туда отлично ложится).
- ❌ Не специализирован под tree-layouts: чтобы получить «правильный»
  pedigree, надо либо считать координаты руками (D3 hierarchy → React
  Flow positions), либо подключать `dagre`/`elkjs`/`d3-flextree`.
  Для MVP это лишний кусок кода.
- ❌ Бандл ~80 KB minified + ~25 KB gzipped — терпимо, но больше, чем
  нужно для read-only pedigree.
- ❌ API заточен под directed-acyclic graphs; tree-специфичных хелперов
  нет, expand/collapse-узлы пишутся вручную.

### Вариант B — D3 + custom React wrapper

`d3-hierarchy` для layout (`d3.tree()`) + `d3-zoom` для pan/zoom, рендер
SVG руками внутри React-компонента (либо `react-d3-graph`-style тонкая
обёртка, либо useEffect-based mount).

- ✅ Полный контроль: можно сделать ровно то, что нужно, без чужих
  абстракций.
- ✅ D3 sub-modules tree-shake-аются — можем взять только `d3-hierarchy`
  - `d3-zoom` (~12 KB total min+gz).
- ✅ Знание D3 пригодится в Phase 5+ (timeline, geo-map, DNA chromosome
  browser — всё это идиоматично делать на D3).
- ❌ Больше boilerplate: pan/zoom-стейт, ref-cleanup, ресайз обработчик,
  highlighted-node, focus management, keyboard-навигация — пишем сами.
- ❌ Императивный D3-mindset плохо ложится на React-рендер. Mixing
  «React владеет DOM» с «D3 манипулирует SVG» — известный источник багов
  (двойной рендер, stale-closures).
- ❌ MVP откладывается: ~день на «сборку дерева руками» вместо ~часа
  на интеграцию declarative-библиотеки.

### Вариант C — `react-d3-tree` (рекомендуется)

Готовая React-библиотека вокруг `d3-hierarchy`. Один компонент `<Tree>`,
declarative props (`data`, `orientation`, `pathFunc`, `zoomable`,
`renderCustomNodeElement`).

- ✅ Pedigree-friendly out of the box: горизонтальная или вертикальная
  ориентация, step / curve / straight pathFunc.
- ✅ `renderCustomNodeElement` пропускает SVG `<foreignObject>` →
  Tailwind-классы и Next `<Link>` работают как обычно.
- ✅ Pan / zoom / centering / collapsible-узлы — флагами.
- ✅ Бандл ~50 KB minified (~15 KB gzipped после tree-shaking) — внутри
  тащит `d3-hierarchy` + `d3-zoom`, которые пригодятся и нам напрямую.
- ✅ Скорость интеграции для MVP: ~2 часа от установки до видимого
  pedigree вместо дня на D3-руками.
- ❌ Меньше гибкости, чем чистый D3: кастомный layout (например,
  «мужчина всегда сверху, женщина снизу» при равной глубине) требует
  препроцессинга данных перед передачей в `<Tree>`.
- ❌ Поддержка библиотеки умеренная (не Vercel-tier), но активная;
  миграционных breaking-changes за последний год не было.
- ❌ При движении к DNA-cluster (граф с циклами, не дерево) library
  не подходит — нужно будет переезжать на A или B. Для 4.3 это не блокер.

### Вариант D — SVG + custom code (zero deps)

Свой layout (BFS по поколениям, фиксированный шаг по X/Y), свой `<svg>`
с `<g transform>` для pan/zoom через React-state.

- ✅ Никаких dependency, ни одного KB лишнего.
- ✅ Полный контроль и предсказуемость.
- ❌ Самый большой объём кода: layout-overlap detection, hover-стейт,
  виртуализация при росте, accessibility — всё руками.
- ❌ Время до видимого результата — ещё больше, чем у B.
- ❌ Для MVP plain-SVG — это «техническое аскетство», не оправданное
  целью фазы.

## Решение

Выбран **Вариант C — `react-d3-tree`** для Phase 4.3 MVP.

Конкретно:

- `pnpm -F @autotreegen/web add react-d3-tree` (latest stable).
- Pedigree рендерится через `<Tree data={tree} orientation="horizontal"
  pathFunc="step" zoomable collapsible={false} renderCustomNodeElement={...}>`.
- `renderCustomNodeElement` использует `<foreignObject>` + Tailwind-узел
  с именем (bold), годами жизни (dimmed), sex-иконкой (♂/♀/⚧),
  hover-эффектом и Next `<Link>`-обёрткой клика.
- API возвращает `AncestorTreeNode` (рекурсивная Pydantic-модель с
  `father` / `mother`); конвертер на фронте превращает её в формат
  react-d3-tree (`{ name, attributes, children: [...] }`).

При выборе именно C основная мотивация:

- 4.3 — read-only pedigree-MVP. C закрывает требования полностью с
  минимальным кодом.
- Если в 4.4–4.6 окажется, что нужен полный контроль (custom layout
  rules, expand/collapse, infinite-scroll по поколениям) — миграция на
  D3-direct будет обоснованной и бюджет на неё запланирован.
- React Flow остаётся явно более удобным для будущего DNA-cluster
  (произвольный граф) — это разные use-case'ы, не конкуренты.

## Последствия

**Положительные:**

- Phase 4.3 закрывается за ~4 PR (ADR + endpoint + page + linkage)
  вместо ~7+ при Variants B/D.
- `<Tree>` принимает любую древовидную структуру — можно переиспользовать
  для descendants-chart (Task 5 опционально).
- D3-hierarchy всё равно подтягивается транзитивно — если нам понадобится
  layout-helper отдельно, импортируем без удвоения веса.

**Отрицательные / стоимость:**

- Зависимость от стороннего пакета (~50 KB min). Bundle первой загрузки
  `/persons/[id]/tree` вырастет с текущих ~125 KB до ~175–200 KB. Это
  всё ещё сильно ниже среднего по индустрии и на 4G грузится <2 сек.
- Если потребуется кастомный layout (например, балансировка под
  узкие/широкие ветки), придётся либо препроцессить данные руками, либо
  мигрировать на B.

**Риски:**

- Поддержка `react-d3-tree` — open-source без enterprise-backing. На
  случай заморозки проекта откатимся на D3-hierarchy + d3-zoom (тот
  же layout-движок), сохранив data-shape.
- Большие деревья (>200 узлов): рендер всех `<foreignObject>` тяжёл,
  возможны просадки FPS при pan. Mitigation: на API лимит generations=10
  - UI-warning при больших значениях. Если упрёмся — переход на
  canvas-renderer (отдельный ADR).
- A11y: SVG-узлы по умолчанию не focus-able, keyboard-навигация ограничена.
  Mitigation: добавить `tabIndex` и `aria-label` в `renderCustomNodeElement`,
  вынести «View family tree»-кнопку как линейный fallback (есть в Task 4).

**Что нужно сделать в коде:**

1. `services/parser-service/src/parser_service/api/trees.py` — новый
   endpoint `GET /persons/{id}/ancestors?generations=N`. Recursive CTE
   на `family_children → families → husband/wife`. Default 5, max 10.
2. `services/parser-service/src/parser_service/schemas.py` — новая
   `AncestorTreeNode` (рекурсивная) + `AncestorTreeResponse`-обёртка.
3. `apps/web/src/app/persons/[id]/tree/page.tsx` — `<Tree>` + custom
   node renderer + конвертер `AncestorTreeNode` → `RawNodeDatum`.
4. `apps/web/src/lib/api.ts` — типы `AncestorTreeNode` + `fetchAncestors`.
5. `apps/web/src/app/persons/[id]/page.tsx` и
   `/trees/[id]/persons/page.tsx` — кнопки/ссылки «View family tree».
6. Зависимость `react-d3-tree` в `apps/web/package.json`.

## Когда пересмотреть

- **>200 узлов с лагами:** нужен canvas/WebGL renderer (separate ADR).
- **Граф с циклами** (DNA-cluster, complex relationships): уйдём
  на React Flow или D3-force.
- **Custom layout rules** (мужской/женский ряд, выравнивание по
  поколениям с разной плотностью): мигрируем на D3-direct (Variant B),
  сохранив data-shape.
- **Изменение branding-системы** (если Tailwind 4 заменяется или
  brand-токены пересматриваются): `renderCustomNodeElement` потребует
  обновления.

## Ссылки

- Связанные ADR: ADR-0001 (stack), ADR-0010 (web first slice — Phase 4.1).
- Brief: `docs/agent-briefs/phase-4-3-tree-visualization.md`.
- ROADMAP § 9 (Phase 5 — visualization): зафиксированы будущие виды
  (descendant, hourglass, family group sheet, timeline, geo-map,
  DNA cluster). Этот ADR закрывает только pedigree.
- `react-d3-tree`: <https://bkrem.github.io/react-d3-tree/>
- `react-flow`: <https://reactflow.dev/>
- `d3-hierarchy`: <https://d3js.org/d3-hierarchy>
