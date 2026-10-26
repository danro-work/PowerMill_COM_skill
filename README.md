# PowerMill COM skill for Codex

Переносимый навык **powermill-com 0.2.3**, COM-помощник **0.2.2** для Autodesk
PowerMill 2023. Прямой COM к уже запущенному приложению; PowerMill MCP не нужен.
Поддерживаемые COM-версии: **2023000, 2023003, 2023101**.

## Установка

Скопируйте папку `skill/powermill-com` **целиком** в один из каталогов:

- `%USERPROFILE%\.codex\skills\powermill-com` — для всех проектов;
- `<project>\.agents\skills\powermill-com` — для одного проекта.

При другом `CODEX_HOME` используйте его каталог `skills`. Обновите список
навыков или откройте новый чат и вызовите `$powermill-com`. Прочитайте
[SKILL.md](skill/powermill-com/SKILL.md) и [INSTALL.txt](skill/powermill-com/INSTALL.txt).

Нужны Windows, Python 3.12+, Windows PowerShell 5, PowerMill 2023.
Сторонние Python-пакеты и ключи помощнику не требуются.

```powershell
Set-Location skill/powermill-com/scripts
python -m powermill_com_helper --help
python -m unittest discover -s powermill_com_helper/tests -t . -v
```

Команды планирования и проверки работают офлайн. `session --connect` читает
открытый сеанс; живое выполнение требует текущего поручения пользователя,
`--connect`, `--allow-write` и плана с проверками. Установка навыка не разрешает
менять траектории или запускать симуляцию.

## Изменения 0.2.3

- Добавлена COM-версия 2023101 в Python и PowerShell без ослабления остальных проверок.
- Добавлены тесты допуска версий, запрета неизвестных версий, совпадения сеанса,
  офлайн-ограничений и возобновления без повторной отправки завершённой команды.
- Описаны [охват нативной проверки и перенос копии](skill/powermill-com/references/compatibility.md).

`PACKAGE_MANIFEST.json` содержит SHA-256 файлов репозитория, кроме самого
манифеста. Внутри навыка находится отдельный `MANIFEST.json`.
Архивы релизов содержат те же файлы. Правила конкретного производственного
проекта, геометрия, макросы заказов, live guard, PID, model ID, пути проектов,
журналы и checkpoint в поставку не входят.

Нативная проверка 2023101 охватывает чтение, копирование, переименование и
перенос копии; новые поверхностные расчёты и роботная симуляция ею не подтверждены.
