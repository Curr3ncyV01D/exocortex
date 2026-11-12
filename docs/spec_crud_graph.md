# Спецификация графового ядра: `src/storage/crud/graph.py`

**Документ:** Архитектурная документация слоя манипуляции направленным ациклическим графом (DAG)  
**Модуль:** `src.storage.crud/graph`  
**Назначение:** Реализация базовых и графовых CRUD-операций, алгоритмов обхода дерева контекста, версионирования веток и инкрементального слияния.  
**Стек:** Python 3.11+, SQLAlchemy 2.0 Async, aiosqlite.

---

## 1. Архитектурное назначение и границы модуля

Модуль `crud/graph.py` является изолированным вычислительным ядром слоя хранения (`Storage Layer`). 
Он инкапсулирует всю бизнес-логику работы с реляционным представлением графа диалога, исключая утечку низкоуровневых SQL-запросов в сервисный слой API и слой ИИ-оркестратора.

### Базовые принципы реализации:
1. **Транзакционная атомарность:** Все мутирующие операции выполняются в рамках единой асинхронной транзакции базы данных с автоматическим откатом (`rollback`) при возникновении исключений.
2. **Отсутствие внешних сетевых зависимостей:** Модуль оперирует исключительно локальной базой данных SQLite и абстракцией сессии `AsyncSession`. Интеграция с внешними LLM-моделями инвертирована через внедрение зависимостей (Dependency Injection: передача вызываемого объекта `summarizer_func`).
3. **Строгая типизация:** Полное покрытие сигнатур функций аннотациями типов Python (`typing`) и проверка валидности внешних идентификаторов (UUID).

---

## 2. Иерархия доменных исключений

Для детерминированной обработки ошибок на уровне транспортного слоя FastAPI в модуле реализована специализированная иерархия исключений:

```mermaid
classDiagram
    class Exception
    class GraphEngineError {
        <<Base Exception>>
    }
    class BranchNotFoundError {
        +branch_name: str
    }
    class BranchAlreadyExistsError {
        +branch_name: str
    }
    class MessageNotFoundError {
        +message_id: str
    }
    class NoActiveMessagesToMergeError

    Exception <|-- GraphEngineError
    GraphEngineError <|-- BranchNotFoundError
    GraphEngineError <|-- BranchAlreadyExistsError
    GraphEngineError <|-- MessageNotFoundError
    GraphEngineError <|-- NoActiveMessagesToMergeError
```

* **`GraphEngineError`**: Корневое исключение подсистемы графа.
* **`BranchNotFoundError`**: Выбрасывается при попытке обращения к несуществующей ветке в рамках `chat_id` (транслируется в HTTP `404 Not Found`).
* **`BranchAlreadyExistsError`**: Защита от коллизий уникального индекса `(chat_id, name)` (транслируется в HTTP `409 Conflict`).
* **`MessageNotFoundError`**: Выбрасывается при указании невалидного `target_message_id` или `message_id` (транслируется в HTTP `404 Not Found`).
* **`NoActiveMessagesToMergeError`**: Предохранитель от пустого слияния, если все реплики дельты маскированы или отсутствуют (транслируется в HTTP `400 Bad Request`).

---

## 3. Детальная спецификация алгоритмов ядра

### 3.1. Реконструкция линейного контекста (`reconstruct_context`)
Функция выполняет рекурсивный трейсбек от актуальной вершины ветки к историческому корню с фильтрацией маскированных узлов и санитизацией смежных ролей.

```mermaid
flowchart TD
    Start(["Старт: reconstruct_context(chat_id, branch_name)"]) --> FetchBranch["Поиск ветки по (chat_id, branch_name)"]
    FetchBranch --> CheckBranch{"Ветка найдена?"}
    CheckBranch -- Нет --> ErrBranch["raise BranchNotFoundError"]
    CheckBranch -- Да --> InitPointer["curr_id = branch.head_message_id\nactive_messages = []"]

    InitPointer --> LoopCheck{"curr_id is not None?"}
    LoopCheck -- Да --> FetchMsg["msg = session.get(Message, curr_id)"]
    FetchMsg --> CheckActive{"msg.is_active == True?"}
    CheckActive -- Да --> AppendMsg["active_messages.append(msg)"]
    CheckActive -- Нет --> NextStep["Пропуск узла (маскирован)"]
    AppendMsg --> NextStep
    NextStep --> StepParent["curr_id = msg.parent_id"]
    StepParent --> LoopCheck

    LoopCheck -- Нет --> ReverseList["timeline = reversed(active_messages)"]
    ReverseList --> Sanitize["Санитизация: склеивание смежных MODEL -> MODEL"]
    Sanitize --> ReturnList(["Возврат sanitized_timeline"])
```

* **Вход:** `chat_id: str`, `branch_name: str`, `session: AsyncSession`.
* **Выход:** `list[Message]` — хронологический массив сущностей `[Root -> ... -> Head]`.
* **Инвариант санитизации:** Если вследствие установки флагов `is_active = False` на репликах пользователя в результирующей выборке два сообщения модели оказываются смежными, их контент объединяется в первый узел через двойной перенос строки `\n\n`. Это предотвращает отказ внешнего API Gemini (`400 Invalid Argument: model turns must alternate`).

---

### 3.2. Атомарное ветвление (`branch_from_here`)
Создает новый независимый именованный указатель ветки, ссылающийся на произвольный узел в существующем графе диалога.

```mermaid
flowchart LR
    TargetNode[("Узел target_message_id")]
    
    subgraph NewBranch ["Новая ветка: Branch"]
        head["head_message_id"]
        fork["forked_from_id"]
    end

    head -->|Указывает| TargetNode
    fork -->|Фиксирует развилку| TargetNode
```

* **Вход:** `chat_id: str`, `target_message_id: str`, `new_branch_name: str`, `session: AsyncSession`.
* **Выход:** Экземпляр созданной сущности `Branch`.
* **Логика:**
  1. Проверяет существование узла `target_message_id` строго внутри пространства `chat_id`.
  2. Проверяет уникальность имени ветки через предикат `WHERE chat_id = :chat_id AND name = :new_branch_name`.
  3. Создает объект `Branch` с полями `head_message_id = target_message_id` и `forked_from_id = target_message_id`, фиксируя точку бифуркации.

---

### 3.3. In-Place редактирование контента (`update_message_content`)
Обеспечивает мутацию содержимого реплики с защитой от непреднамеренной десинхронизации истории в разветвленных узлах графа.

* **Вход:** `message_id: str`, `new_content: str`, `force: bool = False`, `session: AsyncSession`.
* **Выход:** Словарь статуса операции (`{"status": "success" | "warning_required", ...}`).
* **Алгоритм защиты предков (Ancestor Protection):**
  * Вычисляется количество прямых дочерних реплик узла: `SELECT COUNT(id) FROM messages WHERE parent_id = :message_id`.
  * Если `children_count > 1` и флаг `force == False`, мутация блокируется, и возвращается статус `warning_required` с указанием степени разветвления.
  * При `force == True` (или `children_count <= 1`) выполняется обновление поля `content`, выставляется флаг `is_edited = True`, а метка `updated_at` принимает значение `datetime.now(timezone.utc)`.

---

### 3.4. Контекстное маскирование (`toggle_message_active`)
Инвертирует видимость реплики для алгоритма реконструкции контекста без нарушения ссылочной целостности графа связей `parent_id`.

* **Вход:** `message_id: str`, `session: AsyncSession`.
* **Выход:** Новое булево состояние флага `is_active`.
* **Инвариант контента:** Операция **не мутирует** поле `updated_at`, так как текстовое тело сообщения (`content`) остается неизменным.

---

### 3.5. Инкрементальное сквош-слияние (`squash_merge_branch`)
Реализует семантическую компрессию побочной линии рассуждений и ее интеграцию в целевую ветку в виде полиморфного узла `MergeEvent` (роль `SYSTEM_MERGE`).

```mermaid
sequenceDiagram
    autonumber
    participant Caller as Сервисный слой / API
    participant Graph as crud/graph.py
    participant Summarizer as Внешний суммаризатор (Callable)
    participant DB as SQLite Transaction

    Caller->>Graph: squash_merge_branch(chat_id, source, target, summarizer)
    Graph->>DB: Выборка source_branch и target_branch
    
    Note over Graph: Определение точки отсчета дельты:<br/>start_node = last_merged_message_id ?? forked_from_id
    
    Graph->>DB: Сбор цепочки узлов от source.head до start_node
    DB-->>Graph: Список сообщений дельты
    
    Note over Graph: Фильтрация: active_delta = [m for m in delta if m.is_active]
    
    Graph->>Summarizer: summarizer_func(active_delta_ordered)
    Summarizer-->>Graph: Текст семантического сжатия (summary_text)

    rect rgb(20, 30, 40)
    Note over Graph,DB: Атомарная фиксация транзакции
    Graph->>DB: INSERT MergeEvent (role=SYSTEM_MERGE, parent_id=target.head)
    Graph->>DB: UPDATE target.head_message_id = MergeEvent.id
    Graph->>DB: UPDATE source.last_merged_message_id = source.head_message_id
    Graph->>DB: COMMIT
    end

    Graph-->>Caller: Dict (status, merge_message_id, summary)
```

* **Определение границы дельты:**
  $$\text{start\_node} = \begin{cases} \text{source.last\_merged\_message\_id}, & \text{если существует} \\ \text{source.forked\_from\_id}, & \text{иначе} \end{cases}$$
* **Защита от пустых слияний:** Если после фильтрации `is_active == True` дельта пуста, выбрасывается `NoActiveMessagesToMergeError`.
* **Поддержка непрерывности:** Побочная ветка сохраняет статус `ACTIVE`, а указатель `last_merged_message_id` сдвигается на текущий `head_message_id`, позволяя производить последующие инкрементальные слияния.

---

## 4. Временная сложность и метрики производительности

| Алгоритм | Временная сложность | Дисковые операции (I/O) | Индексы БД |
| :--- | :--- | :--- | :--- |
| `reconstruct_context` | $O(K)$, где $K$ — глубина ветки | $K$ точечных чтений по PK | `PRIMARY KEY (id)`, `idx_messages_parent_id` |
| `branch_from_here` | $O(1)$ | 1 чтение, 1 запись | `uq_chat_branch_name`, `PRIMARY KEY (id)` |
| `update_message_content`| $O(1)$ | 1 чтение, 1 агрегация, 1 запись | `idx_messages_parent_id` |
| `toggle_message_active` | $O(1)$ | 1 чтение, 1 запись | `PRIMARY KEY (id)` |
| `squash_merge_branch` | $O(\Delta) + O(\text{LLM})$, где $\Delta$ — длина дельты | $\Delta$ чтений, 2 записи | `PRIMARY KEY (id)`, `idx_messages_parent_id` |

*Все операции чтения выполняются в пределах $0.5–2.0$ мс на локальной базе данных SQLite в режиме WAL.*

---

## 5. Инварианты тестирования (TDD Verification)

Корректность алгоритмов ядра математически зафиксирована набором модульных тестов в `tests/test_graph.py` (9/9 passed):
1. **`test_reconstruct_context_linear_order`**: Гарантия хронологической сборки цепочки реплик.
2. **`test_reconstruct_context_role_sanitization`**: Корректность объединения смежных ходов модели.
3. **`test_branch_from_here_success`**: Проверка инициализации указателей ветки.
4. **`test_branch_from_here_duplicate_name_error`**: Срабатывание уникального ограничения на уровне чата.
5. **`test_update_message_content_ancestor_warning`**: Блокировка неявной мутации общего предка.
6. **`test_update_message_content_force_success`**: Принудительная мутация с фиксацией `updated_at`.
7. **`test_toggle_message_active_invariants`**: Инвариант неизменности времени правки при тумблировании.
8. **`test_squash_merge_branch_incremental`**: Создание полиморфной записи `MergeEvent` и смещение `last_merged_message_id`.
9. **`test_squash_merge_empty_delta_fails`**: Валидация наличия активных сообщений при сжатии.