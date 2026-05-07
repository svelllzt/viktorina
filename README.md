# QuizLab

Платформа для создания и проведения викторин на FastAPI с двумя режимами:
- обычное прохождение по ссылке;
- синхронный **Live Quiz** с ведущим, PIN-кодом и WebSocket-обновлениями.

---

## RU

### Возможности

- Конструктор викторин: название, описание, таймер, доступ (публично/по паролю), вопросы и варианты.
- Типы вопросов:
  - один правильный ответ;
  - несколько правильных ответов;
  - текстовый ответ.
- Режимы таймера:
  - на каждый вопрос;
  - на всю викторину.
- Публикация квиза по уникальной ссылке и QR-коду.
- Live Quiz:
  - запуск ведущим;
  - подключение участников по PIN/QR;
  - синхронные вопросы;
  - статистика ответов в реальном времени.
- Кабинет автора:
  - управление квизами;
  - статистика и графики;
  - экспорт результатов в CSV.
- Восстановление пароля через SMTP.
- Админ-раздел:
  - модерация витрины (`featured`);
  - настройки SMTP через UI.

### Технологии

- Python 3.11+
- FastAPI
- SQLAlchemy (async) + SQLite (`aiosqlite`)
- Jinja2
- Vanilla JS
- WebSocket (`fastapi.WebSocket`)
- `qrcode[pil]` для QR
- `smtplib` + `asyncio.to_thread` для отправки почты
- Chart.js (CDN)
- Google Fonts + Font Awesome 6

### Быстрый старт

1. Установите зависимости:

```bash
pip install -r requirements.txt
```

2. Запустите приложение:

```bash
uvicorn main:app --reload
```

3. Откройте:
- `http://127.0.0.1:8000`

### Конфигурация

Базовые значения хранятся в `config.py`.  
Изменяемые runtime-настройки записываются в `runtime_config.json` через админ-страницу:
- `/admin/settings`

Основные поля:
- `ADMIN_EMAIL`
- `ADMIN_PASSWORD`
- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USER`
- `SMTP_PASSWORD`
- `SMTP_FROM`

### Тестовые данные

Для генерации демо-данных используйте:

```bash
python test.py
```

Скрипт создаёт до 10 опубликованных квизов (минимум 5 авторства админа), вопросы и варианты, а также обновляет демо-пользователей.

### Основные маршруты

- `/` — главная
- `/quizzes` — каталог витрины
- `/dashboard` — кабинет автора
- `/quiz/create` — создание квиза
- `/quiz/{id}/edit` — редактирование
- `/play/{code}` — старт обычного прохождения
- `/quiz/{id}/live` — экран ведущего (live)
- `/join/{pin}` — вход участника в live
- `/admin/featured` — модерация
- `/admin/settings` — SMTP-настройки

### Live Quiz (кратко)

1. Автор включает `is_live` в настройках квиза.
2. Из кабинета нажимает «Запустить Live».
3. Генерируется PIN и QR (`/join/{pin}`).
4. Участники подключаются до старта.
5. Ведущий запускает викторину, листает вопросы и завершает сессию.

---

## EN

### Overview

QuizLab is a FastAPI-based quiz platform with two gameplay modes:
- regular link-based play;
- synchronized **Live Quiz** with a host, PIN code, and real-time WebSocket updates.

### Features

- Quiz builder: title, description, timers, access mode, questions, options.
- Question types:
  - single choice;
  - multiple choice;
  - text input.
- Timer modes:
  - per-question;
  - whole-quiz.
- Publish quizzes via unique links and QR codes.
- Live mode with host-controlled flow and real-time answer stats.
- Author dashboard with analytics and CSV export.
- Password reset via SMTP.
- Admin panel for featured moderation and SMTP settings.

### Stack

- Python 3.11+
- FastAPI
- SQLAlchemy async + SQLite
- Jinja2
- Vanilla JavaScript
- WebSocket (`fastapi.WebSocket`)
- `qrcode[pil]`
- `smtplib` + `asyncio.to_thread`
- Chart.js CDN

### Run locally

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

Open:
- `http://127.0.0.1:8000`

### Seed demo data

```bash
python test.py
```

This script creates up to 10 published quizzes (at least 5 owned by admin), plus demo users/questions/options.

---

## License

Internal/private project by default.  
Add your preferred license before publishing.
