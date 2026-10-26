# Помощник и возобновление

В переносимой поставке код лежит в `scripts/powermill_com_helper` в корне
навыка. Используй именно эту копию; файлы рабочих запусков не входят в навык.
Нужны Windows, открытый PowerMill 2023, Python 3.12+, Windows PowerShell 5.
Сторонние Python-пакеты помощнику не нужны. Посредник скрытый, STA/GetActiveObject.
Для охраняемых операций проверены версии COM 2023000, 2023003 и 2023101. Версию
снимай с текущего сеанса, а совместимость других версий проверяй отдельно.
Охват проверки 2023101: [compatibility.md](compatibility.md).

Выполняй из `scripts` поставки либо корня репозитория:

```text
python -m powermill_com_helper session --connect --output session.json
python -m powermill_com_helper capture-surface session.json PATH_NAME profile.json --connect --geometry
python -m powermill_com_helper plan-surface surface-request.json NEW_BUNDLE
python -m powermill_com_helper plan-sort sort-input.json NEW_BUNDLE
python -m powermill_com_helper validate NEW_BUNDLE/plan.json
python -m powermill_com_helper status RUN_DIR
```

session только читает. JSON с version/process_id/model_ids/project_path подходит
как guard; проверь, что проект соответствует заданию. Импорт модулей,
plan/validate/status не подключаются к PowerMill.

Планировщики по умолчанию создают offline_only=true. Для разрешённого живого
задания сформируй новую сборку с --enable-execution, проверь входы, затем:

```text
python -m powermill_com_helper run NEW_BUNDLE/plan.json RUN_DIR --connect --allow-write
python -m powermill_com_helper reconcile NEW_BUNDLE/plan.json RUN_DIR --connect
```

Флаги не заменяют разрешение пользователя; уже полученное разрешение
на данную задачу из-за наличия этих флагов повторно не запрашивается.
state.json пишется до команды. После обрыва операция uncertain, даже если
PowerMill мог её закончить. Завершённые блоки не переигрываются. Для признания
завершения нужны постусловия и receipt после нативного этапа. Новая копия
поверхности получает файл со своим ID: одинакового имени недостаточно.
Не удаляй checkpoint/receipt ради обхода блокировки, не редактируй действующий
план. При смене PID/проекта или повреждении состояния сначала сверяй данные.

Для частичной сортировки:

```text
python -m powermill_com_helper capture-sort SORT_INPUT observed.json --connect
python -m powermill_com_helper replan-sort SORT_INPUT observed.json NEW_BUNDLE
```

Каталог идентифицирует все головы проходов по XYZ/слой/LP; неоднозначные головы
отвергаются. Режим `identity_mode=geometry` сверяет только XYZ и не читает PP:
используй его лишь при уникальных головах и отдельно проверяй ПП. Оси/другие
PP голов проверяются, если включены в каталог и режим их читает.
Цель по слоям/ролям/парам задаётся явно для текущего задания.
Это не полный аудит всех точек и переходов.

Ошибка Windows 1385 одного механизма не доказывает недоступность остальных:
различай запуск процесса, UI-автоматизацию и COM. Не меняй права Windows;
используй доступный прямой COM с внешним тайм-аутом.

Офлайн-проверка:

```text
python -m unittest discover -s powermill_com_helper/tests -t . -v
```

Тест PS5 использует подставное приложение и запрещает GetActiveObject.
Первое нативное испытание нового генератора — на небольшой копии в пределах
разрешённого задания. Успех тестов Python не подтверждает нативный синтаксис.
