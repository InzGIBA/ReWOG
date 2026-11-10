# T-09: Docker-образ

- **Статус:** не начата
- **Приоритет:** medium
- **Оценка:** S (2–3 дня)
- **Зависимости:** нет
- **Квартал roadmap:** Q2 2027

## Цель

Воспроизводимое окружение без Python на хосте: образ с установленным
`wog-dump`, том для данных, инструкции запуска одной строкой. Образ
публикуется в GHCR автоматически при релизе.

```bash
docker run --rm -it -v wog-data:/data ghcr.io/inzgiba/rewog:latest catalog
docker run --rm -it -v wog-data:/data ghcr.io/inzgiba/rewog:latest full-pipeline --update-keys
```

## Контекст: что уже есть в коде

- Установка — чистый Python-пакет (pyproject/hatchling), тяжёлых
  системных зависимостей нет: базой подходит `python:3.12-slim`.
- Конфигурация — Pydantic-settings + `.env`; все пути runtime
  настраиваются (`base_dir` в `core/config.py`, дефолт `Path.cwd()`),
  БД — `db_path`. Значит, достаточно `WOG_BASE_DIR=/data`.
- `.github/` — CI есть, релизы тегами — сценарий публикации образа
  аналогичен T-06.

## Требования

### Функциональные

- `Dockerfile` (multi-stage: builder с uv/pip для колёс + runtime
  на slim; установка пакета из исходников репозитория — задача не
  зависит от T-06/PyPI).
- Entrypoint `wog-dump`; флаги передаются как args команды `docker run`.
- Не-root пользователь внутри образа (uid/gid настраиваемые через
  build-args `UID/GID`, дефолт 1000); том `/data` — права пользователя.
- `WOG_BASE_DIR=/data` зашит в образ (ENV); документированные точки
  монтирования: `/data` (runtime), `.env` по желанию (`--env-file`).
- docker-compose пример в `docker-compose.yml` (сервис wog, том wog-data).
- CI: workflow `docker.yml` — build + push в GHCR на теги `v*`
  (образы `latest` + семантические теги), и build-without-push на PR
  (проверка сборки).
- README-раздел «Docker» с тремя командами: pull, каталог, полный
  pipeline.

### Нефункциональные

- Размер образа — ориентир < 400 МБ (slim + deps; UnityPy/pillow
  потянут numpy — учесть).
- Слои: зависимости кэшируются отдельно от исходников (сначала
  копия pyproject + установка deps, потом исходники) — быстрый
  rebuild при смене кода.
- `docker run --rm wog-dump --help` работает без томов и сети.

## План реализации

1. `Dockerfile` + `.dockerignore` (исключить `runtime/`, `.venv`,
   кэши — важно: в репо может лежать 10+ ГБ runtime).
2. Проверка локально: build, run `--help`, run `info`, run полного
   pipeline на малом наборе (`--weapons glock17`) с томом.
3. `docker-compose.yml`.
4. Workflow `docker.yml` (GHCR, метаданные-действия docker/metadata-
   action для тегов версий).
5. README-раздел.

## Критерии приёмки

- [ ] `docker build` зелёный локально; образ проходит
      `docker run --rm <img> --help` и `info` без ошибок
- [ ] Полный pipeline glock17 в контейнере с томом: файлы появляются
      в томе с правами пользователя (не root)
- [ ] PR-проверка сборки образа работает; тег v* публикует образ в
      GHCR с тегами версий
- [ ] `.dockerignore` исключает runtime/ и локальные артефакты
      (проверить `docker build` из репо с заполненным runtime/ не
      отправляет гигабайты в контекст)

## Тесты

- Ручной чек-лист в PR (команды выше); автоматизация — CI-шаг сборки
  образа + smoke (`docker run --rm <img> --help`).

## Риски и открытые вопросы

- SQLite в томе на разных файловых системах хоста (Windows/macOS
  mount) — проверить `wog-dump info` с примонтированной БД на
  macOS/Windows-хосте; при проблемах — документировать нюанс
  (performance/locking) в README-разделе Docker.
