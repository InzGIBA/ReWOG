# T-15: Документация API (mkdocs)

- **Статус:** не начата
- **Приоритет:** medium
- **Оценка:** S (2–3 дня)
- **Зависимости:** нет
- **Квартал roadmap:** Q3 2027

## Цель

Публичный сайт документации на GitHub Pages: существующие документы
(architecture, configuration, database, mitm-capture) + автогенерируемый
API-референс по `core/`, `utils/` (README уже обещает «Using as a Python
library» — дать этому настоящую опору).

## Контекст: что уже есть в коде

- `docs/*.md` — четыре хороших документа + images/; контент готов,
  нужна навигация и площадка.
- Код типизирован (mypy strict) и покрыт docstring-ами — материал
  для mkdocstrings есть; недостающие docstring-ы дописать по ходу
  (точечно, публичные классы/функции).
- `.github/` — CI есть; Pages деплой — ещё один workflow.

## Требования

### Функциональные

- mkdocs + mkdocs-material + mkdocstrings[python] в dev-deps
  (или requirements-docs.txt — решить по вкусу проекта, у проекта
  uv: `dependency-groups`).
- Структура: Home (из README-секции Overview), Getting started
  (Quick start из README), Usage (CLI-команды), Configuration
  (docs/configuration.md), Architecture (docs/architecture.md),
  Database (docs/database.md), Capturing sessions
  (docs/mitm-capture.md), API Reference (авто по модулям core/ и
  utils/), Contributing (CONTRIBUTING.md).
- API Reference: страницы по модулям (`wog_dump.core.download`,
  ...scene, ...glb, ...unpack, ...api/auth/keys и т.д.) —
  mkdocstrings рендерит сигнатуры, типы и docstring-ы.
- Деплой: workflow `docs.yml` — build на PR (проверка), publish на
  push в main → GitHub Pages (действие actions/deploy-pages).
- `mkdocs serve` работает локально без спец-настройки.

### Нефункциональные

- Существующие docs/*.md не переезжают физически (mkdocs-nav
  ссылается на текущие пути) — минимальный diff.
- README остаётся главным входом; на сайт ведёт бейдж/ссылка.
- Сборка docsite — < 2 мин (кэш pip в CI).

## План реализации

1. `mkdocs.yml` (тема material: palette, language en, nav как выше).
2. Индексные md-страницы-связки (обёртки над существующими
   документами, контент — прямые включения или тонкие
   страницы-заглушки с ссылкой; выбрать прямой путь без дублей).
3. API-страницы: по одной `::: wog_dump.core.<module>` на модуль;
   пробелы в docstring-ах закрыть (аудит: у всех публичных классов
   и функций, перечисленных в architecture.md, есть docstring?).
4. Workflow `docs.yml` (build-check на PR, deploy на main).
5. Включить сборку в pre-commit/CI-чек: `mkdocs build --strict`
   (strict ловит битые ссылки).

## Критерии приёмки

- [ ] Сайт открывается на GitHub Pages: навигация, поиск, код
      подсвечен, все ссылки живые (`--strict` зелёный)
- [ ] API Reference содержит все модули из architecture.md с
      сигнатурами и типами
- [ ] PR ломает сборку доки — CI красный (проверить битой ссылкой
      на тестовой ветке)
- [ ] `mkdocs serve` работает локально по инструкции из одной
      команды в CONTRIBUTING

## Тесты

- CI-шаг `mkdocs build --strict` — и есть тест этой задачи.

## Риски и открытые вопросы

- mkdocstrings + hatchling src-layout: настроить пути (`SITE_DIR`,
  пакеты через `pip install -e .` в CI перед сборкой) — типовая
  настройка, риск низкий.
