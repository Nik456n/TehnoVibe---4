import json
import re
import base64
from pathlib import Path
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

# Для GigaChat
from gigachat import GigaChat

# Инициализация приложения FastAPI
app = FastAPI(title="Подписки-Сканер", version="1.0")

# Путь к мок-файлу (оставлен для старого эндпоинта /analyze)
MOCK_FILE = Path(__file__).parent / "contracts" / "mock_analyze_response.json"

# ============================================
# НАСТРОЙКА GigaChat
# ============================================
# Вставьте сюда ваш реальный API-ключ GigaChat
GIGACHAT_API_KEY = "ВАШ_КЛЮЧ_СЮДА"

# Промпт для анализа выписки
ANALYSIS_PROMPT = """
Ты — финансовый аналитик. Проанализируй предоставленную банковскую выписку.
Найди все регулярные списания, похожие на подписки (стриминги, софт, сервисы).
Для каждой подписки укажи:
- name (название или получатель платежа),
- amount (сумма, если указана),
- period (периодичность: ежемесячно, ежегодно и т.п.).
Верни ответ строго в формате JSON:
{
  "subscriptions": [
    {"name": "Яндекс Плюс", "amount": 299.0, "period": "ежемесячно"}
  ],
  "total_monthly_savings": 299.0
}
Если подписок не найдено, верни {"subscriptions": [], "total_monthly_savings": 0}.
Не добавляй ничего лишнего, только JSON.
"""


# Обработка POST-запроса для выдачи мок-данных (старый эндпоинт)
@app.post("/analyze")
async def analyze():
    try:
        with open(MOCK_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return JSONResponse(content=data)
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="Файл с ответом не найден!")


# Обработка GET-запроса для имитации отмены подписки
@app.get("/cancel/{subscription_id}")
async def cancel_subscription(subscription_id: str):
    return {"status": "успех", "message": f"Отмена подписки {subscription_id}"}


# Обработка POST-запроса для загрузки файла пользователем (просто метаданные)
@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    content = await file.read()
    return {
        "filename": file.filename,
        "content_type": file.content_type,
        "size_bytes": len(content),
    }


# ============================================
# НОВЫЙ ЭНДПОИНТ: анализ через GigaChat (мультимодальный)
# ============================================
@app.post("/analyze-ai")
async def analyze_with_ai(file: UploadFile = File(...)):
    # Читаем содержимое файла
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Файл пуст")

    ext = Path(file.filename).suffix.lower()
    mime = file.content_type

    # Инициализируем клиент GigaChat
    client = GigaChat(credentials=GIGACHAT_API_KEY, verify_ssl_certs=False)

    # Подготовка сообщений в зависимости от типа файла
    messages = []

    if ext in [".csv", ".txt"]:
        # Текстовый файл – отправляем как текст
        text = content.decode("utf-8", errors="ignore")
        messages.append(
            {
                "role": "user",
                "content": f"{ANALYSIS_PROMPT}\n\nСодержимое файла:\n{text}",
            }
        )
    elif ext in [".png", ".jpg", ".jpeg"]:
        # Изображение – отправляем как base64
        base64_image = base64.b64encode(content).decode("utf-8")
        # Формируем data URL
        image_url = f"data:{mime};base64,{base64_image}"
        messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": ANALYSIS_PROMPT},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }
        )
    else:
        # PDF или другие форматы пока не поддерживаем
        raise HTTPException(
            status_code=400,
            detail=f"Формат {ext} не поддерживается. Загрузите CSV, TXT или изображение.",
        )

    # Отправляем запрос к GigaChat
    try:
        response = client.chat(messages=messages)
        answer_text = response.choices[0].message.content
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка GigaChat: {str(e)}")

    # Пытаемся извлечь JSON из ответа
    try:
        result = json.loads(answer_text)
    except json.JSONDecodeError:
        # Если модель вернула текст с JSON внутри, ищем его регуляркой
        json_match = re.search(r"\{.*\}", answer_text, re.DOTALL)
        if json_match:
            try:
                result = json.loads(json_match.group())
            except Exception:
                raise HTTPException(
                    status_code=500,
                    detail="Не удалось распарсить JSON из ответа модели",
                )
        else:
            raise HTTPException(
                status_code=500, detail="Модель вернула некорректный ответ"
            )

    return result
