# Удержание сессии Kaggle — запускать ПОСЛЕДНЕЙ ячейкой.
#
# Как это работает. Uvicorn крутится в фоновом потоке (daemon), поэтому
# основной поток блокировать безопасно: туннель и API продолжают
# отвечать. Цикл ниже занимает ядро, и Kaggle не считает сессию
# простаивающей.
#
# Ячейка не завершится сама — так и задумано. Останавливать кнопкой
# Interrupt, когда сессия больше не нужна.
#
# ВАЖНО про жёсткий лимит: интерактивная сессия Kaggle живёт до 9 часов
# независимо от активности. Перед защитой ноутбук всё равно надо
# перезапустить и разослать новый адрес туннеля.

import time
import gc
import requests
import torch
from datetime import datetime

PING_EVERY = 240          # секунд между обращениями к модели
LOCAL = "http://127.0.0.1:8080"


def gpu_report() -> str:
    if not torch.cuda.is_available():
        return "GPU недоступна"
    used = torch.cuda.memory_allocated() / 1024**3
    reserved = torch.cuda.memory_reserved() / 1024**3
    total = torch.cuda.get_device_properties(0).total_memory / 1024**3
    return f"GPU {used:.2f} занято / {reserved:.2f} зарезервировано / {total:.1f} всего ГиБ"


print("Удержание сессии запущено. Останавливать кнопкой Interrupt.")
print(gpu_report())
print()

beat = 0
while True:
    beat += 1
    stamp = datetime.now().strftime("%H:%M:%S")
    try:
        # Короткий запрос к самой модели: заодно держит её прогретой
        r = requests.post(
            f"{LOCAL}/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "1"}]},
            timeout=60,
        )
        status = "модель отвечает" if r.status_code == 200 else f"код {r.status_code}"
    except Exception as exc:
        status = f"ОШИБКА: {type(exc).__name__}"

    # Чистим лениво освобождаемую память: без gc.collect() у меня
    # висело больше гигабайта впустую
    gc.collect()
    torch.cuda.empty_cache()

    print(f"[{stamp}] удар {beat}: {status} | {gpu_report()}", flush=True)
    time.sleep(PING_EVERY)
