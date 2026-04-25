# Samples

Тестовые GEDCOM-файлы для парсера.

## Источники

- **`personal/Ztree.ged`** — личное дерево владельца проекта (см. `D:\Projects\TreeGen\Ztree.ged`,
  копируется/симлинкается сюда скриптом `scripts/import_personal_ged.py`). **Не коммитить в публичный репозиторий**
  (см. `.gitignore`: `samples/personal/`).
- **`public/`** — обезличенные публичные образцы из разных платформ:
  - `ancestry/` — экспорты Ancestry
  - `myheritage/` — экспорты MyHeritage
  - `geni/` — экспорты Geni
  - `familysearch/` — экспорты FamilySearch
  - `gramps/` — экспорты Gramps
  - `rootsmagic/` — экспорты RootsMagic
- **`synthetic/`** — синтетические минимальные кейсы для unit-тестов (битые ссылки,
  иврит-даты, ANSEL-кодировка, проприетарные теги).

## Конвенции

- Имена файлов: `<source>_<feature>_<version>.ged` (например, `ancestry_thrulines_2024.ged`).
- Каждый файл сопровождается `.md` с описанием особенностей.
- Размер: < 5 MB для committed файлов; крупные — через Git LFS или внешний storage.
