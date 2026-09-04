import json
from pathlib import Path
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

# Инициализация приложения FastAPI
app = FastAPI(title="Подписки-Сканер", version="1.0")

# Формирование абсолютного пути к JSON-файлу с мок-данными
MOCK_FILE = Path(__file__).parent / "contracts" / "mock_analyze_response.json"


# Обработка POST-запроса для выдачи мок-данных
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


# Обработка POST-запроса для загрузки файла пользователем
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
