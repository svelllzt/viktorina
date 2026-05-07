import asyncio
import random
from datetime import datetime, timezone

import config as cfg
from sqlalchemy import delete, or_, select

from main import (
    AccessType,
    AnswerOption,
    Question,
    QuestionType,
    Quiz,
    QuizStatus,
    SessionLocal,
    TimerMode,
    User,
    generate_link_code,
    hash_password,
)

TOTAL_QUIZZES = 10
ADMIN_QUIZZES = 5
AUTHOR_POOL = [
    ("olga.ivanova@example.com", "12345678", "user"),
    ("sergey.petrov@example.com", "12345678", "user"),
    ("anna.smirnova@example.com", "12345678", "user"),
    ("dmitry.kozlov@example.com", "12345678", "user"),
]
QUIZ_TITLES = [
    "История России: школьный тур",
    "Кино и сериалы: узнаешь кадр?",
    "IT-основы для начинающих",
    "География мира за 10 минут",
    "Литература: классика и современность",
    "Музыкальный микс 2000-х",
    "Биология без паники",
    "Математика: быстрый разгон",
    "Путешествия и страны",
    "Общие знания: вечерний раунд",
]
QUIZ_DESCRIPTIONS = [
    "Короткая викторина для разминки перед занятиями.",
    "Собрал(а) подборку вопросов по популярным темам.",
    "Подойдет для командной игры и личного прохождения.",
    "Вопросы разного уровня: от простых до с подвохом.",
    "Отличный вариант для живого раунда с ведущим.",
    "Квиз на 5-7 минут с динамичным темпом.",
    "Можно проходить индивидуально или в мини-группе.",
    "Проверяем эрудицию и скорость реакции.",
    "Сбалансированная подборка с понятными формулировками.",
    "Хорошо подходит для разминки в начале встречи.",
]


async def ensure_user(email: str, password: str, role: str) -> User:
    async with SessionLocal() as db:
        r = await db.execute(select(User).where(User.email == email.strip().lower()))
        user = r.scalar_one_or_none()
        if user:
            user.password_hash = hash_password(password)
            user.role = role
            await db.commit()
            await db.refresh(user)
            return user
        user = User(email=email.strip().lower(), password_hash=hash_password(password), role=role)
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user


async def cleanup_old_tests() -> None:
    async with SessionLocal() as db:
        known_titles = QUIZ_TITLES[:TOTAL_QUIZZES]
        seeded_emails = [email for email, _, _ in AUTHOR_POOL]
        seeded_emails.append(str(cfg.get_settings()["ADMIN_EMAIL"]).strip().lower())
        seeded_user_ids = (
            await db.execute(select(User.id).where(User.email.in_(seeded_emails)))
        ).scalars().all()
        q_ids = (
            await db.execute(
                select(Quiz.id).where(
                    or_(
                        Quiz.title.like("[TEST]%"),
                        Quiz.description.like("Автоматически созданный тестовый квиз%"),
                        (Quiz.title.in_(known_titles) & Quiz.author_id.in_(seeded_user_ids)),
                    )
                )
            )
        ).scalars().all()
        if not q_ids:
            return
        await db.execute(delete(Quiz).where(Quiz.id.in_(q_ids)))
        await db.commit()


async def unique_code(db) -> str:
    for _ in range(30):
        code = generate_link_code()
        exists = await db.execute(select(Quiz.id).where(Quiz.published_link == code))
        if not exists.scalar_one_or_none():
            return code
    raise RuntimeError("could not generate unique published code")


def quiz_payload(idx: int) -> dict:
    timer_mode = TimerMode.per_question if idx % 2 == 0 else TimerMode.whole_quiz
    return {
        "title": QUIZ_TITLES[idx % len(QUIZ_TITLES)],
        "description": QUIZ_DESCRIPTIONS[idx % len(QUIZ_DESCRIPTIONS)],
        "timer_mode": timer_mode,
        "timer_duration": random.choice([20, 30, 45, 60]),
        "show_correct_after_question": bool(idx % 2),
        "show_final_results": True,
        "status": QuizStatus.published,
        "access_type": AccessType.public if idx % 3 else AccessType.password,
        "access_password": "1234" if idx % 3 == 0 else None,
        "featured": True if idx < 6 else False,
        "is_live": True if idx % 2 == 0 else False,
    }


async def add_questions(db, quiz_id: int) -> None:
    q1 = Question(
        quiz_id=quiz_id,
        text="Сколько минут в одном часу?",
        question_type=QuestionType.single_choice,
        points=1,
        order=1,
    )
    q2 = Question(
        quiz_id=quiz_id,
        text="Выберите языки программирования",
        question_type=QuestionType.multiple_choice,
        points=2,
        order=2,
    )
    q3 = Question(
        quiz_id=quiz_id,
        text="Столица Франции",
        question_type=QuestionType.text_input,
        points=1,
        order=3,
    )
    db.add_all([q1, q2, q3])
    await db.flush()

    db.add_all(
        [
            AnswerOption(question_id=q1.id, text="30", is_correct=False),
            AnswerOption(question_id=q1.id, text="60", is_correct=True),
            AnswerOption(question_id=q1.id, text="90", is_correct=False),
            AnswerOption(question_id=q1.id, text="120", is_correct=False),
            AnswerOption(question_id=q2.id, text="Python", is_correct=True),
            AnswerOption(question_id=q2.id, text="JavaScript", is_correct=True),
            AnswerOption(question_id=q2.id, text="HTML", is_correct=False),
            AnswerOption(question_id=q2.id, text="Go", is_correct=True),
            AnswerOption(question_id=q3.id, text="Париж", is_correct=True),
            AnswerOption(question_id=q3.id, text="paris", is_correct=True),
        ]
    )


async def seed_quizzes(total: int = TOTAL_QUIZZES) -> None:
    total = max(1, min(10, int(total)))
    admin_count = min(ADMIN_QUIZZES, total)
    s = cfg.get_settings()
    admin = await ensure_user(str(s["ADMIN_EMAIL"]), str(s["ADMIN_PASSWORD"]), "admin")
    users = []
    for email, password, role in AUTHOR_POOL:
        users.append(await ensure_user(email, password, role))
    authors = [admin] * admin_count
    rest = total - admin_count
    for i in range(rest):
        authors.append(users[i % len(users)])

    await cleanup_old_tests()
    now = datetime.now(timezone.utc)
    async with SessionLocal() as db:
        for idx in range(total):
            p = quiz_payload(idx)
            quiz = Quiz(
                title=p["title"],
                description=p["description"],
                timer_mode=p["timer_mode"],
                timer_duration=p["timer_duration"],
                show_correct_after_question=p["show_correct_after_question"],
                show_final_results=p["show_final_results"],
                status=p["status"],
                access_type=p["access_type"],
                access_password=p["access_password"],
                author_id=authors[idx].id,
                featured=p["featured"],
                featured_at=now if p["featured"] else None,
                is_live=p["is_live"],
                published_link=await unique_code(db),
            )
            db.add(quiz)
            await db.flush()
            await add_questions(db, quiz.id)
        await db.commit()
    print(f"Created {total} quizzes ({admin_count} by admin).")


if __name__ == "__main__":
    asyncio.run(seed_quizzes())
