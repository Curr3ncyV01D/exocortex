# Как импортировать диалог из Google AI Studio

Помести экспорт Prompt Data JSON в `data/raw/`. Без `--file` скрипт выбирает самый свежий файл в этом каталоге. Сначала проверь его в режиме `--dry-run`. В отчёте будут число реплик, пропущенные размышления и диапазон дат. В этом режиме база и бэкап не создаются.

## Через Docker Compose

Выполняй команды из корня репозитория. Если база создана до Alembic, [подключи её к миграциям](run-with-docker.md#подключение-существующей-базы).

```sh
docker compose build backend
docker compose run --rm --no-deps backend python -m scripts.migrate_ai_studio --dry-run
```

Для записи останови приложение, чтобы освободить SQLite, затем выполни импорт и запусти Compose.

```sh
docker compose stop
docker compose run --rm --no-deps backend python -m scripts.migrate_ai_studio --title "Архив AI Studio"
docker compose up
```

Если в каталоге несколько экспортов, укажи файл явно, например `--file /app/data/raw/export.json`.

## Из локального Python-окружения

Из корня проекта в PowerShell проверь файл и выполни импорт. Перед второй командой останови FastAPI, если он работает.

```powershell
.\.venv\Scripts\python.exe -m scripts.migrate_ai_studio --file ".\data\raw\export.json" --dry-run
.\.venv\Scripts\python.exe -m scripts.migrate_ai_studio --file ".\data\raw\export.json" --title "Архив AI Studio"
```

Каждый успешный импорт создаёт новый чат с веткой `main`. Без `--title` используется имя файла. Скрипт сохраняет порядок реплик, заменяет вложения текстовыми отметками и печатает ID чата. Для существующей базы он предварительно создаёт резервную копию и выводит её путь.
