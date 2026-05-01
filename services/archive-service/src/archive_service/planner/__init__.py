"""Archive Search Planner — ранжированный список архивов для следующего поиска.

Phase 15.5 / ADR-15.5 (TBD). Endpoint:
``GET /archive-planner/persons/{person_id}/suggestions``.

Логика:
1. Найти недокументированные жизненные события персоны (BIRT/DEAT/MARR/...
   без citation).
2. Для каждого события подобрать архивы из локального catalog'а
   (``archives_catalog.json``), покрывающие нужную страну + временной диапазон.
3. Отскорить по силе совпадения покрытия, уровню оцифровки, языку (per locale).
4. Вернуть top-N (default 10) суггестий через ``PlannerResponse``.

Catalog — package-data JSON; в Phase 15.5b промотируется в DB-таблицу.
"""
