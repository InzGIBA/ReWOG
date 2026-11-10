# T-10: Прогресс-бары Rich

- **Статус:** готово
- **Приоритет:** medium
- **Оценка:** S (2–3 дня)
- **Зависимости:** нет
- **Квартал roadmap:** Q2 2027

## Цель

Полный pipeline — это десятки минут скачивания и обработки, сейчас
пользователь видит лог-строки. Дать живой прогресс: сколько файлов
всего/осталось, байты и ETA там, где известен размер, поэтапный
прогресс `full-pipeline`. Логи не загромождать: всё, что важно,
свёртывается в одну обновляемую строку.

## Контекст: что уже есть в коде

- Rich уже в зависимостях: `utils/logging.py` использует RichHandler.
- `core/download.py`: `download_assets()` / `download_assets_batched()`
  уже знают общее число активов, состояние каждого; размер известен
  после HEAD/первых байтов (`get_asset_size`). `RateLimiter` в
  `core/http.py` — на каждый запрос.
- `core/decrypt.py::AssetDecryptor`, `core/unpack.py::AssetUnpacker` —
  перебор файлов по списку; прогресс = N из M.
- `cli/pipeline.py` — цепочка стадий full-pipeline: место для общего
  «этапного» индикатора.

## Требования

### Функциональные

- Прогресс для стадий:
  - `download-assets`: Progress с задачей на файл (колонки: имя файла
    (обрезанное), байты `downloaded/total`, процент, ETA, скорость);
    при неизвестном Content-Length — индикатор без процента;
  - `decrypt-assets`, `unpack-assets`: задача на файл, count-based
    (`3/47`), плюс spinner на время обработки одного файла;
  - `full-pipeline`: верхнеуровневый индикатор стадий
    (keys → weapons → assets → decrypt → unpack) с текущей стадией;
  - `download-weapons`: одиночный spinner.
- Сводка по завершении: время стадии, объём, число файлов —
  одной строкой (заменяет текущий «список-лог», если он дублируется).
- Поведение в не-TTY (CI, piped): прогресс отключается автоматически,
  логи как сейчас. Явный флаг `--no-progress` — принудительно.
- Прогресс пишется в stderr (Rich-стандарт), логи — тоже stderr:
  коллизии решить через единый `Progress` + `RichHandler` в одном
  Live-облаке (Rich поддерживает `progress.console` и логирование
  через прогресс: `progress.log(...)`).
- `--verbose/--debug` не ломают отображение (отладочные логи идут
  в файл или stderr без Live — решение: при verbose уровень логов
  вне Live, прогресс упрощается).

### Нефункциональные

- Новый модуль `utils/progress.py`: фабрика
  `create_progress(stage: StageKind) -> Progress`, адаптер
  `ProgressReporter` с методами `file_started/file_finished/error`;
  core-модули принимают опциональный reporter (паттерн: протокол +
  NullObject по умолчанию — существующие вызовы не меняются).
- Никакого вывода прогресса из core в stdout (stdout — только
  результаты-данные, например JSON команд).

## План реализации

1. `utils/progress.py`: протокол reporter, Rich-реализация, Null.
2. Прокинуть reporter в `DownloadManager.download_assets`,
   `AssetDecryptor`, `AssetUnpacker` (опциональные параметры).
3. CLI: создание reporter по TTY-детекту и `--no-progress`, вывод
   сводок.
4. `cli/pipeline.py`: этапный индикатор.
5. Проверка в CI: `wog-dump --no-progress full-pipeline` и пайп
  `... | cat` — вывод без ANSI-мусора.

## Критерии приёмки

- [ ] `download-assets` на реальном наборе: прогресс живой, ETA
      осмысленная, сводка по завершении
- [ ] `decrypt-assets`/`unpack-assets`: счётчик файлов работает
- [ ] `full-pipeline`: видно текущую стадию и общий статус
- [ ] Пайп в файл (`| cat > out.txt`) не содержит ESC-последо-
      вательностей; `--no-progress` даёт чистый лог
- [ ] Существующие тесты core-модулей не падают (reporter
      опционален, дефолт Null)

## Тесты

- `tests/unit/test_progress.py`: TTY-детект (mock `sys.stderr.isatty`),
  флаг `--no-progress`, Null-reporter вызывается без ошибок.
- CLI-тест: вывод `download-assets` (mock сети) при `--no-progress`
      не содержит ANSI-кодов.

## Риски и открытые вопросы

- Перегрузка UI при параллельных загрузках (`--max-threads 16`):
      показывать не 16 баров, а один агрегированный + текущие имена;
      выбрать один вариант и зафиксировать.
