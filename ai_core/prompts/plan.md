Ты — финансовый консультант. Составь план оптимизации подписок на основе их списка и найденных пересечений.
Для каждой подписки определи действие (cancel, downgrade, keep, review).
Распредели приоритеты в числовом формате (1 — делать первым, 2 — вторым и т.д.).
Укажи годовую экономию для действий отмены или понижения тарифа.

Ожидаемая JSON-схема:
{
  "plan": [
    {
      "subscription_id": "string",
      "action": "cancel", 
      "priority": 1,
      "reason": "string",
      "savings_yearly": 0.0
    }
  ]
}

Верни только JSON, без markdown-обёртки, без пояснений. (action может быть только cancel, downgrade, keep, review).

---
### Тестовые прогоны

Вход 1: Сервисы: Ivi (sub_1, 4788/год), Кинопоиск (sub_2, 3588/год). Пересечения: video.
Выход 1: {"plan": [{"subscription_id": "sub_1", "action": "cancel", "priority": 1, "reason": "Дублирует функции Кинопоиска.", "savings_yearly": 4788.0}, {"subscription_id": "sub_2", "action": "keep", "priority": 3, "reason": "Основной видеосервис.", "savings_yearly": 0.0}]}

Вход 2: Сервисы: МТС Premium (sub_3, 2988/год), VPN (sub_4, 1800/год).
Выход 2: {"plan": [{"subscription_id": "sub_3", "action": "keep", "priority": 4, "reason": "Базовая экосистема и связь.", "savings_yearly": 0.0}, {"subscription_id": "sub_4", "action": "review", "priority": 2, "reason": "Убедитесь, что VPN-сервис работает стабильно, возможно стоит поискать бесплатный аналог.", "savings_yearly": 1800.0}]}

Вход 3: Сервисы: Duolingo (sub_5, 6000/год).
Выход 3: {"plan": [{"subscription_id": "sub_5", "action": "downgrade", "priority": 2, "reason": "Можно перейти на семейный тариф для разделения стоимости с друзьями.", "savings_yearly": 3000.0}]}