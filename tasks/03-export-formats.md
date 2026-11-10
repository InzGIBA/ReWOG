# T-03: Форматы экспорта — USD/USDZ, STL, FBX

- **Статус:** готово
- **Приоритет:** medium
- **Оценка:** M
- **Зависимости:** нет
- **Квартал roadmap:** Q1 2027

## Цель

Сегодня меши уходят только в OBJ, сцены — только в GLB. Расширить форматы
вывода под реальные сценарии: USD/USDZ для AR (iPhone Quick Look) и
пайплайнов Pixar-стека, STL для 3D-печати и CAD, FBX для игровых движков
(через headless Blender).

```bash
wog-dump build-scene ak47 --format glb,usdz   # несколько форматов за запуск
wog-dump build-scene ak47 --format stl        # STL сцены
wog-dump unpack-assets --mesh-format stl      # STL на уровне unpack
```

## Контекст: что уже есть в коде

- `core/glb.py::GLBWriter` — собственный writer, glTF-сцена уже содержит
  иерархию, материалы, текстуры. GLB — естественный промежуточный формат:
  `build_scene()` уже отдаёт байты сцены.
- `core/unpack.py::MeshExtractor` пишет OBJ (+ MTL) на пакетах UnityPy.
- `cli/scene.py` и `cli/unpack.py` — точки проброса флагов.
- `resources/blender_setup.py` — шаблон скрипта для Blender; подход
  «генерируем скрипт и запускаем headless» уже опробован в проекте.

## Решение по форматам (обоснование)

| Формат | Способ | Обоснование |
|--------|--------|-------------|
| STL (binary) | Свой writer в новом `core/export.py` | Тривиальный формат (~100 строк), без зависимостей |
| USD/USDZ | Пакет `usd-core` (PyPI, от Pixar) | Официальный стек, ставится как extra `wog-dump[usd]` |
| FBX | Headless Blender (опционально) | Чистого Python-FBX-writer нет; Blender импортирует GLB и экспортирует FBX — надёжный путь |

## Требования

### Функциональные

- `--format` в `build-scene`: список через запятую из
  `glb,usd,usdz,stl,fbx`; `glb` остаётся всегда (остальные строятся
  из готовой сцены, GLB — источник геометрии).
- STL: binary STL в метрах; `--stl-units {m,mm,cm}` — масштабирование
  при экспорте; опция `--stl-per-part` — по файлу на часть вместо
  одного объединённого меша.
- USD: сцена с иерархией узлов и мешами; материалы — UsdPreviewSurface
  с текстурами (базовый albedo; normal/roughness — best effort).
- USDZ: валидный архив для AR Quick Look (текстуры внутри, без внешних
  ссылок; размер < 100 МБ — проверить на самом большом оружии).
- FBX: `--format fbx` требует найденный Blender (см. обнаружение в
  «Нефункциональных»); если Blender не найден — понятная ошибка с
  инструкцией, ненулевой exit code.
- `unpack-assets --mesh-format {obj,stl}`: меши в unpacked/ пишутся
  в выбранном формате (OBJ остаётся дефолтом).

### Нефункциональные

- Новые тяжёлые зависимости — только в extras: `[usd]` (usd-core),
  `[fbx]` ничего не добавляет, но требует установленный Blender.
- Экспортёры — стратегии за общим интерфейсом
  `Exporter.export(glb_bytes: bytes, out: Path) -> Path` в `core/export.py`;
  добавление формата = новый класс + регистрация в dict (паттерн как
  `EXTRACTORS` в `core/unpack.py`).
- Возможность использовать как библиотеку: `convert_format(glb, "usdz")`.

## План реализации

1. `core/export.py`: базовый класс + STL-writer (с unit-тестами на
   известном кубе: байты сверяются с эталоном).
2. USD/USDZ-экспортёр: из GLB через usd-core (`Usd.Stage`,
   `UsdGeom.Mesh`, материалы UsdPreviewSurface). GLB читать своим же
   кодом не нужно — сцена ещё в памяти `SceneBuilder`; экспортёр
   принимает структуру сцены, а не байты (уточнить сигнатуру: сначала
   `export(scene_model, out)`; GLB-байты — частный случай источника
   для unpack-пути).
3. FBX-экспортёр: генерация временного Blender-скрипта
   (импорт GLB → экспорт FBX), запуск `blender --background --python`;
   путь к Blender: `WOG_BLENDER_PATH` из конфига, иначе `shutil.which`.
4. CLI-флаги, справка, README-таблица форматов.

## Критерии приёмки

- [ ] `--format usdz` для ak47: файл открывается в iOS Quick Look
      (или macOS Quick Look) с текстурами
- [ ] `--format stl --stl-units mm --stl-per-part`: PrusaSlicer/Cura
      открывают файлы без ошибок валидации меша
- [ ] USD-файл открывается в `usdview` (usd-core) без предупреждений
- [ ] FBX-путь отрабатывает при установленном Blender и даёт понятную
      ошибку без него
- [ ] Юнит-тесты STL-writer (эталонные байты), registry экспортёров,
      парсинг `--format`
- [ ] README документирует форматы, extras и требования

## Тесты

- `tests/unit/test_export.py`: STL-байты куба/тетраэдра против эталона;
  масштабирование единиц; неизвестный формат → понятная ошибка.
- USD/USDZ: skipped-тесты при отсутствии `usd-core`, при наличии —
  раундтрип через `Usd.Stage.Open`, проверка узлов и мешей.
- FBX: тест генерации скрипта (строковая фиксация); реальный запуск —
  integration, skip без Blender.

## Риски и открытые вопросы

- `usd-core` ставится не на всех платформ одинаково хорошо (Linux/macOS
  ок, Windows — проверить колёса); поэтому USD за extra, а не в ядро.
- glTF→USD — соответствие материалов неполное (KHR_materials и т.п.);
  зафиксировать таблицу соответствий в docstring экспортёра.
- USDZ-лимит AR Quick Look по размеру: большие оружия могут не влезть —
  добавить `--compress` (сжатие текстур, уменьшение до 2048px) как
  опцию этой же задачи, если лимит превышается.
