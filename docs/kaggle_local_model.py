# Локальная модель на Kaggle — что запускать в ноутбуке
#
# Цель: показать, что тот же пайплайн работает на локальной модели,
# без облака. Для защиты достаточно одного прогона и записи экрана.
#
# Kaggle → Notebook → Settings → Accelerator: GPU T4 x2, Internet: On
# Секрет HF_TOKEN добавляется в Add-ons → Secrets (модель gated).

# ── ячейка 1: окружение ────────────────────────────────────────────
# transformers >= 5.5.0 обязательно: тип "gemma4" появился там.
# На 5.0.0 ошибка выглядит так, будто версия слишком новая — это ловушка.
"""
!pip install -q "transformers>=5.5.0" bitsandbytes accelerate
!git clone -q https://github.com/Nik456n/TehnoVibe---4.git
%cd TehnoVibe---4
"""

# ── ячейка 2: токен ────────────────────────────────────────────────
"""
import os
from kaggle_secrets import UserSecretsClient
os.environ["HF_TOKEN"] = UserSecretsClient().get_secret("HF_TOKEN")
os.environ["LLM_PROVIDER"] = "local"
"""

# ── ячейка 3: прогон пайплайна ─────────────────────────────────────
# Весь код проекта работает без изменений: провайдер выбирается
# переменной LLM_PROVIDER, остальной слой этого не замечает.
"""
import sys; sys.path.insert(0, ".")
from backend.ml.parser import parse_statement
from backend.ml.detector import detect
from backend.ml import llm

result = detect(parse_statement(open("data/demo_statement_clean.csv", "rb").read()))
result = llm.enrich(result)

print("модель отработала:", result["llm_used"])
print("подписок:", len(result["subscriptions"]))
print("экономия:", result["summary"]["potential_savings_yearly"], "руб/год")
for o in result["overlaps"]:
    print("-", o["explanation"])
"""

# ── ячейка 4 (опционально): модель как HTTP-сервис ────────────────
# Нужна, только если бэкенд остаётся на ноутбуке, а модель на Kaggle.
# Для записи демо это не требуется — проще прогнать всё в ноутбуке.
"""
!pip install -q fastapi uvicorn pyngrok nest_asyncio

import nest_asyncio, uvicorn, threading
from fastapi import FastAPI
from pydantic import BaseModel
sys.path.insert(0, "ai_core")
from gemma4_local import Gemma4

nest_asyncio.apply()
model = Gemma4(hf_token=os.environ["HF_TOKEN"])
api = FastAPI()

class Req(BaseModel):
    messages: list
    model: str = "gemma"
    temperature: float = 0.2
    stream: bool = False

@api.post("/v1/chat/completions")
def chat(r: Req):
    system = next((m["content"] for m in r.messages if m["role"] == "system"), "")
    user = next((m["content"] for m in r.messages if m["role"] == "user"), "")
    text = model.generate(user, system=system, greedy=True)
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}

threading.Thread(
    target=lambda: uvicorn.run(api, host="0.0.0.0", port=8080),
    daemon=True,
).start()

from pyngrok import ngrok
print("адрес для LLM_BASE_URL:", ngrok.connect(8080).public_url)
"""

# ── что писать в .env на ноутбуке, если используете вариант с HTTP ──
"""
LLM_PROVIDER=http
LLM_BASE_URL=https://xxxx.ngrok.io
LLM_MODEL=gemma-4-E4B
"""
