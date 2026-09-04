# Контракты данных

Единственный источник правды о формате обмена между частями системы.
Всё в этой папке — общее. Меняется **только через тимлида**, отдельным
коммитом с сообщением в общий чат.

## Файлы

| Файл | Что это | Кто использует |
|---|---|---|
| `schemas.py` | Pydantic-модели всех объектов | бэкенд, ML |
| `mock_analyze_response.json` | Фейковый ответ `/analyze` с 11 подписками | фронтенд, бот, дизайнер |

## Как подключить

### Бэкенд

```python
from fastapi import FastAPI, UploadFile
from contracts.schemas import AnalyzeResponse, CancelInstruction

app = FastAPI()

@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze(file: UploadFile):
    ...

@app.get("/cancel/{subscription_id}", response_model=CancelInstruction)
async def cancel(subscription_id: str):
    ...
```

FastAPI сам проверит ответ на соответствие схеме и поднимет живую
документацию на `/docs` — фронтендер открывает её в браузере и видит
все поля без единого вопроса в чат.

**В первый час** сделайте заглушку, которая просто отдаёт мок:

```python
import json, pathlib

MOCK = json.loads(
    (pathlib.Path(__file__).parent.parent / "contracts" / "mock_analyze_response.json")
    .read_text(encoding="utf-8")
)

@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze(file: UploadFile):
    return MOCK          # ← настоящий пайплайн подключится на второй день
```

С этого момента фронтенд разблокирован и больше никого не ждёт.

### Фронтенд

День 1 — работаем полностью на моке, бэкенд не нужен:

```js
import mock from "../../contracts/mock_analyze_response.json";
const data = mock;
```

День 2 — меняется одна строка:

```js
const data = await (await fetch("/analyze", { method: "POST", body: form })).json();
```

Вёрстка не переделывается, потому что форма данных та же.

### Промпт-инженер

Кусок схемы копируется прямо в системный промпт. Например, для промпта
поиска пересечений:

```
Верни ТОЛЬКО JSON-массив, без markdown-обёртки и без пояснений.
Каждый элемент:
{
  "id": string,
  "category": "video"|"music"|"cloud"|"fitness"|"education"|"books"|
              "games"|"delivery"|"transport"|"ai_tools"|"bank_premium"|
              "telecom"|"other",
  "subscription_ids": string[],
  "keep_suggestion": string|null,
  "savings_yearly": number,
  "explanation": string
}
```

Ответ модели прогоняется через `OverlapGroup.model_validate_json()`.
Не прошло — один ретрай, потом фолбэк на детерминированную группировку
по `category`.

### Аналитики

Ground truth для замера точности размечается в том же формате: список
объектов `Subscription` для каждой демо-выписки. Тогда метрика считается
сравнением двух списков по полю `name`, без ручной сверки.

## Правила

1. Новое поле добавлять можно в любой момент — это никого не ломает.
2. Переименовать или удалить поле — только с уведомлением всей команды.
3. Любое изменение `schemas.py` тянет за собой обновление мока. Проверка:

```bash
python -c "import json; from contracts.schemas import AnalyzeResponse; \
AnalyzeResponse.model_validate(json.load(open('contracts/mock_analyze_response.json'))); \
print('мок валиден')"
```

Повесьте эту команду в pre-commit или просто прогоняйте перед пушем.

## Ключевое поле для демо

`summary.potential_savings_yearly` — это цифра, которая идёт крупно на
первый экран и в питч. В моке 11 964 ₽. Именно её жюри запомнит.
