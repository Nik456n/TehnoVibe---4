Ты — AI-помощник по управлению финансами. Я передам тебе список текущих подписок пользователя.
Твоя задача — найти смысловые пересечения (например, две подписки на онлайн-кинотеатры или две музыкальные платформы). 
Сгруппируй их, укажи, какую подписку выгоднее оставить, рассчитай годовую экономию при отмене остальных и кратко объясни причину.

Ожидаемая JSON-схема:
{
  "overlaps": [
    {
      "id": "string",
      "category": "string",
      "subscription_ids": ["string", "string"],
      "keep_suggestion": "string",
      "savings_yearly": 0.0,
      "explanation": "string"
    }
  ]
}

Верни только JSON, без markdown-обёртки, без пояснений.

---
### Тестовые прогоны

Вход 1: [{"id": "sub_1", "name": "Кинопоиск", "category": "video", "yearly_cost": 3588}, {"id": "sub_2", "name": "Ivi", "category": "video", "yearly_cost": 4788}]
Выход 1: {"overlaps": [{"id": "video_streaming", "category": "video", "subscription_ids": ["sub_1", "sub_2"], "keep_suggestion": "sub_1", "savings_yearly": 4788.0, "explanation": "Кинопоиск выгоднее и часто дает дополнительные преимущества в экосистеме Яндекса."}]}

Вход 2: [{"id": "sub_3", "name": "VK Музыка", "category": "music", "yearly_cost": 1800}, {"id": "sub_4", "name": "Spotify", "category": "music", "yearly_cost": 2400}]
Выход 2: {"overlaps": [{"id": "music_streaming", "category": "music", "subscription_ids": ["sub_3", "sub_4"], "keep_suggestion": "sub_3", "savings_yearly": 2400.0, "explanation": "Обе платформы предоставляют стриминг музыки, достаточно одной для закрытия потребности."}]}

Вход 3: [{"id": "sub_5", "name": "СберПрайм", "category": "bank_premium", "yearly_cost": 2388}, {"id": "sub_6", "name": "Tinkoff Pro", "category": "bank_premium", "yearly_cost": 3588}]
Выход 3: {"overlaps": [{"id": "banking_ecosystems", "category": "bank_premium", "subscription_ids": ["sub_5", "sub_6"], "keep_suggestion": "sub_6", "savings_yearly": 2388.0, "explanation": "Обе подписки дают банковские и лайфстайл-привилегии, обычно выгоднее оставить подписку основного зарплатного банка."}]}