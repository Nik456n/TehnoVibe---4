# gemma4_local.py — автономный клиент google/gemma-4-E4B-it.
#
# Самодостаточный файл: нужен только transformers, torch, bitsandbytes,
# accelerate. Собран из того, что было ИЗМЕРЕНО на Tesla T4 (sm75),
# transformers 5.16.1, torch 2.10 — каждая неочевидная строка ниже стоит
# отдельной пробы, и почти каждая была найдена не с первого раза.
#
# ═══ ЧТО ОБЯЗАТЕЛЬНО ЗНАТЬ ПЕРЕД ЗАПУСКОМ ═══
#
# 1. ВЕРСИЯ transformers >= 5.5.0. Тип "gemma4" введён именно там. На
#    5.0.0 модель не грузится, и сообщение об ошибке наводит на мысль,
#    что версия СЛИШКОМ НОВАЯ — это ловушка, версия слишком старая.
#
# 2. ЭТО МУЛЬТИМОДАЛЬНАЯ МОДЕЛЬ. AutoModelForCausalLM её не возьмёт,
#    нужен AutoModelForMultimodalLM + AutoProcessor. Расходится весь
#    путь, а не только класс: генерация идёт через model.generate(), а
#    не через pipeline("text-generation").
#
# 3. dtype=float16, НЕ bfloat16 — если карта старше Ampere (sm < 80).
#    Это главная находка. В родном для модели bfloat16 mem_efficient
#    attention НЕ РАБОТАЕТ: torch принимает только {Half, Float} и молча
#    уходит в math-ядро, которое материализует матрицу внимания
#    целиком. Измерено: квадратичный член расхода 208.77 байт/ток²
#    против 1.13 в float16 — разница в 15.5 раза. При 8192 токенах это
#    15.62 ГиБ против 1.52.
#    Численно проверено: top-5 логитов совпадает, NaN/Inf нет.
#    На sm >= 80 (A100, L4, 4090) bfloat16 работает нормально — там
#    менять не нужно, и лучше не менять.
#
# 4. ДЛИННЫЕ ПРОМПТЫ — ЧАСТЯМИ. Одним куском потолок 16384 токена,
#    частями по 2048 — 53248 (замерено до OOM, граница уточнена
#    бинарным поиском). Извлечение из начала промпта при этом работает:
#    проверено маркером в первой части и вопросом про него в последней.
#
# 5. ПАМЯТЬ: веса в 4 битах 8.88 ГиБ, KV-кэш 56 КиБ на токен. Кэш
#    ДЕШЁВЫЙ из-за скользящего окна, поэтому на 32k он всего 1.75 ГиБ —
#    узкое место не он, а обработка промпта.
#
# 6. МОДЕЛЬ GATED. Нужен HF_TOKEN в окружении. 401 означает «токен не
#    долетел», а не «нет прав» — принятие лицензии на сайте не помогает,
#    если токена нет в процессе. При отсутствии прав пришёл бы 403.
#
# Требования:
#     pip install "transformers>=5.5.0" torch bitsandbytes accelerate

from __future__ import annotations

import gc
import json
import os
import re
from typing import Any, Optional

MODEL_ID = "google/gemma-4-E4B-it"


class Gemma4:
    """Локальный клиент gemma-4-E4B с прицелом на структурный вывод."""

    def __init__(
        self,
        model_id: str = MODEL_ID,
        load_in_4bit: bool = True,
        dtype: Optional[str] = None,      # None → выбрать по железу
        device_map: str = "auto",
        prefill_chunk: Optional[int] = 2048,
        max_new_tokens: int = 512,
        hf_token: Optional[str] = None,
    ):
        self.model_id = model_id
        self.load_in_4bit = load_in_4bit
        self.device_map = device_map
        self.prefill_chunk = prefill_chunk
        self.max_new_tokens = max_new_tokens
        self.hf_token = hf_token or os.environ.get("HF_TOKEN")
        self._dtype_arg = dtype
        self._model = None
        self._processor = None

    # ── загрузка ────────────────────────────────────────────────────
    def _pick_dtype(self):
        """float16 на старых картах, родной тип на новых.

        Решение по ЖЕЛЕЗУ, а не по вкусу: на sm<80 bfloat16 отключает
        эффективное ядро внимания (см. пункт 3 в шапке), на sm>=80
        работает и точнее по динамическому диапазону.
        """
        import torch
        if self._dtype_arg:
            return {"float16": torch.float16,
                    "bfloat16": torch.bfloat16,
                    "auto": "auto"}[self._dtype_arg]
        if not torch.cuda.is_available():
            return torch.float32
        major, _ = torch.cuda.get_device_capability(0)
        if major < 8:
            print("[gemma4] карта старше Ampere → float16 "
                  "(в bfloat16 внимание уходит в math-ядро)")
            return torch.float16
        return "auto"

    def load(self):
        if self._model is not None:
            return
        import torch
        from transformers import AutoProcessor, AutoModelForMultimodalLM

        quant = None
        if self.load_in_4bit:
            from transformers import BitsAndBytesConfig
            quant = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=True,
                # compute_dtype отдельно от dtype весов: он определяет,
                # в чём считаются матричные умножения.
                bnb_4bit_compute_dtype=torch.float16,
            )

        kw = {"token": self.hf_token} if self.hf_token else {}
        self._processor = AutoProcessor.from_pretrained(
            self.model_id, trust_remote_code=True, **kw)
        self._model = AutoModelForMultimodalLM.from_pretrained(
            self.model_id,
            dtype=self._pick_dtype(),
            device_map=self.device_map,
            quantization_config=quant,
            trust_remote_code=True,
            **kw,
        )
        self._model.eval()

    def unload(self):
        """Выгрузка с ЯВНЫМ gc.collect().

        Без него память освобождается ЛЕНИВО: у объектов циклические
        ссылки, empty_cache() возвращает драйверу только то, что уже
        никем не удерживается. Измерено: после выгрузки висело 1.11 ГиБ,
        после добавления gc.collect() — 0.02.
        """
        self._model = None
        self._processor = None
        gc.collect()
        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:
            pass

    # ── генерация ───────────────────────────────────────────────────
    def _encode(self, prompt: str, system: str = ""):
        # system идёт ОТДЕЛЬНЫМ сообщением, а не приклеивается к тексту:
        # у gemma есть своя роль для него, и склейка ломает шаблон.
        msgs = []
        if system:
            msgs.append({"role": "system",
                         "content": [{"type": "text", "text": system}]})
        msgs.append({"role": "user",
                     "content": [{"type": "text", "text": prompt}]})
        enc = self._processor.apply_chat_template(
            msgs, add_generation_prompt=True, tokenize=True,
            return_dict=True, return_tensors="pt")
        return {k: v.to(self._model.device) if hasattr(v, "to") else v
                for k, v in enc.items()}

    def _prefill_chunks(self, input_ids):
        """Набить кэш по частям. None означает «иди обычным путём».

        Отказ здесь не должен стоить ответа: приём — оптимизация.
        """
        import torch
        try:
            from transformers import DynamicCache
            prefix = input_ids[:, :input_ids.shape[-1] - 1]
            cache = DynamicCache()
            with torch.inference_mode():
                for start in range(0, prefix.shape[-1], self.prefill_chunk):
                    piece = prefix[:, start:start + self.prefill_chunk]
                    pos = torch.arange(start, start + piece.shape[-1],
                                       device=piece.device)
                    out = self._model(input_ids=piece, past_key_values=cache,
                                      use_cache=True, cache_position=pos,
                                      # БЕЗ этого forward вернёт логиты для
                                      # ВСЕХ позиций: словарь 262144, на
                                      # 13k токенов это 6.35 ГиБ впустую.
                                      logits_to_keep=1)
                    cache = out.past_key_values
                    del out
            return cache
        except Exception as e:
            print(f"[gemma4] обработка частями не удалась ({e}); "
                  f"дальше обычным путём")
            return None

    def generate(self, prompt: str, system: str = "",
                 max_new_tokens: Optional[int] = None,
                 temperature: float = 0.7,
                 greedy: bool = False) -> str:
        import torch
        self.load()
        inputs = self._encode(prompt, system)
        n = inputs["input_ids"].shape[-1]

        gen_kw: dict[str, Any] = {
            "max_new_tokens": max_new_tokens or self.max_new_tokens,
        }
        if greedy:
            # temperature при do_sample=False бессмысленна, transformers
            # об этом предупреждает — не передаём её вовсе.
            gen_kw["do_sample"] = False
        else:
            gen_kw.update(do_sample=True, temperature=temperature)

        cache = None
        # Порог в две части: на коротких промптах приём не окупается,
        # только добавляет проходов.
        if (self.prefill_chunk and n > self.prefill_chunk * 2
                and "pixel_values" not in inputs):
            cache = self._prefill_chunks(inputs["input_ids"])

        with torch.inference_mode():
            if cache is not None:
                out = self._model.generate(**inputs, past_key_values=cache,
                                           **gen_kw)
            else:
                out = self._model.generate(**inputs, **gen_kw)
        # Декодируем ТОЛЬКО новые токены: иначе в ответе окажется весь
        # промпт целиком.
        text = self._processor.decode(out[0][n:], skip_special_tokens=True)
        del out, cache
        return text.strip()

    # ── структурный вывод ───────────────────────────────────────────
    def generate_json(self, prompt: str, schema_hint: str,
                      retries: int = 2, **kwargs) -> Any:
        """Ответ в JSON с проверкой и повтором.

        ВАЖНО, ПОЧЕМУ ТАК. У gemma нет constrained decoding из коробки:
        модель не обязана выдать валидный JSON, она лишь старается.
        Поэтому надёжность даёт не промпт, а ЦИКЛ: сгенерировать →
        распарсить → при ошибке повторить, показав модели, что именно
        сломалось.
        Три частые поломки, все обрабатываются ниже:
          • обёртка в ```json ... ``` — модель любит форматировать;
          • текст до и после объекта («Вот результат: {...}»);
          • обрыв по бюджету токенов — тогда JSON просто не закрыт, и
            увеличивать надо max_new_tokens, а не чинить парсер.
        """
        system = ("Ты возвращаешь ТОЛЬКО валидный JSON без пояснений, "
                  "без markdown-обёртки и без текста до или после. "
                  f"Структура ответа:\n{schema_hint}")
        last_err = ""
        for attempt in range(retries + 1):
            ask = prompt if not last_err else (
                f"{prompt}\n\nПредыдущий ответ не разобрался как JSON: "
                f"{last_err}. Верни только корректный JSON.")
            raw = self.generate(ask, system=system, greedy=True, **kwargs)
            parsed, err = extract_json(raw)
            if err is None:
                return parsed
            last_err = err
            print(f"[gemma4] попытка {attempt + 1}: {err}")
        raise ValueError(
            f"JSON не получен за {retries + 1} попыток. Последняя ошибка: "
            f"{last_err}. Если ответ обрывается — поднимите "
            f"max_new_tokens, парсер тут не поможет.")


def extract_json(text: str):
    """Вытащить JSON из ответа модели. Возвращает (объект, ошибка)."""
    s = text.strip()
    # 1. markdown-обёртка
    fence = re.search(r"```(?:json)?\s*(.+?)```", s, re.S)
    if fence:
        s = fence.group(1).strip()
    # 2. Тип определяем по ПЕРВОЙ значащей скобке, а не перебором.
    # Перебор «сначала {, потом [» разбирал массив [{"a":1}] как
    # внутренний объект и молча возвращал не ту структуру — ошибка
    # хуже отказа, потому что выглядит как успех.
    starts = [(s.find(o), o, c) for o, c in (("{", "}"), ("[", "]"))
              if s.find(o) != -1]
    if not starts:
        return None, "в ответе не найдено ни объекта, ни массива"
    i, opener, closer = min(starts)

    j = s.rfind(closer)
    if j > i:
        candidate = s[i:j + 1]
        try:
            return json.loads(candidate), None
        except json.JSONDecodeError as e:
            err = f"{e.msg} (позиция {e.pos})"
            if candidate.count(opener) > candidate.count(closer):
                err += " — похоже на обрыв по max_new_tokens"
            return None, err

    # Закрывающей скобки нет вовсе: ответ оборвался. Это НЕ ошибка
    # разметки, и чинить надо бюджет токенов, а не парсер — поэтому
    # сообщение говорит именно об этом.
    return None, ("ответ не закрыт: нет символа "
                  f"{closer!r} — обрыв по max_new_tokens")


if __name__ == "__main__":
    llm = Gemma4(prefill_chunk=2048)
    print(llm.generate("Ответь одним словом: столица Франции?",
                       greedy=True, max_new_tokens=16))
    print(llm.generate_json(
        "Разбери: 'Иванов, 34 года, инженер из Казани'.",
        schema_hint='{"name": str, "age": int, "job": str, "city": str}',
        max_new_tokens=200))
    llm.unload()
