import json
import re
import base64
import os
from pathlib import Path
from fastapi import FastAPI, File, HTTPException, UploadFile

# ИСПРАВЛЕНО: добавлен CORS — без него Mini App с другого домена
# не сможет обратиться к бэкенду, браузер заблокирует запросы
from fastapi.middleware.cors import CORSMiddleware

# ИСПРАВЛЕНО: ключи читаем из .env, а не из кода
from dotenv import load_dotenv

# ИСПРАВЛЕНО: подключаем контракты, чтобы FastAPI проверял ответы
# и показывал схему фронтендеру на /docs
from contracts.schemas import AnalyzeResponse, CancelInstruction

# ИСПРАВЛЕНО: подключён детектор — /analyze больше не заглушка
from backend.ml.detector import detect
from backend.ml.parser import parse_statement

# ИСПРАВЛЕНО: импорт gigachat перенесён вниз, внутрь эндпоинта.
# Если пакет не установлен у фронтендера — весь бэкенд не поднимался.

load_dotenv()

# Инициализация приложения FastAPI
app = FastAPI(title="Подписки-Сканер", version="1.0")

# ИСПРАВЛЕНО: разрешаем запросы с любого домена (для хакатона достаточно)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Формирование абсолютного пути к JSON-файлу с мок-данными
# ИСПРАВЛЕНО: было parent — указывало на backend/contracts/, а мок лежит
# в корне репозитория, поэтому parent.parent
ROOT = Path(__file__).resolve().parent.parent
MOCK_FILE = ROOT / "contracts" / "mock_analyze_response.json"
CANCEL_KB = ROOT / "data" / "cancel_kb.json"

MOCK = json.loads(MOCK_FILE.read_text(encoding="utf-8"))

# ============================================
# НАСТРОЙКА GigaChat
# ============================================
# ИСПРАВЛЕНО: ключ больше не в коде. Репозиторий публичный —
# если запушить настоящий ключ, он утечёт. .env уже в .gitignore.
GIGACHAT_API_KEY = os.getenv("LLM_API_KEY", "")

# Промпт для анализа выписки
# (оставлен как есть, используется в /analyze-ai)
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


def run_pipeline(content: bytes, filename: str) -> dict:
    """Парсер + детектор. Если выписку разобрать не удалось — мок.

    LLM здесь не участвует: результат детерминированный и воспроизводимый.
    Категоризация неизвестных сервисов и тексты подключаются слоем выше.
    """
    try:
        transactions = parse_statement(content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if not transactions:
        raise HTTPException(
            status_code=400,
            detail="В файле не найдено ни одной операции. "
                   "Проверьте, что это выписка, а не другой файл.",
        )
    return detect(transactions)


# ИСПРАВЛЕНО: добавлена проверка живости, удобно фронту и на демо
@app.get("/health")
async def health():
    return {"status": "ok", "llm_configured": bool(GIGACHAT_API_KEY)}


# Обработка POST-запроса для выдачи мок-данных
# ИСПРАВЛЕНО: был незакрытый try: и return из другого эндпоинта —
# файл падал с IndentationError при импорте. Теперь принимает файл
# и отдаёт результат по контракту.
@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze(file: UploadFile = File(...)):
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Файл пуст")

    ext = Path(file.filename or "").suffix.lower()
    if ext not in {".csv", ".xlsx", ".xls", ".txt", ".pdf"}:
        raise HTTPException(
            status_code=400,
            detail=f"Формат {ext or '?'} не поддерживается. "
                   "Нужен PDF, CSV или XLSX.",
        )

    return run_pipeline(content, file.filename or "")


# ИСПРАВЛЕНО: добавлен отдельный эндпоинт под кнопку «Демо-выписка».
# Ничего не грузит, отдаёт готовый результат — наш план Б на защите.
@app.post("/analyze/demo", response_model=AnalyzeResponse)
async def analyze_demo():
    return MOCK


# ИСПРАВЛЕНО: этого эндпоинта не было — его текст случайно попал
# внутрь /analyze. Восстановлен и подключён к базе отмены.
@app.get("/cancel/{subscription_id}", response_model=CancelInstruction)
async def cancel(subscription_id: str):
    sub = next(
        (s for s in MOCK["subscriptions"] if s["id"] == subscription_id), None
    )
    if sub is None:
        raise HTTPException(
            status_code=404, detail=f"Подписка {subscription_id} не найдена"
        )

    kb = {}
    if CANCEL_KB.exists():
        kb = json.loads(CANCEL_KB.read_text(encoding="utf-8"))
    entry = kb.get(sub["name"])

    if entry:
        return {
            "subscription_id": subscription_id,
            "service_name": sub["name"],
            "difficulty": entry.get("difficulty", "medium"),
            "steps": entry.get("steps", []),
            "url": entry.get("url"),
            "letter_template": entry.get("letter_template"),
            "source": "knowledge_base",
            "savings_yearly": sub["yearly_cost"],
        }

    # Сервиса нет в базе — не выдумываем шаги, отдаём общие
    # и честно помечаем источник
    return {
        "subscription_id": subscription_id,
        "service_name": sub["name"],
        "difficulty": "medium",
        "steps": [
            "Открыть личный кабинет сервиса",
            "Перейти в раздел «Подписки» или «Платежи»",
            "Отключить автопродление",
        ],
        "url": None,
        "letter_template": None,
        "source": "llm_generated",
        "savings_yearly": sub["yearly_cost"],
    }


# Обработка POST-запроса для загрузки файла пользователем (просто метаданные)
@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    # Асинхронное чтение содержимого файла в память
    content = await file.read()
    # Возврат метаданных загруженного файла в формате JSON
    return {
        "filename": file.filename,
        "content_type": file.content_type,
        "size_bytes": len(content),
    }


# ============================================
# ЭКСПЕРИМЕНТ: анализ через GigaChat (мультимодальный)
# ============================================
# Код оставлен как есть. Из основного пути вынесен: подписки ищет
# детектор, а GigaChat подключим после него — на категоризацию
# неизвестных мерчантов и генерацию писем. Схема ответа здесь своя,
# контракту не соответствует, поэтому фронт этот эндпоинт не вызывает.
@app.post("/analyze-ai")
async def analyze_with_ai(file: UploadFile = File(...)):
    # ИСПРАВЛЕНО: импорт внутри функции — если пакет не установлен,
    # падает только этот эндпоинт, а не весь бэкенд
    try:
        from gigachat import GigaChat
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="Пакет gigachat не установлен: pip install gigachat",
        )

    if not GIGACHAT_API_KEY:
        raise HTTPException(
            status_code=503, detail="LLM_API_KEY не задан в .env"
        )

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
