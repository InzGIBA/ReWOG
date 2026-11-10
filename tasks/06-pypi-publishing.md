# T-06: Публикация на PyPI

- **Статус:** не начата
- **Приоритет:** high
- **Оценка:** S (1–2 дня)
- **Зависимости:** нет
- **Квартал roadmap:** Q2 2027

## Цель

Установка в одну команду без git clone:

```bash
pip install wog-dump        # или: uv tool install wog-dump
wog-dump --help
```

Публикация автоматическая: пуш тега `vX.Y.Z` → GitHub Actions собирает
и публикует пакет (trusted publishing, без ручной загрузки и токенов).

## Контекст: что уже есть в коде

- `pyproject.toml` — полный метаданный проект (hatchling, версия из
  `src/wog_dump/__init__.py`, scripts `wog-dump` и `wog-convert-normals`).
- `.github/` — CI уже существует; релизные коммиты `Release: vX.Y.Z`
  уже делаются вручную.
- `resources/blender_setup.py` — данные внутри пакета; проверить, что
  hatch включает их в wheel (по умолчанию hatchling берёт только `.py`).

## Требования

### Функциональные

- `pip install wog-dump` из чистого venv (Linux/macOS/Windows) даёт
  работающий `wog-dump --help` и `wog-dump info`.
- Extras сохраняются: `wog-dump[dev]`, `[test]`; новые из других задач
  (`[usd]`, `[web]`) добавятся к той же схеме.
- `resources/` включены в wheel (шаблон blender_setup, а позже viewer/
  и render-шаблон) — через `[tool.hatch.build.targets.wheel.force-
  included]` или package-data.
- Trusted publishing: workflow `publish.yml` — trigger `push: tags:
  ['v*']`, окружения PyPI, сборка через `python -m build`, публикация
  `pypa/gh-action-pypi-publish`; аттестация provenance включена.
- Первый релиз вручную проверен: скачать wheel с TestPyPI, установить,
  прогнать smoke-тест (`wog-dump --help`, `wog-dump info` в чистом
  окружении без репозитория).

### Нефункциональные

- Название пакета: проверить занятость `wog-dump` на PyPI; если занято —
  `rewog` (решить до начала, отразить в pyproject и README).
- `uv.lock` в sdist не входит; README-инструкция для контрибьюторов
  остаётся через `uv pip install -e .`.
- Метаданные: `[project.urls]` (Homepage, Issues, Changelog, Docs),
  classifiers (Python 3.12+, License MIT, OS-independent, статус
  Beta), `readme = "README.md"` с content-type.
- Версионирование остаётся semver, версия — единственный источник
  `src/wog_dump/__init__.py::__version__`.

## План реализации

1. Дозаполнить `pyproject.toml`: urls, classifiers, readme, включение
   resources в wheel.
2. Локальная проверка сборки: `python -m build` → проверить wheel
   содержимым (`unzip -l`): пакеты + resources + LICENSE.
3. Аккаунт/проект на PyPI (+ TestPyPI для пробы), настроить trusted
   publisher на GitHub-репозиторий.
4. Workflow `.github/workflows/publish.yml`; тестовый прогон на TestPyPI
   (отдельный environment).
5. Релиз v2.8.0 (или ближайший) тегом → публикация; обновить README:
   Quick start через `pip install wog-dump`, git clone — как
   альтернатива для разработки.

## Критерии приёмки

- [ ] `pip install wog-dump` работает в чистом venv на Linux; smoke:
      `wog-dump --help`, `wog-dump info` (без конфига — штатный вывод
      пустого статуса, не трейсбек)
- [ ] Wheel содержит LICENSE и resources/
- [ ] Пуш тега публикует релиз автоматически; Actions-лог зелёный
- [ ] README обновлён: установка одной командой
- [ ] CHANGELOG отражает релиз по принятым конвенциям

## Тесты

- CI-шаг (existing workflow): `pip install dist/*.whl` в чистом
  окружении + smoke-команды — как гарантия целостности wheel на
  каждый PR после merg'а задачи.

## Риски и открытые вопросы

- Имя `wog-dump` может быть занято/сквоттировано — проверить до
  старта; fallback-имя согласовать заранее.
- Обфусцированная тема авторских прав на контент игры: PyPI-страница
  должна прямо наследовать формулировку README про личное/
  исследовательское использование — не добавлять нового риска.
