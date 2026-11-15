# ExoCortex Frontend

## Локальный запуск

Понадобятся Node.js 24 LTS, npm и установленное Python-окружение проекта.
Команды ниже рассчитаны на PowerShell.

В первом терминале из корня репозитория запусти FastAPI.

```powershell
.\.venv\Scripts\python.exe -m uvicorn src.main:app --host 127.0.0.1 --port 8000
```

Во втором терминале из корня репозитория запусти Vite.

```powershell
cd frontend
npm.cmd ci
npm.cmd run dev
```

Открой [ExoCortex](http://127.0.0.1:5173/) и нажми **Проверить API чатов**.
Vite проксирует путь `/api/v1/chats` на FastAPI. Если чатов ещё нет, сервер
вернёт `[]`. Для этой проверки ключ Gemini не нужен.

## Проверка сборки

Из папки `frontend` выполни:

```powershell
npm.cmd run build
```

Команда проверяет типы TypeScript и собирает статические файлы в `dist/`.
Прокси Vite предназначен для локальной разработки.
