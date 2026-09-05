"""Прогрев кэша LLM перед защитой.

Прогоняет демо-выписки через полный пайплайн, чтобы все ответы модели
осели в data/llm_cache.json. После этого демонстрация работает мгновенно
и не зависит от сети и доступности GigaChat.

Запуск из корня репозитория:
    python scripts/warm_cache.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.ml import llm                      # noqa: E402
from backend.ml.detector import detect          # noqa: E402
from backend.ml.parser import parse_statement   # noqa: E402

STATEMENTS = [
    ROOT / "data" / "demo_statement_clean.csv",
    ROOT / "data" / "demo_statement_clean.pdf",
    ROOT / "data" / "demo_statement_dirty.csv",
]


def main() -> None:
    provider = llm.get_provider()
    if provider is None:
        print("LLM_API_KEY не задан в .env — прогревать нечего.")
        print("Демо будет работать на детекторе, llm_used: false.")
        return

    cache = llm.Cache()
    before = len(cache.data)

    for path in STATEMENTS:
        if not path.exists():
            print(f"пропускаю, нет файла: {path.name}")
            continue
        result = detect(parse_statement(path.read_bytes()))
        result = llm.enrich(result, provider, cache)
        status = "модель ответила" if result["llm_used"] else "модель не ответила"
        print(f"{path.name:32} {len(result['subscriptions'])} подписок, {status}")

    cache.save()
    print(f"\nв кэше записей: {len(cache.data)} (было {before})")
    print(f"файл: {llm.CACHE_FILE.relative_to(ROOT)}")
    print("Коммитить кэш в репозиторий стоит — тогда демо работает"
          " у всей команды и без ключа.")


if __name__ == "__main__":
    main()
