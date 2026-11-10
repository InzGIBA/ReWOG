# T-02: Экспорт анимаций в glTF

- **Статус:** не начата
- **Приоритет:** high
- **Оценка:** L
- **Зависимости:** нет
- **Квартал roadmap:** Q1 2027

## Цель

Анимации из бандлов (AnimationClip) сейчас дампятся как JSON-метаданные
и нигде не используются. Научиться извлекать кривые анимации и писать их
в GLB как настоящие glTF-анимации: сцена с оружием, которое реально
разбирается/собирается, открывается в Blender и three.js «живой».

```bash
wog-dump build-scene ak47 --animations   # GLB + animation tracks
```

## Контекст: что уже есть в коде

- `core/unpack.py::AnimationClipExtractor` (строки ~314) дампит только
  `name/length/frame_rate/legacy` — кривые не читаются вообще.
- `core/glb.py::GLBWriter` — собственный glTF 2.0 writer: `add_node()`,
  `add_mesh()`, `add_material()`, камеры и свет. Анимаций нет:
  в `_build_document()` нет ключей `animations`/`animation.channels`/
  `animation.samplers`.
- `core/scene.py::SceneBuilder._build_game_object_tree()` строит
  `_GameObjectInfo` с иерархией и TRS — узлы сцены соответствуют
  GameObject-ам, к которым привязаны кривые Unity (bindings по path).

## Требования

### Функциональные

- Чтение кривых из AnimationClip через UnityPy: трансляции и вращения
  (TRS-треки на Transform-ах). Морф-таргеты, анимация материалов и
  кривые освещения — вне scope (явно отбрасываются с логом).
- Маппинг path binding Unity (`Parent/Child`) → индекс glTF-узла по
  имени из дерева сцены. Несопоставленные биндинги — предупреждение
  в лог + счётчик в манифесте.
- Каждый AnimationClip → отдельная glTF animation (имя = имя клипа).
  В бандле может быть несколько клипов — все.
- Конвертация времени: Unity (секунды, frame_rate) → glTF (секунды);
  интерполяция LINEAR для трансляций, LINEAR на кватернионах вращения
  (slerp выполняет потребитель формата).
- Флаг `build-scene --animations` (дефолт выключен — текущее поведение
  и размер GLB не меняются). `scene.json` получает секцию `animations`:
  имя, длительность, число треков, число пропущенных биндингов.
- Новая опция `--animation <name>`: включить в GLB только указанный клип.

### Нефункциональные

- `GLBWriter.add_animation(...)` — новый публичный метод writer-а,
  задокументированный по стилю остальных `add_*`.
- Плавность: сэмплы не перегружать — по умолчанию писать ключи как есть;
  децимация ключей (`--simplify` с допуском в секундах) — опционально.
- mypy strict, длина `core/glb.py` контролируется: кривые/сэмплы вынести
  в новый модуль `core/anim.py`, writer остаётся сериализатором.

## План реализации

1. Исследование на живых данных: `UnityPy.load(decrypted_bundle)`,
   объекты AnimationClip — какой реальный формат кривых в этих бандлах
   (legacy m_PositionCurves против clip-based m_Clip.m_Binding).
   Зафиксировать в комментарии модуля `core/anim.py`.
2. `core/anim.py`: `AnimationTrack` (node_name, property, times[], values[]),
   `extract_tracks(clip, path_to_node: dict[str, int]) -> list[AnimationTrack]`.
3. `GLBWriter.add_animation(name, tracks)`: accessors для times/values
   (min/max у accessors — обязательно, glTF-валидатор проверяет),
   channels/samplers, узел `animations` в `_build_document()`.
4. Интеграция в `SceneBuilder.build_scene()`: собрать path→node mapping
   из дерева, вызвать экстракцию при `--animations`.
5. CLI-флаги в `cli/scene.py`.

## Критерии приёмки

- [ ] `build-scene ak47 --animations` даёт GLB, который Blender 4.x
      импортирует с анимацией в списке Action-ов
- [ ] Тот же GLB валиден через официальный glTF-валидатор
      (`gltf_validator`) — 0 ошибок
- [ ] Без флага `--animations` выход не отличается от текущего
- [ ] Манифест содержит статистику по каждому клипу
- [ ] Юнит-тесты маппинга path→node на синтетическом дереве, включая
      кейс «биндинг на несуществующий узел»

## Тесты

- `tests/unit/test_anim.py`: экстракция треков из синтетического
  AnimationClip-объекта (mock UnityPy-структуры), конвертация значений.
- `tests/unit/test_glb.py`: расширить — add_animation пишет корректные
  accessors/channels; пустая анимация — ошибка с понятным сообщением.
- Integration: ak47 с `--animations` — импорт в Blender (без网络, из
  локального кэша), проверить наличие треков по именам.

## Риски и открытые вопросы

- Формат кривых в бандлах игры может быть кастомным (legacy Unity) —
  фаза исследования обязательна до дизайна API.
- Размер GLB с анимациями может вырасти заметно — упомянуть в README,
  оставить дефолт выключенным.
- Иерархические path-ы Unity с префиксами могут не совпасть с именами
  узлов сцены 1:1 — те же правила нормализации имён, что в T-01
  (модули не зависят друг от друга: вынести нормализацию в
  `core/scene.py` как общую функцию, если обе задачи в работе).
