# Как получить живой ответ Gemini через Swagger

Эта проверка подтверждает, что локальный REST API может отправить сохранённый вопрос в Gemini и вернуть текстовый ответ. Проверки Function Calling и сквош-слияния в этот сценарий не входят.

## Настрой ключ и запусти сервер

1. Создай или скопируй ключ на странице [Google AI Studio → API Keys](https://aistudio.google.com/apikey).
2. Создай `.env` рядом с `requirements.txt` на основе [`.env.example`](../../.env.example). Добавь свой ключ. Файл `.env` игнорируется Git, а Swagger получает доступ к Gemini через сервер.

   ```dotenv
   GEMINI_API_KEY=your_key_here
   ```

3. Если виртуального окружения ещё нет, создай его и установи зависимости из корня проекта в PowerShell.

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

4. Запусти сервер. При старте он инициализирует SQLite. Перезапускай сервер после изменения `.env`.

   ```powershell
   .\.venv\Scripts\python.exe -m uvicorn src.main:app --host 127.0.0.1 --port 8000
   ```

## Выполни запросы

Открой [Swagger UI](http://127.0.0.1:8000/docs). У каждого маршрута нажимай **Try it out**, заполняй параметры и нажимай **Execute**.

| Шаг | Маршрут | Ввод и результат |
| --- | --- | --- |
| 1 | `POST /api/v1/chats` | Отправь `{"title":"Проверка Gemini"}`. Из ответа `201` возьми `id` чата. |
| 2 | `GET /api/v1/chats/{chat_id}/branches` | Подставь ID чата. Из списка возьми `id` ветки `main`. |
| 3 | `POST /api/v1/branches/{branch_id}/messages` | Подставь ID ветки и отправь `{"content":"Объясни в двух предложениях, зачем Python нужно виртуальное окружение."}`. Из ответа `201` возьми `id` сообщения. |
| 4 | `POST /api/v1/branches/{branch_id}/run` | Подставь тот же ID ветки и отправь `{"expected_head_id":"ID сообщения из шага 3"}`, заменив строку на фактический ID. |

Ожидаемый результат — `200 OK`, `role: "model"` и текст в `content`. Поле `parent_id` совпадает с ID вопроса, а `metadata.execution_trace` содержит статистику хода. При прямом ответе список `steps` пуст. Чтобы проверить сохранённую историю, открой `GET /api/v1/branches/{branch_id}/timeline`.

Если `/run` вернул `502`, проверь ключ, доступ к настроенной модели и квоту проекта Gemini. После устранения причины повтори шаг 4 с тем же ID, пока вопрос остаётся вершиной ветки. При `409` заново проверь текущую вершину и убедись, что это вопрос, на который нужен ответ. [Настройка ключей Gemini](https://ai.google.dev/gemini-api/docs/api-key), [справочник REST API](../SPEC.md#5-контракты-api-и-pydantic-схемы-rest-interface)
