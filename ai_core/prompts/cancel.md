Ты — эксперт по клиентскому сервису. Для указанного цифрового сервиса сгенерируй пошаговую инструкцию по отмене подписки.
Шаги должны быть реалистичными и точными (от 3 до 6 шагов). 
Оцени сложность отмены строго из списка: easy, medium, hard. 
Параметр source всегда устанавливай в "llm_generated". Поле letter_template оставь null.

Ожидаемая JSON-схема (объект CancelInstruction):
{
  "subscription_id": "string",
  "service_name": "string",
  "difficulty": "easy",
  "steps": [
    "string"
  ],
  "url": "string",
  "letter_template": null,
  "source": "llm_generated",
  "savings_yearly": 0.0
}

Верни только JSON, без markdown-обёртки, без пояснений.

---
### Тестовые прогоны

Вход 1: "Midjourney", id "sub_2", savings: 12000
Выход 1: {"subscription_id": "sub_2", "service_name": "Midjourney", "difficulty": "easy", "steps": ["Зайдите на midjourney.com/account", "Авторизуйтесь через Discord", "В разделе Plan Details нажмите Cancel Plan"], "url": "https://midjourney.com/account", "letter_template": null, "source": "llm_generated", "savings_yearly": 12000.0}

Вход 2: "Adobe Creative Cloud", id "sub_1", savings: 24000
Выход 2: {"subscription_id": "sub_1", "service_name": "Adobe Creative Cloud", "difficulty": "hard", "steps": ["Войдите в аккаунт на account.adobe.com", "Перейдите в раздел 'Планы и платежи'", "Нажмите 'Управление планом'", "Выберите 'Отменить план' и пройдите экраны удержания"], "url": "https://account.adobe.com/plans", "letter_template": null, "source": "llm_generated", "savings_yearly": 24000.0}