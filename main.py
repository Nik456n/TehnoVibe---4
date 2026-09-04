import json
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

# Наш сервер-ресторан
app = FastAPI(title="Подписки-Сканер", version="1.0")

# Путь к папке contracts латиницей, как просил тимлид
MOCK_FILE = Path(__file__).parent / "contracts" / "mock_analyze_response.json"


@app.post("/analyze")
async def analyze():
    try:
        with open(MOCK_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return JSONResponse(content=data)
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="Файл с ответом не найден!")


@app.get("/cancel/{subscription_id}")
async def cancel_subscription(subscription_id: str):
    return {"status": "успех", "message": f"Отмена подписки {subscription_id}"}
