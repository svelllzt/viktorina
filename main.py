import asyncio
import csv
import io
import json
import os
import base64
import hashlib
import hmac
from urllib.parse import quote, unquote
import secrets
import smtplib
import statistics
import string
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from enum import Enum
from typing import Annotated, Any, Optional

import config as cfg
from email_validator import validate_email as ev_validate_email, EmailNotValidError
import qrcode
from fastapi.exceptions import RequestValidationError
from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from jose import JWTError, jwt
from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    delete,
    func,
    select,
    text,
)
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, selectinload

SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
ALGORITHM = "HS256"
ACCESS_EXPIRE_MINUTES = 30
REFRESH_EXPIRE_DAYS = 14
PLAY_COOKIE = "play_session"
PLAY_MAX_AGE = 86400 * 7
LIVE_COOKIE = "live_session"
PBKDF2_PREFIX = "pbkdf2_sha256"
PBKDF2_ITERS = 390000

engine = create_async_engine("sqlite+aiosqlite:///./quiz.db", echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
templates = Jinja2Templates(directory="templates")
play_serializer = URLSafeTimedSerializer(SECRET_KEY, salt="play")
live_serializer = URLSafeTimedSerializer(SECRET_KEY, salt="live")


def make_qr_data_uri(url: str) -> str:
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


class Base(DeclarativeBase):
    pass


class TimerMode(str, Enum):
    per_question = "per_question"
    whole_quiz = "whole_quiz"


class QuizStatus(str, Enum):
    draft = "draft"
    published = "published"
    closed = "closed"


class AccessType(str, Enum):
    public = "public"
    password = "password"


class QuestionType(str, Enum):
    single_choice = "single_choice"
    multiple_choice = "multiple_choice"
    text_input = "text_input"


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), default="user")
    reset_token: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    reset_token_expires: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    quizzes: Mapped[list["Quiz"]] = relationship(back_populates="author", cascade="all, delete-orphan")


class Quiz(Base):
    __tablename__ = "quizzes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text, default="")
    timer_mode: Mapped[TimerMode] = mapped_column(SAEnum(TimerMode, values_callable=lambda x: [e.value for e in x]))
    timer_duration: Mapped[int] = mapped_column(Integer, default=30)
    show_correct_after_question: Mapped[bool] = mapped_column(Boolean, default=False)
    show_final_results: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[QuizStatus] = mapped_column(
        SAEnum(QuizStatus, values_callable=lambda x: [e.value for e in x]), default=QuizStatus.draft
    )
    access_type: Mapped[AccessType] = mapped_column(
        SAEnum(AccessType, values_callable=lambda x: [e.value for e in x]), default=AccessType.public
    )
    access_password: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_link: Mapped[Optional[str]] = mapped_column(String(64), unique=True, nullable=True)
    featured: Mapped[bool] = mapped_column(Boolean, default=False)
    featured_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    is_live: Mapped[bool] = mapped_column(Boolean, default=False)
    live_pin: Mapped[Optional[str]] = mapped_column(String(6), unique=True, nullable=True)
    live_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    live_finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    live_current_index: Mapped[int] = mapped_column(Integer, default=0)
    live_question_deadline_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    live_quiz_deadline_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    author: Mapped["User"] = relationship(back_populates="quizzes")
    questions: Mapped[list["Question"]] = relationship(
        back_populates="quiz", order_by="Question.order", cascade="all, delete-orphan"
    )
    attempts: Mapped[list["Attempt"]] = relationship(back_populates="quiz", cascade="all, delete-orphan")


class Question(Base):
    __tablename__ = "questions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quiz_id: Mapped[int] = mapped_column(ForeignKey("quizzes.id", ondelete="CASCADE"))
    text: Mapped[str] = mapped_column(Text)
    question_type: Mapped[QuestionType] = mapped_column(
        SAEnum(QuestionType, values_callable=lambda x: [e.value for e in x])
    )
    points: Mapped[int] = mapped_column(Integer, default=1)
    timer_override: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    explanation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    order: Mapped[int] = mapped_column(Integer, default=0)

    quiz: Mapped["Quiz"] = relationship(back_populates="questions")
    options: Mapped[list["AnswerOption"]] = relationship(
        back_populates="question", cascade="all, delete-orphan"
    )
    user_answers: Mapped[list["UserAnswer"]] = relationship(
        back_populates="question", cascade="all, delete-orphan"
    )


class AnswerOption(Base):
    __tablename__ = "answer_options"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"))
    text: Mapped[str] = mapped_column(String(2000))
    is_correct: Mapped[bool] = mapped_column(Boolean, default=False)

    question: Mapped["Question"] = relationship(back_populates="options")


class Attempt(Base):
    __tablename__ = "attempts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quiz_id: Mapped[int] = mapped_column(ForeignKey("quizzes.id", ondelete="CASCADE"))
    participant_name: Mapped[str] = mapped_column(String(200), default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    score: Mapped[int] = mapped_column(Integer, default=0)
    max_score: Mapped[int] = mapped_column(Integer, default=0)
    quiz_deadline_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    quiz: Mapped["Quiz"] = relationship(back_populates="attempts")
    user_answers: Mapped[list["UserAnswer"]] = relationship(
        back_populates="attempt", cascade="all, delete-orphan"
    )
    question_deadlines: Mapped[list["AttemptQuestionDeadline"]] = relationship(
        back_populates="attempt", cascade="all, delete-orphan"
    )


class AttemptQuestionDeadline(Base):
    __tablename__ = "attempt_question_deadlines"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("attempts.id", ondelete="CASCADE"))
    question_index: Mapped[int] = mapped_column(Integer)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    attempt: Mapped["Attempt"] = relationship(back_populates="question_deadlines")


class UserAnswer(Base):
    __tablename__ = "user_answers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("attempts.id", ondelete="CASCADE"))
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"))
    selected_options: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    text_answer: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_correct: Mapped[bool] = mapped_column(Boolean, default=False)
    answered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    attempt: Mapped["Attempt"] = relationship(back_populates="user_answers")
    question: Mapped["Question"] = relationship(back_populates="user_answers")


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def migrate_schema():
    stmts = [
        "ALTER TABLE users ADD COLUMN role VARCHAR(32) DEFAULT 'user'",
        "ALTER TABLE users ADD COLUMN reset_token VARCHAR(128)",
        "ALTER TABLE users ADD COLUMN reset_token_expires TIMESTAMP",
        "ALTER TABLE quizzes ADD COLUMN featured INTEGER DEFAULT 0",
        "ALTER TABLE quizzes ADD COLUMN featured_at TIMESTAMP",
        "ALTER TABLE quizzes ADD COLUMN is_live INTEGER DEFAULT 0",
        "ALTER TABLE quizzes ADD COLUMN live_pin VARCHAR(6)",
        "ALTER TABLE quizzes ADD COLUMN live_started_at TIMESTAMP",
        "ALTER TABLE quizzes ADD COLUMN live_finished_at TIMESTAMP",
        "ALTER TABLE quizzes ADD COLUMN live_current_index INTEGER DEFAULT 0",
        "ALTER TABLE quizzes ADD COLUMN live_question_deadline_at TIMESTAMP",
        "ALTER TABLE quizzes ADD COLUMN live_quiz_deadline_at TIMESTAMP",
    ]
    async with engine.begin() as conn:
        for stmt in stmts:
            try:
                await conn.execute(text(stmt))
            except OperationalError:
                pass


async def ensure_admin_user(db: AsyncSession):
    s = cfg.get_settings()
    email = s["ADMIN_EMAIL"].strip().lower()
    pwd = hash_password(str(s["ADMIN_PASSWORD"]))
    r = await db.execute(select(User).where(User.email == email))
    u = r.scalar_one_or_none()
    if u:
        u.password_hash = pwd
        u.role = "admin"
    else:
        db.add(User(email=email, password_hash=pwd, role="admin"))
    await db.commit()


def get_base_url(request: Request) -> str:
    forwarded_proto = request.headers.get("X-Forwarded-Proto")
    scheme = forwarded_proto if forwarded_proto else request.url.scheme
    forwarded_host = request.headers.get("X-Forwarded-Host")
    host = forwarded_host if forwarded_host else request.headers.get("Host", "localhost:8000")
    return f"{scheme}://{host}"


def send_mail_sync(to_addr: str, subject: str, body: str) -> None:
    s = cfg.get_settings()
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = s["SMTP_FROM"]
    msg["To"] = to_addr
    with smtplib.SMTP(s["SMTP_HOST"], int(s["SMTP_PORT"]), timeout=45) as server:
        server.starttls()
        server.login(s["SMTP_USER"], s["SMTP_PASSWORD"])
        server.send_message(msg)


async def send_mail_async(to_addr: str, subject: str, body: str) -> None:
    await asyncio.to_thread(send_mail_sync, to_addr, subject, body)


def verify_password(plain: str, hashed: str) -> bool:
    if not hashed:
        return False
    if hashed.startswith(f"{PBKDF2_PREFIX}$"):
        try:
            _, it_s, salt_b64, dk_b64 = hashed.split("$", 3)
            iters = int(it_s)
            salt = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
            expected = base64.urlsafe_b64decode(dk_b64.encode("ascii"))
            check = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt, iters, dklen=len(expected))
            return hmac.compare_digest(check, expected)
        except Exception:
            return False
    if hashed.startswith("$2"):
        try:
            import bcrypt as _bcrypt

            return bool(_bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8")))
        except Exception:
            return False
    return False


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERS, dklen=32)
    salt_b64 = base64.urlsafe_b64encode(salt).decode("ascii")
    dk_b64 = base64.urlsafe_b64encode(dk).decode("ascii")
    return f"{PBKDF2_PREFIX}${PBKDF2_ITERS}${salt_b64}${dk_b64}"


def create_token(data: dict, expires_delta: timedelta) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + expires_delta
    to_encode["exp"] = expire
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None


def normalize_text(s: str) -> str:
    return " ".join(s.lower().strip().split())


def generate_link_code() -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(12))


def generate_live_pin() -> str:
    return "".join(secrets.choice(string.digits) for _ in range(6))


async def get_db():
    async with SessionLocal() as session:
        yield session


DbDep = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user(request: Request, db: DbDep) -> Optional[User]:
    token = request.cookies.get("access_token")
    if not token:
        return None
    payload = decode_token(token)
    if not payload or payload.get("type") != "access":
        return None
    uid = payload.get("sub")
    if not uid:
        return None
    r = await db.execute(select(User).where(User.id == int(uid)))
    return r.scalar_one_or_none()


async def require_user(request: Request, db: DbDep) -> User:
    user = await get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="auth_required")
    return user


UserDep = Annotated[User, Depends(require_user)]


async def require_admin(user: UserDep) -> User:
    if getattr(user, "role", None) != "admin":
        raise HTTPException(status_code=403, detail="forbidden")
    return user


AdminDep = Annotated[User, Depends(require_admin)]


def set_auth_cookies(response: Response, user_id: int):
    access = create_token({"sub": str(user_id), "type": "access"}, timedelta(minutes=ACCESS_EXPIRE_MINUTES))
    refresh = create_token({"sub": str(user_id), "type": "refresh"}, timedelta(days=REFRESH_EXPIRE_DAYS))
    response.set_cookie(
        "access_token",
        access,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=ACCESS_EXPIRE_MINUTES * 60,
        path="/",
    )
    response.set_cookie(
        "refresh_token",
        refresh,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=REFRESH_EXPIRE_DAYS * 86400,
        path="/",
    )


def clear_auth_cookies(response: Response):
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/")


def play_cookie_value(attempt_id: int, quiz_id: int) -> str:
    return play_serializer.dumps({"a": attempt_id, "q": quiz_id})


def read_play_cookie(request: Request) -> Optional[tuple[int, int]]:
    raw = request.cookies.get(PLAY_COOKIE)
    if not raw:
        return None
    try:
        data = play_serializer.loads(raw, max_age=PLAY_MAX_AGE)
        return int(data["a"]), int(data["q"])
    except (BadSignature, SignatureExpired, KeyError, TypeError, ValueError):
        return None


def set_play_cookie(response: Response, attempt_id: int, quiz_id: int):
    response.set_cookie(
        PLAY_COOKIE,
        play_cookie_value(attempt_id, quiz_id),
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=PLAY_MAX_AGE,
        path="/",
    )


def clear_play_cookie(response: Response):
    response.delete_cookie(PLAY_COOKIE, path="/")


def live_cookie_value(quiz_id: int, attempt_id: int) -> str:
    return live_serializer.dumps({"q": quiz_id, "a": attempt_id})


def read_live_cookie(request: Request) -> Optional[tuple[int, int]]:
    raw = request.cookies.get(LIVE_COOKIE)
    if not raw:
        return None
    try:
        data = live_serializer.loads(raw, max_age=PLAY_MAX_AGE)
        return int(data["q"]), int(data["a"])
    except (BadSignature, SignatureExpired, KeyError, TypeError, ValueError):
        return None


def set_live_cookie(response: Response, quiz_id: int, attempt_id: int):
    response.set_cookie(
        LIVE_COOKIE,
        live_cookie_value(quiz_id, attempt_id),
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=PLAY_MAX_AGE,
        path="/",
    )


class LiveHub:
    def __init__(self):
        self.hosts: dict[str, set[WebSocket]] = defaultdict(set)
        self.players: dict[str, dict[int, set[WebSocket]]] = defaultdict(lambda: defaultdict(set))
        self.timer_tasks: dict[str, asyncio.Task] = {}

    async def connect_host(self, pin: str, ws: WebSocket):
        await ws.accept()
        self.hosts[pin].add(ws)

    async def connect_player(self, pin: str, attempt_id: int, ws: WebSocket):
        await ws.accept()
        self.players[pin][attempt_id].add(ws)

    def disconnect_host(self, pin: str, ws: WebSocket):
        self.hosts[pin].discard(ws)

    def disconnect_player(self, pin: str, attempt_id: int, ws: WebSocket):
        if pin in self.players and attempt_id in self.players[pin]:
            self.players[pin][attempt_id].discard(ws)

    async def send_hosts(self, pin: str, payload: dict):
        if not pin:
            return
        dead = []
        for ws in self.hosts.get(pin, set()):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect_host(pin, ws)

    async def send_players(self, pin: str, payload: dict):
        if not pin:
            return
        dead: list[tuple[int, WebSocket]] = []
        for attempt_id, sockets in self.players.get(pin, {}).items():
            for ws in sockets:
                try:
                    await ws.send_json(payload)
                except Exception:
                    dead.append((attempt_id, ws))
        for attempt_id, ws in dead:
            self.disconnect_player(pin, attempt_id, ws)

    def reset_timer(self, pin: str):
        task = self.timer_tasks.get(pin)
        if task and not task.done():
            task.cancel()


live_hub = LiveHub()


def as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


async def sorted_questions(db: AsyncSession, quiz_id: int) -> list[Question]:
    r = await db.execute(
        select(Question).where(Question.quiz_id == quiz_id).order_by(Question.order, Question.id)
    )
    return list(r.scalars().all())


async def load_quiz_by_code(db: AsyncSession, code: str) -> Optional[Quiz]:
    r = await db.execute(select(Quiz).where(Quiz.published_link == code))
    return r.scalar_one_or_none()


async def ensure_question_deadline(
    db: AsyncSession, attempt: Attempt, quiz: Quiz, question_index: int, question: Question
) -> datetime:
    now = datetime.now(timezone.utc)
    if quiz.timer_mode == TimerMode.whole_quiz:
        if not attempt.quiz_deadline_at:
            attempt.quiz_deadline_at = now + timedelta(seconds=quiz.timer_duration)
            await db.commit()
        d = as_utc(attempt.quiz_deadline_at)
        return d if d is not None else now
    dur = question.timer_override if question.timer_override else quiz.timer_duration
    r = await db.execute(
        select(AttemptQuestionDeadline).where(
            AttemptQuestionDeadline.attempt_id == attempt.id,
            AttemptQuestionDeadline.question_index == question_index,
        )
    )
    row = r.scalar_one_or_none()
    if row:
        d = as_utc(row.deadline_at)
        return d if d is not None else now
    deadline = now + timedelta(seconds=dur)
    db.add(AttemptQuestionDeadline(attempt_id=attempt.id, question_index=question_index, deadline_at=deadline))
    await db.commit()
    return deadline


def compute_remaining_seconds(now: datetime, deadline: datetime) -> float:
    d = as_utc(deadline)
    n = as_utc(now) or now
    if d is None:
        return 0.0
    return max(0.0, (d - n).total_seconds())


async def check_time_valid(
    db: AsyncSession, attempt: Attempt, quiz: Quiz, question_index: int, question: Question
) -> tuple[bool, datetime]:
    now = datetime.now(timezone.utc)
    if quiz.timer_mode == TimerMode.whole_quiz:
        if not attempt.quiz_deadline_at:
            return True, now
        dl = as_utc(attempt.quiz_deadline_at)
        if dl is None:
            return True, now
        return now <= dl, dl
    r = await db.execute(
        select(AttemptQuestionDeadline).where(
            AttemptQuestionDeadline.attempt_id == attempt.id,
            AttemptQuestionDeadline.question_index == question_index,
        )
    )
    row = r.scalar_one_or_none()
    if not row:
        return True, now
    dl = as_utc(row.deadline_at)
    if dl is None:
        return True, now
    return now <= dl, dl


async def score_answer(
    db: AsyncSession, question: Question, selected_ids: Optional[list], text_val: Optional[str]
) -> tuple[bool, int]:
    opts = list(
        (await db.execute(select(AnswerOption).where(AnswerOption.question_id == question.id))).scalars().all()
    )
    pts = question.points
    if question.question_type == QuestionType.text_input:
        correct_texts = [normalize_text(o.text) for o in opts if o.is_correct]
        if not correct_texts:
            return False, 0
        u = normalize_text(text_val or "")
        ok = u in correct_texts
        return ok, pts if ok else 0
    correct_ids = {o.id for o in opts if o.is_correct}
    sel = set(selected_ids or [])
    if question.question_type == QuestionType.single_choice:
        ok = sel == correct_ids and len(correct_ids) >= 1
        return ok, pts if ok else 0
    ok = sel == correct_ids and len(correct_ids) > 0
    return ok, pts if ok else 0


async def ensure_unique_live_pin(db: AsyncSession) -> str:
    for _ in range(30):
        pin = generate_live_pin()
        r = await db.execute(select(Quiz).where(Quiz.live_pin == pin))
        if not r.scalar_one_or_none():
            return pin
    raise HTTPException(500, "live_pin_generation_failed")


def quiz_live_state(quiz: Quiz, total_questions: int) -> str:
    if quiz.live_finished_at:
        return "finished"
    if quiz.live_started_at:
        return "running"
    return "waiting"


def live_next_question_deadline(quiz: Quiz, question: Question, now: datetime) -> datetime:
    sec = question.timer_override if question.timer_override is not None else quiz.timer_duration
    end = now + timedelta(seconds=int(sec))
    if quiz.timer_mode == TimerMode.whole_quiz and quiz.live_quiz_deadline_at:
        cap = as_utc(quiz.live_quiz_deadline_at)
        if cap is not None and end > cap:
            end = cap
    return end


async def live_connected_count(db: AsyncSession, quiz_id: int) -> int:
    r = await db.execute(
        select(func.count()).select_from(Attempt).where(Attempt.quiz_id == quiz_id, Attempt.finished_at.is_(None))
    )
    return int(r.scalar() or 0)


async def live_answer_stats(db: AsyncSession, question_id: int) -> dict[str, int]:
    r = await db.execute(select(UserAnswer).where(UserAnswer.question_id == question_id))
    dist: dict[str, int] = defaultdict(int)
    for ua in r.scalars().all():
        if ua.selected_options:
            for oid in ua.selected_options:
                dist[str(oid)] += 1
        elif ua.text_answer:
            dist[ua.text_answer] += 1
    return dict(dist)


async def live_results_payload(db: AsyncSession, quiz: Quiz, attempt: Attempt) -> dict:
    qs = await sorted_questions(db, quiz.id)
    ua_r = await db.execute(select(UserAnswer).where(UserAnswer.attempt_id == attempt.id))
    answers = list(ua_r.scalars().all())
    rows = []
    for q in qs:
        ua = next((x for x in answers if x.question_id == q.id), None)
        await db.refresh(q, ["options"])
        your = "—"
        if ua:
            if q.question_type == QuestionType.text_input:
                your = (ua.text_answer or "").strip() or "—"
            elif ua.selected_options:
                id2t = {o.id: o.text for o in q.options}
                parts = []
                for oid in ua.selected_options:
                    try:
                        oi = int(oid)
                    except (TypeError, ValueError):
                        oi = oid
                    parts.append(id2t.get(oi, str(oid)))
                your = ", ".join(parts)
            else:
                your = "—"
        rows.append({"question": q.text, "is_correct": bool(ua and ua.is_correct), "your_answer": your})
    pct = round(100 * attempt.score / attempt.max_score, 1) if attempt.max_score else 0
    return {
        "score": attempt.score,
        "max_score": attempt.max_score,
        "pct": pct,
        "rows": rows,
    }

@asynccontextmanager
async def app_lifespan(app: FastAPI):
    await init_db()
    await migrate_schema()
    async with SessionLocal() as db:
        await ensure_admin_user(db)
    yield


app = FastAPI(title="QuizLab", lifespan=app_lifespan)


def prefers_json(request: Request) -> bool:
    path = request.url.path
    accept = (request.headers.get("accept") or "").lower()
    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        return True
    if "application/json" in accept and "text/html" not in accept:
        return True
    if request.headers.get("x-requested-with", "").lower() == "xmlhttprequest":
        return True
    if path.endswith("/sync") or path.endswith("/state"):
        return True
    if path.endswith("/start") or path.endswith("/next") or path.endswith("/finish"):
        return True
    if path.endswith("/answer"):
        return True
    return False


async def render_error_page(request: Request, status_code: int, detail: str):
    if prefers_json(request):
        return JSONResponse({"detail": detail}, status_code=status_code)
    user = None
    try:
        async with SessionLocal() as db:
            user = await get_current_user(request, db)
    except Exception:
        user = None
    title = "Страница не найдена" if status_code == 404 else "Что-то пошло не так"
    return templates.TemplateResponse(
        "error.html",
        {
            "request": request,
            "user": user,
            "status_code": status_code,
            "title": title,
            "detail": detail,
        },
        status_code=status_code,
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    detail = str(exc.detail) if exc.detail else "Ошибка обработки запроса."
    return await render_error_page(request, exc.status_code, detail)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return await render_error_page(request, 422, "Проверьте корректность заполненных данных.")


@app.exception_handler(404)
async def not_found_handler(request: Request, exc: Exception):
    return await render_error_page(request, 404, "Такой страницы не существует или она была перемещена.")


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    return await render_error_page(request, 500, "Внутренняя ошибка сервера. Попробуйте еще раз.")


@app.middleware("http")
async def base_url_middleware(request: Request, call_next):
    request.state.base_url = get_base_url(request).rstrip("/")
    return await call_next(request)


@app.middleware("http")
async def auth_refresh_middleware(request: Request, call_next):
    if request.url.path.startswith("/static"):
        return await call_next(request)
    response = await call_next(request)
    if response.status_code >= 400:
        return response
    if request.cookies.get("access_token"):
        return response
    refresh = request.cookies.get("refresh_token")
    if refresh and request.url.path not in (
        "/login",
        "/register",
        "/logout",
        "/forgot-password",
        "/reset-password",
        "/quizzes",
        "/",
    ):
        p = decode_token(refresh)
        if p and p.get("type") == "refresh":
            async with SessionLocal() as db:
                r = await db.execute(select(User).where(User.id == int(p["sub"])))
                user = r.scalar_one_or_none()
                if user:
                    access = create_token(
                        {"sub": str(user.id), "type": "access"}, timedelta(minutes=ACCESS_EXPIRE_MINUTES)
                    )
                    response.set_cookie(
                        "access_token",
                        access,
                        httponly=True,
                        secure=False,
                        samesite="lax",
                        max_age=ACCESS_EXPIRE_MINUTES * 60,
                        path="/",
                    )
    return response


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, db: DbDep):
    user = await get_current_user(request, db)
    fq = (
        await db.execute(
            select(Quiz)
            .where(Quiz.featured == True, Quiz.status == QuizStatus.published)
            .options(selectinload(Quiz.author))
        )
    ).scalars().all()
    scored = []
    for q in fq:
        cnt = (
            await db.execute(
                select(func.count())
                .select_from(Attempt)
                .where(Attempt.quiz_id == q.id, Attempt.finished_at.isnot(None))
            )
        ).scalar() or 0
        scored.append((q, cnt))
    scored.sort(key=lambda x: -x[1])
    top3 = scored[:3]
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "user": user, "top_quizzes": top3},
    )


@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request, db: DbDep):
    if await get_current_user(request, db):
        return RedirectResponse("/dashboard", status_code=302)
    return templates.TemplateResponse("register.html", {"request": request, "errors": {}})


@app.post("/register", response_class=HTMLResponse)
async def register_post(
    request: Request,
    db: DbDep,
    email: str = Form(""),
    password: str = Form(""),
    password2: str = Form(""),
):
    errors: dict[str, str] = {}
    try:
        email_norm = ev_validate_email(email, check_deliverability=False).normalized
    except EmailNotValidError:
        errors["email"] = "Укажите корректный email"
        email_norm = email.strip().lower()
    if len(password) < 8:
        errors["password"] = "Пароль не короче 8 символов"
    if password != password2:
        errors["password2"] = "Пароли не совпадают"
    if errors:
        return templates.TemplateResponse(
            "register.html", {"request": request, "errors": errors, "email": email}, status_code=400
        )
    ex = await db.execute(select(User).where(User.email == email_norm))
    if ex.scalar_one_or_none():
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "errors": {"email": "Такой email уже зарегистрирован"}, "email": email},
            status_code=400,
        )
    user = User(email=email_norm, password_hash=hash_password(password), role="user")
    db.add(user)
    await db.commit()
    await db.refresh(user)
    resp = RedirectResponse("/dashboard", status_code=302)
    set_auth_cookies(resp, user.id)
    return resp


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, db: DbDep):
    if await get_current_user(request, db):
        return RedirectResponse("/dashboard", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "errors": {}})


@app.post("/login", response_class=HTMLResponse)
async def login_post(request: Request, db: DbDep, email: str = Form(""), password: str = Form("")):
    errors: dict[str, str] = {}
    try:
        email_norm = ev_validate_email(email, check_deliverability=False).normalized
    except EmailNotValidError:
        email_norm = email.strip().lower()
        errors["email"] = "Некорректный email"
    r = await db.execute(select(User).where(User.email == email_norm))
    user = r.scalar_one_or_none()
    if not user or not verify_password(password, user.password_hash):
        errors["form"] = "Неверный email или пароль"
        return templates.TemplateResponse(
            "login.html", {"request": request, "errors": errors, "email": email}, status_code=400
        )
    if not (user.password_hash or "").startswith(f"{PBKDF2_PREFIX}$"):
        user.password_hash = hash_password(password)
        await db.commit()
    resp = RedirectResponse("/dashboard", status_code=302)
    set_auth_cookies(resp, user.id)
    return resp


@app.get("/logout")
async def logout():
    resp = RedirectResponse("/", status_code=302)
    clear_auth_cookies(resp)
    clear_play_cookie(resp)
    return resp


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: DbDep, user: UserDep):
    r = await db.execute(select(Quiz).where(Quiz.author_id == user.id).order_by(Quiz.created_at.desc()))
    quizzes = list(r.scalars().all())
    month_start = datetime.now(timezone.utc) - timedelta(days=30)
    cards = []
    for q in quizzes:
        qc = await db.execute(select(func.count()).select_from(Question).where(Question.quiz_id == q.id))
        nq = qc.scalar() or 0
        ac = await db.execute(
            select(func.count()).select_from(Attempt).where(Attempt.quiz_id == q.id, Attempt.finished_at.isnot(None))
        )
        na = ac.scalar() or 0
        avg_r = await db.execute(
            select(func.avg(Attempt.score)).where(Attempt.quiz_id == q.id, Attempt.finished_at.isnot(None))
        )
        avg = avg_r.scalar()
        cards.append(
            {
                "quiz": q,
                "n_questions": nq,
                "n_attempts": na,
                "avg_score": round(float(avg), 2) if avg is not None else None,
            }
        )
    mc = await db.execute(
        select(func.count())
        .select_from(Attempt)
        .join(Quiz, Attempt.quiz_id == Quiz.id)
        .where(Quiz.author_id == user.id, Attempt.finished_at.isnot(None), Attempt.finished_at >= month_start)
    )
    month_attempts = mc.scalar() or 0
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "cards": cards,
            "month_attempts": month_attempts,
        },
    )


@app.post("/quiz/create")
async def quiz_create(
    request: Request,
    db: DbDep,
    user: UserDep,
    title: str = Form(""),
    description: str = Form(""),
    timer_mode: str = Form("per_question"),
    timer_duration: int = Form(30),
    show_correct_after_question: Optional[str] = Form(None),
    show_final_results: Optional[str] = Form(None),
    access_type: str = Form("public"),
    access_password: str = Form(""),
    is_live: Optional[str] = Form(None),
):
    title = title.strip()
    if not title:
        return templates.TemplateResponse(
            "create_quiz.html",
            {
                "request": request,
                "user": user,
                "errors": {"title": "Введите название"},
                "form": {},
            },
            status_code=400,
        )
    tm = TimerMode(timer_mode) if timer_mode in TimerMode._value2member_map_ else TimerMode.per_question
    at = AccessType(access_type) if access_type in AccessType._value2member_map_ else AccessType.public
    td = max(5, min(300, int(timer_duration)))
    quiz = Quiz(
        title=title,
        description=description.strip(),
        timer_mode=tm,
        timer_duration=td,
        show_correct_after_question=bool(show_correct_after_question),
        show_final_results=bool(show_final_results),
        status=QuizStatus.draft,
        access_type=at,
        access_password=access_password.strip() if at == AccessType.password else None,
        is_live=bool(is_live),
        author_id=user.id,
    )
    db.add(quiz)
    await db.commit()
    await db.refresh(quiz)
    return RedirectResponse(f"/quiz/{quiz.id}/edit", status_code=302)


@app.get("/quiz/create", response_class=HTMLResponse)
async def quiz_create_page(request: Request, db: DbDep, user: UserDep):
    return templates.TemplateResponse(
        "create_quiz.html", {"request": request, "user": user, "quiz": None, "errors": {}, "form": {}}
    )


@app.get("/quiz/{quiz_id}/edit", response_class=HTMLResponse)
async def quiz_edit(request: Request, quiz_id: int, db: DbDep, user: UserDep):
    r = await db.execute(
        select(Quiz)
        .where(Quiz.id == quiz_id, Quiz.author_id == user.id)
        .options(selectinload(Quiz.questions).selectinload(Question.options))
    )
    quiz = r.scalar_one_or_none()
    if not quiz:
        raise HTTPException(404)
    qs = sorted(quiz.questions, key=lambda x: (x.order, x.id))
    return templates.TemplateResponse(
        "create_quiz.html",
        {
            "request": request,
            "user": user,
            "quiz": quiz,
            "questions": qs,
            "errors": {},
            "form": {},
            "published_qr": make_qr_data_uri(f"{request.state.base_url}/play/{quiz.published_link}")
            if quiz.published_link
            else None,
        },
    )


@app.post("/quiz/{quiz_id}/update", response_class=HTMLResponse)
async def quiz_update(
    request: Request,
    quiz_id: int,
    db: DbDep,
    user: UserDep,
    title: str = Form(""),
    description: str = Form(""),
    timer_mode: str = Form("per_question"),
    timer_duration: int = Form(30),
    show_correct_after_question: Optional[str] = Form(None),
    show_final_results: Optional[str] = Form(None),
    access_type: str = Form("public"),
    access_password: str = Form(""),
    is_live: Optional[str] = Form(None),
):
    r = await db.execute(select(Quiz).where(Quiz.id == quiz_id, Quiz.author_id == user.id))
    quiz = r.scalar_one_or_none()
    if not quiz:
        raise HTTPException(404)
    title = title.strip()
    errors = {}
    if not title:
        errors["title"] = "Введите название"
    if errors:
        await db.refresh(quiz)
        qs = await sorted_questions(db, quiz.id)
        for q in qs:
            await db.refresh(q, ["options"])
        return templates.TemplateResponse(
            "create_quiz.html",
            {"request": request, "user": user, "quiz": quiz, "questions": qs, "errors": errors, "form": {}},
            status_code=400,
        )
    quiz.title = title
    quiz.description = description.strip()
    quiz.timer_mode = TimerMode(timer_mode) if timer_mode in TimerMode._value2member_map_ else TimerMode.per_question
    quiz.timer_duration = max(5, min(300, int(timer_duration)))
    quiz.show_correct_after_question = bool(show_correct_after_question)
    quiz.show_final_results = bool(show_final_results)
    quiz.access_type = AccessType(access_type) if access_type in AccessType._value2member_map_ else AccessType.public
    if quiz.access_type == AccessType.password:
        quiz.access_password = access_password.strip() or quiz.access_password
    else:
        quiz.access_password = None
    quiz.is_live = bool(is_live)
    await db.commit()
    return RedirectResponse(f"/quiz/{quiz_id}/edit", status_code=302)


@app.post("/quiz/{quiz_id}/duplicate")
async def quiz_duplicate(quiz_id: int, db: DbDep, user: UserDep):
    r = await db.execute(
        select(Quiz).where(Quiz.id == quiz_id, Quiz.author_id == user.id).options(selectinload(Quiz.questions))
    )
    src = r.scalar_one_or_none()
    if not src:
        raise HTTPException(404)
    nq = Quiz(
        title=src.title + " (копия)",
        description=src.description,
        timer_mode=src.timer_mode,
        timer_duration=src.timer_duration,
        show_correct_after_question=src.show_correct_after_question,
        show_final_results=src.show_final_results,
        status=QuizStatus.draft,
        access_type=src.access_type,
        access_password=src.access_password,
        is_live=src.is_live,
        author_id=user.id,
    )
    db.add(nq)
    await db.flush()
    for old in sorted(src.questions, key=lambda x: (x.order, x.id)):
        opts = (
            await db.execute(select(AnswerOption).where(AnswerOption.question_id == old.id))
        ).scalars().all()
        qq = Question(
            quiz_id=nq.id,
            text=old.text,
            question_type=old.question_type,
            points=old.points,
            timer_override=old.timer_override,
            explanation=old.explanation,
            order=old.order,
        )
        db.add(qq)
        await db.flush()
        for o in opts:
            db.add(AnswerOption(question_id=qq.id, text=o.text, is_correct=o.is_correct))
    await db.commit()
    await db.refresh(nq)
    return RedirectResponse(f"/quiz/{nq.id}/edit", status_code=302)


@app.post("/quiz/{quiz_id}/delete")
async def quiz_delete(quiz_id: int, db: DbDep, user: UserDep):
    r = await db.execute(select(Quiz).where(Quiz.id == quiz_id, Quiz.author_id == user.id))
    quiz = r.scalar_one_or_none()
    if not quiz:
        raise HTTPException(404)
    await db.delete(quiz)
    await db.commit()
    return RedirectResponse("/dashboard", status_code=302)


@app.post("/quiz/{quiz_id}/questions/add")
async def question_add(
    request: Request,
    quiz_id: int,
    db: DbDep,
    user: UserDep,
    text: str = Form(""),
    question_type: str = Form("single_choice"),
    points: int = Form(1),
    timer_override: str = Form(""),
    explanation: str = Form(""),
    option_texts: list[str] = Form([]),
    correct_single: str = Form(""),
    correct_multi: list[str] = Form([]),
    correct_text: str = Form(""),
    alt_texts: list[str] = Form([]),
):
    r = await db.execute(select(Quiz).where(Quiz.id == quiz_id, Quiz.author_id == user.id))
    quiz = r.scalar_one_or_none()
    if not quiz:
        raise HTTPException(404)
    text = text.strip()
    errors = {}
    if not text:
        errors["text"] = "Введите текст вопроса"
    qt = (
        QuestionType(question_type)
        if question_type in QuestionType._value2member_map_
        else QuestionType.single_choice
    )
    pts = max(1, min(100, int(points)))
    to_val = None
    if timer_override.strip().isdigit():
        to_val = max(5, min(300, int(timer_override.strip())))
    if qt == QuestionType.text_input and not correct_text.strip():
        errors["correct"] = "Укажите правильный ответ"
    mx = await db.execute(select(func.max(Question.order)).where(Question.quiz_id == quiz_id))
    next_ord = (mx.scalar() or 0) + 1
    if errors:
        qs = await sorted_questions(db, quiz_id)
        for qq in qs:
            await db.refresh(qq, ["options"])
        return templates.TemplateResponse(
            "create_quiz.html",
            {"request": request, "user": user, "quiz": quiz, "questions": qs, "errors": errors, "form": {}},
            status_code=400,
        )
    q = Question(
        quiz_id=quiz_id,
        text=text,
        question_type=qt,
        points=pts,
        timer_override=to_val,
        explanation=explanation.strip() or None,
        order=next_ord,
    )
    db.add(q)
    await db.flush()
    if qt == QuestionType.text_input:
        db.add(AnswerOption(question_id=q.id, text=correct_text.strip(), is_correct=True))
        for alt in alt_texts:
            alt = alt.strip()
            if alt:
                db.add(AnswerOption(question_id=q.id, text=alt, is_correct=True))
    else:
        cleaned = [t.strip() for t in option_texts if t.strip()]
        if len(cleaned) < 2 or len(cleaned) > 6:
            await db.delete(q)
            await db.commit()
            qs = await sorted_questions(db, quiz_id)
            for qq in qs:
                await db.refresh(qq, ["options"])
            return templates.TemplateResponse(
                "create_quiz.html",
                {
                    "request": request,
                    "user": user,
                    "quiz": quiz,
                    "questions": qs,
                    "errors": {"options": "От 2 до 6 вариантов ответа"},
                    "form": {},
                },
                status_code=400,
            )
        corr_set = set()
        if qt == QuestionType.single_choice and correct_single.isdigit():
            corr_set.add(int(correct_single))
        for c in correct_multi:
            if c.isdigit():
                corr_set.add(int(c))
        if qt == QuestionType.single_choice and len(corr_set) != 1:
            await db.delete(q)
            await db.commit()
            qs = await sorted_questions(db, quiz_id)
            for qq in qs:
                await db.refresh(qq, ["options"])
            return templates.TemplateResponse(
                "create_quiz.html",
                {
                    "request": request,
                    "user": user,
                    "quiz": quiz,
                    "questions": qs,
                    "errors": {"correct": "Выберите один правильный вариант"},
                    "form": {},
                },
                status_code=400,
            )
        if qt == QuestionType.multiple_choice and len(corr_set) < 1:
            await db.delete(q)
            await db.commit()
            qs = await sorted_questions(db, quiz_id)
            for qq in qs:
                await db.refresh(qq, ["options"])
            return templates.TemplateResponse(
                "create_quiz.html",
                {
                    "request": request,
                    "user": user,
                    "quiz": quiz,
                    "questions": qs,
                    "errors": {"correct": "Отметьте хотя бы один правильный вариант"},
                    "form": {},
                },
                status_code=400,
            )
        for idx, ot in enumerate(cleaned):
            oid = idx + 1
            db.add(AnswerOption(question_id=q.id, text=ot, is_correct=(oid in corr_set)))
    await db.commit()
    return RedirectResponse(f"/quiz/{quiz_id}/edit", status_code=302)


@app.post("/quiz/{quiz_id}/questions/{qid}/update")
async def question_update(
    request: Request,
    quiz_id: int,
    qid: int,
    db: DbDep,
    user: UserDep,
    text: str = Form(""),
    question_type: str = Form("single_choice"),
    points: int = Form(1),
    timer_override: str = Form(""),
    explanation: str = Form(""),
    option_texts: list[str] = Form([]),
    correct_single: str = Form(""),
    correct_multi: list[str] = Form([]),
    correct_text: str = Form(""),
    alt_texts: list[str] = Form([]),
):
    r = await db.execute(
        select(Question).join(Quiz).where(Question.id == qid, Quiz.id == quiz_id, Quiz.author_id == user.id)
    )
    q = r.scalar_one_or_none()
    if not q:
        raise HTTPException(404)
    r2 = await db.execute(select(Quiz).where(Quiz.id == quiz_id))
    quiz = r2.scalar_one()
    text = text.strip()
    if not text:
        qs = await sorted_questions(db, quiz_id)
        for qq in qs:
            await db.refresh(qq, ["options"])
        return templates.TemplateResponse(
            "create_quiz.html",
            {
                "request": request,
                "user": user,
                "quiz": quiz,
                "questions": qs,
                "errors": {"text": "Введите текст вопроса"},
                "form": {},
            },
            status_code=400,
        )
    q.text = text
    q.question_type = (
        QuestionType(question_type)
        if question_type in QuestionType._value2member_map_
        else QuestionType.single_choice
    )
    q.points = max(1, min(100, int(points)))
    q.timer_override = None
    if timer_override.strip().isdigit():
        q.timer_override = max(5, min(300, int(timer_override.strip())))
    q.explanation = explanation.strip() or None
    await db.execute(AnswerOption.__table__.delete().where(AnswerOption.question_id == q.id))
    if q.question_type == QuestionType.text_input:
        db.add(AnswerOption(question_id=q.id, text=correct_text.strip(), is_correct=True))
        for alt in alt_texts:
            alt = alt.strip()
            if alt:
                db.add(AnswerOption(question_id=q.id, text=alt, is_correct=True))
    else:
        cleaned = [t.strip() for t in option_texts if t.strip()]
        if len(cleaned) < 2 or len(cleaned) > 6:
            qs = await sorted_questions(db, quiz_id)
            for qq in qs:
                await db.refresh(qq, ["options"])
            return templates.TemplateResponse(
                "create_quiz.html",
                {
                    "request": request,
                    "user": user,
                    "quiz": quiz,
                    "questions": qs,
                    "errors": {"options": "От 2 до 6 вариантов ответа"},
                    "form": {},
                },
                status_code=400,
            )
        corr_set = set()
        if q.question_type == QuestionType.single_choice and correct_single.isdigit():
            corr_set.add(int(correct_single))
        for c in correct_multi:
            if c.isdigit():
                corr_set.add(int(c))
        if q.question_type == QuestionType.single_choice and len(corr_set) != 1:
            qs = await sorted_questions(db, quiz_id)
            for qq in qs:
                await db.refresh(qq, ["options"])
            return templates.TemplateResponse(
                "create_quiz.html",
                {
                    "request": request,
                    "user": user,
                    "quiz": quiz,
                    "questions": qs,
                    "errors": {"correct": "Выберите один правильный вариант"},
                    "form": {},
                },
                status_code=400,
            )
        if q.question_type == QuestionType.multiple_choice and len(corr_set) < 1:
            qs = await sorted_questions(db, quiz_id)
            for qq in qs:
                await db.refresh(qq, ["options"])
            return templates.TemplateResponse(
                "create_quiz.html",
                {
                    "request": request,
                    "user": user,
                    "quiz": quiz,
                    "questions": qs,
                    "errors": {"correct": "Отметьте хотя бы один правильный вариант"},
                    "form": {},
                },
                status_code=400,
            )
        for idx, ot in enumerate(cleaned):
            oid = idx + 1
            db.add(AnswerOption(question_id=q.id, text=ot, is_correct=(oid in corr_set)))
    await db.commit()
    return RedirectResponse(f"/quiz/{quiz_id}/edit", status_code=302)


@app.post("/quiz/{quiz_id}/questions/{qid}/delete")
async def question_delete(quiz_id: int, qid: int, db: DbDep, user: UserDep):
    r = await db.execute(
        select(Question).join(Quiz).where(Question.id == qid, Quiz.id == quiz_id, Quiz.author_id == user.id)
    )
    q = r.scalar_one_or_none()
    if not q:
        raise HTTPException(404)
    await db.delete(q)
    await db.commit()
    qs = await sorted_questions(db, quiz_id)
    for i, qq in enumerate(qs, start=1):
        qq.order = i
    await db.commit()
    return RedirectResponse(f"/quiz/{quiz_id}/edit", status_code=302)


@app.post("/quiz/{quiz_id}/questions/reorder")
async def questions_reorder(quiz_id: int, db: DbDep, user: UserDep, request: Request):
    r = await db.execute(select(Quiz).where(Quiz.id == quiz_id, Quiz.author_id == user.id))
    if not r.scalar_one_or_none():
        raise HTTPException(404)
    body = await request.json()
    order_ids = body.get("order", [])
    if not isinstance(order_ids, list):
        raise HTTPException(400)
    for pos, qid in enumerate(order_ids, start=1):
        rr = await db.execute(
            select(Question).where(Question.id == int(qid), Question.quiz_id == quiz_id)
        )
        qq = rr.scalar_one_or_none()
        if qq:
            qq.order = pos
    await db.commit()
    return {"ok": True}


@app.get("/quiz/{quiz_id}/publish")
async def quiz_publish(quiz_id: int, db: DbDep, user: UserDep):
    r = await db.execute(select(Quiz).where(Quiz.id == quiz_id, Quiz.author_id == user.id))
    quiz = r.scalar_one_or_none()
    if not quiz:
        raise HTTPException(404)
    qc = await db.execute(select(func.count()).select_from(Question).where(Question.quiz_id == quiz.id))
    if (qc.scalar() or 0) < 1:
        return RedirectResponse(f"/quiz/{quiz_id}/edit?err=no_questions", status_code=302)
    for _ in range(20):
        code = generate_link_code()
        ex = await db.execute(select(Quiz).where(Quiz.published_link == code))
        if not ex.scalar_one_or_none():
            quiz.published_link = code
            quiz.status = QuizStatus.published
            await db.commit()
            return RedirectResponse(f"/quiz/{quiz_id}/edit?published=1", status_code=302)
    raise HTTPException(500)


@app.get("/quiz/{quiz_id}/close")
async def quiz_close(quiz_id: int, db: DbDep, user: UserDep):
    r = await db.execute(select(Quiz).where(Quiz.id == quiz_id, Quiz.author_id == user.id))
    quiz = r.scalar_one_or_none()
    if not quiz:
        raise HTTPException(404)
    quiz.status = QuizStatus.closed
    await db.commit()
    return RedirectResponse(f"/quiz/{quiz_id}/edit", status_code=302)


@app.get("/quiz/{quiz_id}/stats", response_class=HTMLResponse)
async def quiz_stats_page(request: Request, quiz_id: int, db: DbDep, user: UserDep):
    r = await db.execute(select(Quiz).where(Quiz.id == quiz_id, Quiz.author_id == user.id))
    quiz = r.scalar_one_or_none()
    if not quiz:
        raise HTTPException(404)
    qs = await sorted_questions(db, quiz_id)
    for qq in qs:
        await db.refresh(qq, ["options"])
    att_r = await db.execute(
        select(Attempt)
        .where(Attempt.quiz_id == quiz_id, Attempt.finished_at.isnot(None))
        .order_by(Attempt.finished_at.desc())
    )
    attempts = list(att_r.scalars().all())
    scores = [a.score for a in attempts]
    avg = statistics.mean(scores) if scores else 0
    med = statistics.median(scores) if scores else 0
    dist: dict[int, dict[str, Any]] = {}
    for qq in qs:
        opts = {str(o.id): {"text": o.text, "count": 0, "is_correct": o.is_correct} for o in qq.options}
        wrong_counts: list[tuple[str, int]] = []
        ua_r = await db.execute(select(UserAnswer).where(UserAnswer.question_id == qq.id))
        for ua in ua_r.scalars().all():
            if qq.question_type == QuestionType.text_input:
                key = ua.text_answer or ""
                opts.setdefault(
                    key, {"text": key or "(пусто)", "count": 0, "is_correct": ua.is_correct}
                )
                opts[key]["count"] = opts[key].get("count", 0) + 1
            else:
                for oid in ua.selected_options or []:
                    k = str(oid)
                    if k in opts:
                        opts[k]["count"] += 1
                if not ua.is_correct and qq.question_type != QuestionType.text_input:
                    for oid in ua.selected_options or []:
                        o = next((x for x in qq.options if x.id == oid), None)
                        if o and not o.is_correct:
                            wrong_counts.append((o.text, 1))
        popular_wrong = None
        maxw = 0
        if qq.question_type != QuestionType.text_input:
            for oid, info in opts.items():
                if not info["is_correct"] and info["count"] > maxw:
                    maxw = info["count"]
                    popular_wrong = info["text"]
        dist[qq.id] = {"options": list(opts.values()), "popular_wrong": popular_wrong}
    hardest = []
    for qq in qs:
        ua_r = await db.execute(
            select(func.count()).select_from(UserAnswer).where(UserAnswer.question_id == qq.id, UserAnswer.is_correct == False)
        )
        wrong_n = ua_r.scalar() or 0
        hardest.append({"question": qq, "wrong": wrong_n})
    hardest.sort(key=lambda x: -x["wrong"])
    att_ids = [a.id for a in attempts]
    ua_list: list[UserAnswer] = []
    if att_ids:
        ua_r2 = await db.execute(select(UserAnswer).where(UserAnswer.attempt_id.in_(att_ids)))
        ua_list = list(ua_r2.scalars().all())
    corr_n = sum(1 for x in ua_list if x.is_correct)
    wrong_n = len(ua_list) - corr_n
    pie_center = round(100 * corr_n / len(ua_list), 1) if ua_list else 0
    bar_labels = []
    bar_pcts = []
    for i, qq in enumerate(qs, start=1):
        rel = [x for x in ua_list if x.question_id == qq.id]
        tot = len(rel)
        c = sum(1 for x in rel if x.is_correct)
        pct = round(100 * c / tot, 1) if tot else 0
        bar_labels.append(f"№{i}")
        bar_pcts.append(pct)
    mx = sum(q.points for q in qs) if qs else 1
    hist_map: dict[int, int] = defaultdict(int)
    for a in attempts:
        hist_map[a.score] += 1
    hist_labels = [str(s) for s in range(0, mx + 1)]
    hist_counts = [hist_map.get(s, 0) for s in range(0, mx + 1)]
    chart_payload = {
        "pie_correct": corr_n,
        "pie_wrong": wrong_n,
        "pie_center": pie_center,
        "bar_labels": bar_labels,
        "bar_pcts": bar_pcts,
        "hist_labels": hist_labels,
        "hist_counts": hist_counts,
    }
    return templates.TemplateResponse(
        "stats.html",
        {
            "request": request,
            "user": user,
            "quiz": quiz,
            "attempts": attempts,
            "questions": qs,
            "avg": round(avg, 2),
            "median": round(med, 2),
            "dist": dist,
            "hardest": hardest[:5],
            "chart_json": json.dumps(chart_payload, ensure_ascii=False),
        },
    )


@app.get("/quiz/{quiz_id}/export")
async def quiz_export(quiz_id: int, db: DbDep, user: UserDep):
    r = await db.execute(select(Quiz).where(Quiz.id == quiz_id, Quiz.author_id == user.id))
    quiz = r.scalar_one_or_none()
    if not quiz:
        raise HTTPException(404)
    qs = await sorted_questions(db, quiz_id)
    max_score = sum(q.points for q in qs)
    att_r = await db.execute(
        select(Attempt).where(Attempt.quiz_id == quiz_id, Attempt.finished_at.isnot(None)).order_by(Attempt.finished_at)
    )
    attempts = list(att_r.scalars().all())
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Участник", "Дата завершения", "Баллы", "Максимум", "Процент"])
    for a in attempts:
        pct = round(100 * a.score / max_score, 1) if max_score else 0
        w.writerow(
            [
                a.participant_name or "—",
                as_utc(a.finished_at).strftime("%Y-%m-%d %H:%M") if a.finished_at else "",
                a.score,
                max_score,
                pct,
            ]
        )
    data = "\ufeff" + buf.getvalue()
    return StreamingResponse(
        iter([data.encode("utf-8")]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="quiz_{quiz_id}_results.csv"'},
    )


@app.get("/play/{code}", response_class=HTMLResponse)
async def play_landing(request: Request, code: str, db: DbDep):
    quiz = await load_quiz_by_code(db, code)
    if not quiz or quiz.status != QuizStatus.published:
        raise HTTPException(404)
    user = await get_current_user(request, db)
    return templates.TemplateResponse(
        "quiz_start.html",
        {"request": request, "quiz": quiz, "user": user, "errors": {}},
    )


@app.post("/play/{code}/start")
async def play_start(
    request: Request,
    code: str,
    db: DbDep,
    participant_name: str = Form(""),
    access_password: str = Form(""),
):
    quiz = await load_quiz_by_code(db, code)
    if not quiz or quiz.status != QuizStatus.published:
        raise HTTPException(404)
    if quiz.access_type == AccessType.password:
        if (quiz.access_password or "") != access_password.strip():
            return templates.TemplateResponse(
                "quiz_start.html",
                {
                    "request": request,
                    "quiz": quiz,
                    "user": await get_current_user(request, db),
                    "errors": {"password": "Неверный пароль"},
                },
                status_code=400,
            )
    qs = await sorted_questions(db, quiz.id)
    if not qs:
        raise HTTPException(400)
    max_score = sum(q.points for q in qs)
    now = datetime.now(timezone.utc)
    att = Attempt(
        quiz_id=quiz.id,
        participant_name=participant_name.strip()[:200],
        started_at=now,
        score=0,
        max_score=max_score,
        quiz_deadline_at=now + timedelta(seconds=quiz.timer_duration) if quiz.timer_mode == TimerMode.whole_quiz else None,
    )
    db.add(att)
    await db.commit()
    await db.refresh(att)
    resp = RedirectResponse(f"/play/{code}/question/1", status_code=302)
    set_play_cookie(resp, att.id, quiz.id)
    return resp


async def get_play_attempt(request: Request, db: AsyncSession, code: str) -> tuple[Attempt, Quiz]:
    quiz = await load_quiz_by_code(db, code)
    if not quiz:
        raise HTTPException(404)
    pair = read_play_cookie(request)
    if not pair:
        raise HTTPException(401)
    aid, qid = pair
    if qid != quiz.id:
        raise HTTPException(401)
    r = await db.execute(select(Attempt).where(Attempt.id == aid, Attempt.quiz_id == quiz.id))
    att = r.scalar_one_or_none()
    if not att or att.finished_at:
        raise HTTPException(401)
    return att, quiz


@app.get("/play/{code}/question/{n}", response_class=HTMLResponse)
async def play_question(request: Request, code: str, n: int, db: DbDep, feedback: Optional[str] = None):
    att, quiz = await get_play_attempt(request, db, code)
    if quiz.status == QuizStatus.draft:
        raise HTTPException(404)
    qs = await sorted_questions(db, quiz.id)
    if n < 1 or n > len(qs):
        raise HTTPException(404)
    answered = (
        await db.execute(select(func.count()).select_from(UserAnswer).where(UserAnswer.attempt_id == att.id))
    ).scalar() or 0
    if n <= answered:
        if answered >= len(qs):
            return RedirectResponse(f"/play/{code}/results", status_code=302)
        return RedirectResponse(f"/play/{code}/question/{answered + 1}", status_code=302)
    if n > answered + 1:
        return RedirectResponse(f"/play/{code}/question/{answered + 1}", status_code=302)
    q = qs[n - 1]
    await db.refresh(q, ["options"])
    opts = [{"id": o.id, "text": o.text} for o in sorted(q.options, key=lambda x: x.id)]
    feedback_explanation = None
    if n > 1:
        prev_q = qs[n - 2]
        if prev_q.explanation:
            prev_answer = (
                await db.execute(
                    select(UserAnswer).where(
                        UserAnswer.attempt_id == att.id,
                        UserAnswer.question_id == prev_q.id,
                    )
                )
            ).scalar_one_or_none()
            if prev_answer:
                feedback_explanation = prev_q.explanation
    deadline = await ensure_question_deadline(db, att, quiz, n, q)
    remaining = compute_remaining_seconds(datetime.now(timezone.utc), deadline)
    user = await get_current_user(request, db)
    if quiz.timer_mode == TimerMode.whole_quiz:
        timer_total = float(quiz.timer_duration)
    else:
        timer_total = float(q.timer_override if q.timer_override else quiz.timer_duration)
    return templates.TemplateResponse(
        "question_page.html",
        {
            "request": request,
            "quiz": quiz,
            "question": q,
            "options": opts,
            "index": n,
            "total": len(qs),
            "remaining_initial": remaining,
            "timer_total_seconds": max(timer_total, remaining, 1.0),
            "timer_mode": quiz.timer_mode.value,
            "feedback": feedback,
            "feedback_explanation": feedback_explanation,
            "user": user,
        },
    )


@app.post("/play/{code}/sync")
async def play_sync(request: Request, code: str, db: DbDep):
    body = await request.json()
    n = int(body.get("n", 1))
    att, quiz = await get_play_attempt(request, db, code)
    qs = await sorted_questions(db, quiz.id)
    if n < 1 or n > len(qs):
        raise HTTPException(400)
    q = qs[n - 1]
    deadline = await ensure_question_deadline(db, att, quiz, n, q)
    now = datetime.now(timezone.utc)
    rem = compute_remaining_seconds(now, deadline)
    warn = rem <= 5
    expired = rem <= 0
    return {"remaining": rem, "warn": warn, "expired": expired}


@app.post("/play/{code}/submit")
async def play_submit(request: Request, code: str, db: DbDep):
    att, quiz = await get_play_attempt(request, db, code)
    if quiz.status == QuizStatus.draft:
        raise HTTPException(404)
    form = await request.form()
    question_id = int(form.get("question_id") or 0)
    question_index = int(form.get("question_index") or 0)
    text_answer = str(form.get("text_answer") or "")
    qs = await sorted_questions(db, quiz.id)
    answered = (
        await db.execute(select(func.count()).select_from(UserAnswer).where(UserAnswer.attempt_id == att.id))
    ).scalar() or 0
    if answered >= len(qs):
        return RedirectResponse(f"/play/{code}/results", status_code=302)
    expected_idx = answered + 1
    if question_index != expected_idx:
        return RedirectResponse(f"/play/{code}/question/{expected_idx}", status_code=302)
    q = qs[expected_idx - 1]
    if q.id != question_id:
        raise HTTPException(400)
    await ensure_question_deadline(db, att, quiz, expected_idx, q)
    ex = await db.execute(
        select(UserAnswer).where(UserAnswer.attempt_id == att.id, UserAnswer.question_id == q.id)
    )
    if ex.scalar_one_or_none():
        return RedirectResponse(f"/play/{code}/question/{expected_idx}", status_code=302)
    selected: list[int] = []
    if q.question_type == QuestionType.single_choice:
        v = form.get("option_id")
        if v and str(v).isdigit():
            selected = [int(v)]
    elif q.question_type == QuestionType.multiple_choice:
        for k in form.keys():
            if k.startswith("opt_"):
                tail = k.replace("opt_", "")
                if tail.isdigit():
                    selected.append(int(tail))
        selected = sorted(set(selected))
    text_val = text_answer.strip() if q.question_type == QuestionType.text_input else None
    valid_time, _ = await check_time_valid(db, att, quiz, expected_idx, q)
    if not valid_time:
        ok, pts = False, 0
        timed_out = True
        selected = []
        text_val = None
    else:
        timed_out = False
        ok, pts = await score_answer(db, q, selected if q.question_type != QuestionType.text_input else None, text_val)
    ua = UserAnswer(
        attempt_id=att.id,
        question_id=q.id,
        selected_options=selected if q.question_type != QuestionType.text_input else None,
        text_answer=text_val if q.question_type == QuestionType.text_input else None,
        is_correct=ok and not timed_out,
    )
    if timed_out:
        ua.is_correct = False
        ua.selected_options = None
        ua.text_answer = None
    db.add(ua)
    att.score += pts if not timed_out else 0
    await db.commit()
    nxt = expected_idx + 1
    fb = None
    if quiz.show_correct_after_question:
        fb = "correct" if ok and not timed_out else "wrong"
    if nxt > len(qs):
        att.finished_at = datetime.now(timezone.utc)
        await db.commit()
        return RedirectResponse(f"/play/{code}/results", status_code=302)
    url = f"/play/{code}/question/{nxt}"
    if fb:
        url += f"?feedback={fb}"
    return RedirectResponse(url, status_code=302)


@app.get("/play/{code}/results", response_class=HTMLResponse)
async def play_results(request: Request, code: str, db: DbDep):
    quiz = await load_quiz_by_code(db, code)
    if not quiz:
        raise HTTPException(404)
    pair = read_play_cookie(request)
    if not pair:
        raise HTTPException(401)
    aid, qid = pair
    if qid != quiz.id:
        raise HTTPException(401)
    r = await db.execute(select(Attempt).where(Attempt.id == aid, Attempt.quiz_id == quiz.id))
    att = r.scalar_one_or_none()
    if not att:
        raise HTTPException(401)
    qs = await sorted_questions(db, quiz.id)
    ua_r = await db.execute(
        select(UserAnswer).where(UserAnswer.attempt_id == att.id).order_by(UserAnswer.id)
    )
    ua_list = list(ua_r.scalars().all())
    if len(ua_list) < len(qs):
        return RedirectResponse(f"/play/{code}/question/{len(ua_list) + 1}", status_code=302)
    if not att.finished_at:
        att.finished_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(att)
    rows = []
    for qq in qs:
        ua = next((x for x in ua_list if x.question_id == qq.id), None)
        rows.append({"question": qq, "answer": ua})
    pct = round(100 * att.score / att.max_score, 1) if att.max_score else 0
    user = await get_current_user(request, db)
    return templates.TemplateResponse(
        "results.html",
        {
            "request": request,
            "quiz": quiz,
            "attempt": att,
            "rows": rows,
            "pct": pct,
            "user": user,
        },
    )


@app.get("/quizzes", response_class=HTMLResponse)
async def quizzes_catalog(request: Request, db: DbDep):
    user = await get_current_user(request, db)
    r = await db.execute(
        select(Quiz)
        .where(Quiz.featured == True, Quiz.status == QuizStatus.published)
        .options(selectinload(Quiz.author))
        .order_by(Quiz.featured_at.desc().nulls_last(), Quiz.id.desc())
    )
    rows = []
    for q in r.scalars().all():
        nq = (
            await db.execute(select(func.count()).select_from(Question).where(Question.quiz_id == q.id))
        ).scalar() or 0
        na = (
            await db.execute(
                select(func.count()).select_from(Attempt).where(Attempt.quiz_id == q.id, Attempt.finished_at.isnot(None))
            )
        ).scalar() or 0
        av = (
            await db.execute(
                select(func.avg(Attempt.score)).where(Attempt.quiz_id == q.id, Attempt.finished_at.isnot(None))
            )
        ).scalar()
        rows.append(
            {
                "quiz": q,
                "n_questions": nq,
                "n_attempts": na,
                "avg_score": round(float(av), 2) if av is not None else None,
            }
        )
    return templates.TemplateResponse("quizzes.html", {"request": request, "user": user, "rows": rows})


@app.get("/quiz/{quiz_id}/host")
async def quiz_host_legacy(quiz_id: int):
    return RedirectResponse(f"/quiz/{quiz_id}/conduct", status_code=302)


@app.get("/quiz/{quiz_id}/conduct", response_class=HTMLResponse)
async def quiz_conduct(request: Request, quiz_id: int, db: DbDep):
    user = await get_current_user(request, db)
    r = await db.execute(select(Quiz).where(Quiz.id == quiz_id, Quiz.status == QuizStatus.published))
    quiz = r.scalar_one_or_none()
    if not quiz or not quiz.published_link:
        raise HTTPException(404)
    play_url = f"{request.state.base_url}/play/{quiz.published_link}"
    is_author = bool(user and user.id == quiz.author_id)
    if quiz.is_live and is_author:
        return RedirectResponse(f"/quiz/{quiz_id}/live", status_code=302)
    return templates.TemplateResponse(
        "conduct.html",
        {
            "request": request,
            "user": user,
            "quiz": quiz,
            "mode": "live_other" if quiz.is_live else "normal",
            "play_url": play_url,
            "play_qr": make_qr_data_uri(play_url),
        },
    )


@app.get("/quiz/{quiz_id}/live/open")
async def live_open_session(quiz_id: int, db: DbDep, user: UserDep):
    r = await db.execute(
        select(Quiz).where(
            Quiz.id == quiz_id,
            Quiz.author_id == user.id,
            Quiz.is_live == True,
            Quiz.status == QuizStatus.published,
        )
    )
    quiz = r.scalar_one_or_none()
    if not quiz:
        raise HTTPException(404)
    qc = await db.execute(select(func.count()).select_from(Question).where(Question.quiz_id == quiz.id))
    if (qc.scalar() or 0) < 1:
        return RedirectResponse(f"/quiz/{quiz_id}/edit?err=no_questions", status_code=302)
    att_sub = select(Attempt.id).where(Attempt.quiz_id == quiz.id, Attempt.finished_at.is_(None))
    await db.execute(delete(UserAnswer).where(UserAnswer.attempt_id.in_(att_sub)))
    await db.execute(delete(AttemptQuestionDeadline).where(AttemptQuestionDeadline.attempt_id.in_(att_sub)))
    await db.execute(delete(Attempt).where(Attempt.quiz_id == quiz.id, Attempt.finished_at.is_(None)))
    quiz.live_started_at = None
    quiz.live_finished_at = None
    quiz.live_current_index = 0
    quiz.live_question_deadline_at = None
    quiz.live_quiz_deadline_at = None
    quiz.live_pin = await ensure_unique_live_pin(db)
    await db.commit()
    return RedirectResponse(f"/quiz/{quiz_id}/live", status_code=302)


@app.get("/quiz/{quiz_id}/live", response_class=HTMLResponse)
async def live_host_page(request: Request, quiz_id: int, db: DbDep, user: UserDep):
    r = await db.execute(select(Quiz).where(Quiz.id == quiz_id, Quiz.author_id == user.id))
    quiz = r.scalar_one_or_none()
    if not quiz or not quiz.is_live:
        raise HTTPException(404)
    qs = await sorted_questions(db, quiz.id)
    join_url = f"{request.state.base_url}/join/{quiz.live_pin}" if quiz.live_pin else ""
    join_qr = make_qr_data_uri(join_url) if quiz.live_pin else None
    return templates.TemplateResponse(
        "live_host.html",
        {
            "request": request,
            "user": user,
            "quiz": quiz,
            "join_url": join_url,
            "join_qr": join_qr,
            "state": quiz_live_state(quiz, len(qs)),
            "connected_count": await live_connected_count(db, quiz.id),
            "total_questions": len(qs),
        },
    )


@app.post("/quiz/{quiz_id}/live/start")
async def live_start(quiz_id: int, db: DbDep, user: UserDep):
    r = await db.execute(select(Quiz).where(Quiz.id == quiz_id, Quiz.author_id == user.id))
    quiz = r.scalar_one_or_none()
    if not quiz or not quiz.is_live or not quiz.live_pin:
        raise HTTPException(404)
    qs = await sorted_questions(db, quiz.id)
    if not qs:
        raise HTTPException(400)
    now = datetime.now(timezone.utc)
    if quiz.timer_mode == TimerMode.whole_quiz:
        quiz.live_quiz_deadline_at = now + timedelta(seconds=int(quiz.timer_duration))
    else:
        quiz.live_quiz_deadline_at = None
    quiz.live_started_at = now
    quiz.live_finished_at = None
    quiz.live_current_index = 1
    q0 = qs[0]
    quiz.live_question_deadline_at = live_next_question_deadline(quiz, q0, now)
    await db.commit()
    pin = quiz.live_pin
    await live_hub.send_players(pin, {"type": "question", "index": 1})
    await live_hub.send_hosts(pin, {"type": "state", "state": "running", "index": 1})
    return {"ok": True}


@app.post("/quiz/{quiz_id}/live/next")
async def live_next(quiz_id: int, db: DbDep, user: UserDep):
    r = await db.execute(select(Quiz).where(Quiz.id == quiz_id, Quiz.author_id == user.id))
    quiz = r.scalar_one_or_none()
    if not quiz or not quiz.is_live or not quiz.live_pin:
        raise HTTPException(404)
    qs = await sorted_questions(db, quiz.id)
    if quiz.live_current_index >= len(qs):
        return {"ok": False}
    pin = quiz.live_pin
    quiz.live_current_index += 1
    now = datetime.now(timezone.utc)
    q = qs[quiz.live_current_index - 1]
    quiz.live_question_deadline_at = live_next_question_deadline(quiz, q, now)
    await db.commit()
    await live_hub.send_players(pin, {"type": "question", "index": quiz.live_current_index})
    await live_hub.send_hosts(pin, {"type": "state", "state": "running", "index": quiz.live_current_index})
    return {"ok": True}


@app.post("/quiz/{quiz_id}/live/finish")
async def live_finish(quiz_id: int, db: DbDep, user: UserDep):
    r = await db.execute(select(Quiz).where(Quiz.id == quiz_id, Quiz.author_id == user.id))
    quiz = r.scalar_one_or_none()
    if not quiz or not quiz.is_live or not quiz.live_pin:
        raise HTTPException(404)
    pin = quiz.live_pin
    quiz.live_finished_at = datetime.now(timezone.utc)
    await db.commit()
    atts = (
        await db.execute(select(Attempt).where(Attempt.quiz_id == quiz.id, Attempt.finished_at.is_(None)))
    ).scalars().all()
    for att in atts:
        att.finished_at = datetime.now(timezone.utc)
    await db.commit()
    await live_hub.send_players(pin, {"type": "finished"})
    await live_hub.send_hosts(pin, {"type": "state", "state": "finished"})
    return {"ok": True}


@app.get("/quiz/{quiz_id}/live/state")
async def live_host_state(quiz_id: int, db: DbDep, user: UserDep):
    r = await db.execute(select(Quiz).where(Quiz.id == quiz_id, Quiz.author_id == user.id))
    quiz = r.scalar_one_or_none()
    if not quiz:
        raise HTTPException(404)
    qs = await sorted_questions(db, quiz.id)
    state = quiz_live_state(quiz, len(qs))
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "state": state,
        "index": quiz.live_current_index,
        "total": len(qs),
        "connected": await live_connected_count(db, quiz.id),
        "answered": 0,
        "distribution": {},
        "question_text": None,
        "question_type": None,
        "options": [],
        "deadline_at": None,
        "time_expired": False,
    }
    if state == "running" and quiz.live_current_index > 0 and quiz.live_current_index <= len(qs):
        q = qs[quiz.live_current_index - 1]
        await db.refresh(q, ["options"])
        dist = await live_answer_stats(db, q.id)
        ans_r = await db.execute(select(func.count()).select_from(UserAnswer).where(UserAnswer.question_id == q.id))
        answered = int(ans_r.scalar() or 0)
        payload["answered"] = answered
        payload["distribution"] = dist
        opts_sorted = sorted(q.options, key=lambda x: x.id)
        mx = 1
        if q.question_type != QuestionType.text_input:
            counts = [dist.get(str(o.id), 0) for o in opts_sorted]
            mx = max(counts + [answered, 1])
            payload["options"] = [
                {
                    "id": o.id,
                    "text": o.text,
                    "count": dist.get(str(o.id), 0),
                    "is_correct": o.is_correct,
                    "ratio": (dist.get(str(o.id), 0) / mx) if mx else 0,
                }
                for o in opts_sorted
            ]
        else:
            pairs = sorted(dist.items(), key=lambda x: -x[1])[:16]
            mx = max([v for _, v in pairs] + [1])
            payload["options"] = [
                {"id": k, "text": (k or "(пусто)")[:200], "count": v, "is_correct": False, "ratio": v / mx if mx else 0}
                for k, v in pairs
            ]
        payload["question_text"] = q.text
        payload["question_type"] = q.question_type.value
        dl = as_utc(quiz.live_question_deadline_at)
        if dl:
            payload["deadline_at"] = dl.isoformat()
            payload["time_expired"] = now > dl
        g = as_utc(quiz.live_quiz_deadline_at)
        if quiz.timer_mode == TimerMode.whole_quiz and g and now > g:
            payload["time_expired"] = True
    return payload


@app.get("/join/{pin}", response_class=HTMLResponse)
async def live_join_page(request: Request, pin: str, db: DbDep):
    r = await db.execute(select(Quiz).where(Quiz.live_pin == pin, Quiz.is_live == True))
    quiz = r.scalar_one_or_none()
    if not quiz:
        raise HTTPException(404)
    pair = read_live_cookie(request)
    attempt = None
    if pair and pair[0] == quiz.id:
        ar = await db.execute(select(Attempt).where(Attempt.id == pair[1], Attempt.quiz_id == quiz.id))
        attempt = ar.scalar_one_or_none()
    join_closed = bool(quiz.live_started_at and not attempt)
    return templates.TemplateResponse(
        "live_join.html",
        {
            "request": request,
            "quiz": quiz,
            "attempt": attempt,
            "state": quiz_live_state(quiz, 0),
            "user": await get_current_user(request, db),
            "join_closed": join_closed,
        },
    )


@app.get("/join/{pin}/state")
async def live_join_state(request: Request, pin: str, db: DbDep):
    r = await db.execute(select(Quiz).where(Quiz.live_pin == pin, Quiz.is_live == True))
    quiz = r.scalar_one_or_none()
    if not quiz:
        raise HTTPException(404)
    pair = read_live_cookie(request)
    has_attempt = bool(pair and pair[0] == quiz.id)
    return {
        "state": quiz_live_state(quiz, 0),
        "has_attempt": has_attempt,
        "current_index": quiz.live_current_index,
        "finished": bool(quiz.live_finished_at),
    }


@app.post("/join/{pin}")
async def live_join_submit(request: Request, pin: str, db: DbDep, participant_name: str = Form("")):
    r = await db.execute(select(Quiz).where(Quiz.live_pin == pin, Quiz.is_live == True))
    quiz = r.scalar_one_or_none()
    if not quiz:
        raise HTTPException(404)
    if quiz.live_started_at:
        return templates.TemplateResponse(
            "live_join.html",
            {
                "request": request,
                "quiz": quiz,
                "attempt": None,
                "state": quiz_live_state(quiz, 0),
                "user": await get_current_user(request, db),
                "join_closed": True,
            },
            status_code=403,
        )
    qs = await sorted_questions(db, quiz.id)
    attempt = Attempt(
        quiz_id=quiz.id,
        participant_name=participant_name.strip()[:200],
        started_at=datetime.now(timezone.utc),
        score=0,
        max_score=sum(q.points for q in qs),
    )
    db.add(attempt)
    await db.commit()
    await db.refresh(attempt)
    resp = RedirectResponse(f"/join/{pin}", status_code=302)
    set_live_cookie(resp, quiz.id, attempt.id)
    if quiz.live_pin:
        await live_hub.send_hosts(quiz.live_pin, {"type": "connected"})
    return resp


@app.get("/join/{pin}/question", response_class=HTMLResponse)
async def live_join_question(request: Request, pin: str, db: DbDep):
    r = await db.execute(select(Quiz).where(Quiz.live_pin == pin, Quiz.is_live == True))
    quiz = r.scalar_one_or_none()
    if not quiz:
        raise HTTPException(404)
    pair = read_live_cookie(request)
    if not pair or pair[0] != quiz.id:
        return RedirectResponse(f"/join/{pin}", status_code=302)
    ar = await db.execute(select(Attempt).where(Attempt.id == pair[1], Attempt.quiz_id == quiz.id))
    attempt = ar.scalar_one_or_none()
    if not attempt:
        return RedirectResponse(f"/join/{pin}", status_code=302)
    if quiz.live_finished_at:
        payload = await live_results_payload(db, quiz, attempt)
        return templates.TemplateResponse("live_results.html", {"request": request, "quiz": quiz, "result": payload})
    qs = await sorted_questions(db, quiz.id)
    idx = quiz.live_current_index
    if idx <= 0 or idx > len(qs):
        return RedirectResponse(f"/join/{pin}", status_code=302)
    q = qs[idx - 1]
    await db.refresh(q, ["options"])
    already = (
        await db.execute(select(UserAnswer).where(UserAnswer.attempt_id == attempt.id, UserAnswer.question_id == q.id))
    ).scalar_one_or_none()
    dl = as_utc(quiz.live_question_deadline_at)
    deadline_ms = 0 if already else (int(dl.timestamp() * 1000) if dl else 0)
    return templates.TemplateResponse(
        "live_question.html",
        {
            "request": request,
            "quiz": quiz,
            "attempt": attempt,
            "question": q,
            "index": idx,
            "total": len(qs),
            "already": bool(already),
            "deadline_ms": deadline_ms,
        },
    )


@app.post("/join/{pin}/answer")
async def live_answer(request: Request, pin: str, db: DbDep):
    r = await db.execute(select(Quiz).where(Quiz.live_pin == pin, Quiz.is_live == True))
    quiz = r.scalar_one_or_none()
    if not quiz or not quiz.live_started_at or quiz.live_finished_at:
        return RedirectResponse(f"/join/{pin}", status_code=302)
    pair = read_live_cookie(request)
    if not pair or pair[0] != quiz.id:
        return RedirectResponse(f"/join/{pin}", status_code=302)
    ar = await db.execute(select(Attempt).where(Attempt.id == pair[1], Attempt.quiz_id == quiz.id))
    attempt = ar.scalar_one_or_none()
    if not attempt:
        return RedirectResponse(f"/join/{pin}", status_code=302)
    qs = await sorted_questions(db, quiz.id)
    q = qs[quiz.live_current_index - 1]
    ex = (
        await db.execute(select(UserAnswer).where(UserAnswer.attempt_id == attempt.id, UserAnswer.question_id == q.id))
    ).scalar_one_or_none()
    if ex:
        return RedirectResponse(f"/join/{pin}/question", status_code=303)
    form = await request.form()
    selected: list[int] = []
    text_val: Optional[str] = None
    if q.question_type == QuestionType.single_choice:
        v = form.get("option_id")
        if v and str(v).isdigit():
            selected = [int(v)]
    elif q.question_type == QuestionType.multiple_choice:
        for key in form.keys():
            if key.startswith("opt_") and key[4:].isdigit():
                selected.append(int(key[4:]))
        selected = sorted(set(selected))
    else:
        text_val = (form.get("text_answer") or "").strip()
    valid_time = True
    nowv = datetime.now(timezone.utc)
    dl = as_utc(quiz.live_question_deadline_at)
    if dl and nowv > dl:
        valid_time = False
    g = as_utc(quiz.live_quiz_deadline_at)
    if quiz.timer_mode == TimerMode.whole_quiz and g and nowv > g:
        valid_time = False
    if not valid_time:
        selected = []
        text_val = None
        ok, pts = False, 0
    else:
        ok, pts = await score_answer(db, q, selected if q.question_type != QuestionType.text_input else None, text_val)
    ua = UserAnswer(
        attempt_id=attempt.id,
        question_id=q.id,
        selected_options=selected if q.question_type != QuestionType.text_input else None,
        text_answer=text_val if q.question_type == QuestionType.text_input else None,
        is_correct=ok,
    )
    db.add(ua)
    attempt.score += pts
    await db.commit()
    if quiz.live_pin:
        await live_hub.send_hosts(quiz.live_pin, {"type": "answers"})
    return RedirectResponse(f"/join/{pin}/question", status_code=303)


@app.websocket("/ws/live/host/{pin}")
async def ws_live_host(ws: WebSocket, pin: str):
    await live_hub.connect_host(pin, ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        live_hub.disconnect_host(pin, ws)


@app.websocket("/ws/live/player/{pin}/{attempt_id}")
async def ws_live_player(ws: WebSocket, pin: str, attempt_id: int, db: DbDep):
    r = await db.execute(select(Quiz).where(Quiz.live_pin == pin, Quiz.is_live == True))
    quiz = r.scalar_one_or_none()
    if not quiz:
        await ws.close()
        return
    await live_hub.connect_player(pin, attempt_id, ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        live_hub.disconnect_player(pin, attempt_id, ws)

@app.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request, db: DbDep):
    if await get_current_user(request, db):
        return RedirectResponse("/dashboard", status_code=302)
    return templates.TemplateResponse("forgot_password.html", {"request": request, "errors": {}, "sent": False})


@app.post("/forgot-password", response_class=HTMLResponse)
async def forgot_password_post(request: Request, db: DbDep, email: str = Form("")):
    if await get_current_user(request, db):
        return RedirectResponse("/dashboard", status_code=302)
    try:
        email_norm = ev_validate_email(email, check_deliverability=False).normalized
    except EmailNotValidError:
        return templates.TemplateResponse(
            "forgot_password.html",
            {"request": request, "errors": {"email": "Некорректный email"}, "sent": False},
            status_code=400,
        )
    r = await db.execute(select(User).where(User.email == email_norm))
    u = r.scalar_one_or_none()
    if u:
        tok = secrets.token_urlsafe(32)
        u.reset_token = tok
        u.reset_token_expires = datetime.now(timezone.utc) + timedelta(hours=1)
        await db.commit()
        base = get_base_url(request).rstrip("/")
        link = f"{base}/reset-password?token={tok}"
        body = f"Сброс пароля QuizLab. Перейдите по ссылке (действует 1 час):\n{link}"
        try:
            await send_mail_async(email_norm, "Сброс пароля QuizLab", body)
        except Exception:
            pass
    return templates.TemplateResponse("forgot_password.html", {"request": request, "errors": {}, "sent": True})


@app.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page(request: Request, db: DbDep, token: Optional[str] = None):
    if await get_current_user(request, db):
        return RedirectResponse("/dashboard", status_code=302)
    if not token:
        return templates.TemplateResponse(
            "reset_password.html", {"request": request, "errors": {"token": "Нет токена"}, "token": ""}, status_code=400
        )
    return templates.TemplateResponse("reset_password.html", {"request": request, "errors": {}, "token": token})


@app.post("/reset-password", response_class=HTMLResponse)
async def reset_password_post(
    request: Request, db: DbDep, token: str = Form(""), password: str = Form(""), password2: str = Form("")
):
    if await get_current_user(request, db):
        return RedirectResponse("/dashboard", status_code=302)
    errors: dict[str, str] = {}
    if len(password) < 8:
        errors["password"] = "Пароль не короче 8 символов"
    if password != password2:
        errors["password2"] = "Пароли не совпадают"
    if errors:
        return templates.TemplateResponse(
            "reset_password.html", {"request": request, "errors": errors, "token": token}, status_code=400
        )
    r = await db.execute(select(User).where(User.reset_token == token))
    u = r.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if not u or not u.reset_token_expires or as_utc(u.reset_token_expires) < now:
        return templates.TemplateResponse(
            "reset_password.html",
            {"request": request, "errors": {"form": "Ссылка недействительна или истекла"}, "token": token},
            status_code=400,
        )
    u.password_hash = hash_password(password)
    u.reset_token = None
    u.reset_token_expires = None
    await db.commit()
    return RedirectResponse("/login?pwd=reset", status_code=302)


@app.get("/admin/settings", response_class=HTMLResponse)
async def admin_settings_page(request: Request, db: DbDep, admin: AdminDep):
    s = cfg.get_settings()
    flash_ok = request.query_params.get("saved") == "1"
    flash_mail = request.query_params.get("mail")
    mail_detail = unquote(request.query_params.get("detail") or "")
    return templates.TemplateResponse(
        "admin_settings.html",
        {
            "request": request,
            "user": admin,
            "settings": s,
            "flash_ok": flash_ok,
            "flash_mail": flash_mail,
            "mail_detail": mail_detail,
        },
    )


@app.post("/admin/settings")
async def admin_settings_save(
    request: Request,
    db: DbDep,
    admin: AdminDep,
    smtp_host: str = Form(""),
    smtp_port: int = Form(587),
    smtp_user: str = Form(""),
    smtp_password: str = Form(""),
    smtp_from: str = Form(""),
):
    cfg.save_settings(
        {
            "SMTP_HOST": smtp_host.strip(),
            "SMTP_PORT": int(smtp_port),
            "SMTP_USER": smtp_user.strip(),
            "SMTP_PASSWORD": smtp_password,
            "SMTP_FROM": smtp_from.strip(),
        }
    )
    return RedirectResponse("/admin/settings?saved=1", status_code=302)


@app.post("/admin/settings/test-mail")
async def admin_settings_test_mail(request: Request, db: DbDep, admin: AdminDep):
    s = cfg.get_settings()
    to = s["ADMIN_EMAIL"].strip()
    try:
        await send_mail_async(to, "QuizLab SMTP test", "Тестовое письмо: настройки SMTP работают.")
        return RedirectResponse("/admin/settings?mail=ok", status_code=302)
    except Exception as ex:
        msg = quote(str(ex).replace("\n", " ")[:400], safe="")
        return RedirectResponse(f"/admin/settings?mail=err&detail={msg}", status_code=302)


@app.get("/admin/featured", response_class=HTMLResponse)
async def admin_featured_page(request: Request, db: DbDep, admin: AdminDep):
    r = await db.execute(
        select(Quiz)
        .where(Quiz.status == QuizStatus.published)
        .options(selectinload(Quiz.author))
        .order_by(Quiz.created_at.desc())
    )
    rows = []
    for q in r.scalars().all():
        nq = (
            await db.execute(select(func.count()).select_from(Question).where(Question.quiz_id == q.id))
        ).scalar() or 0
        na = (
            await db.execute(
                select(func.count()).select_from(Attempt).where(Attempt.quiz_id == q.id, Attempt.finished_at.isnot(None))
            )
        ).scalar() or 0
        rows.append({"quiz": q, "n_questions": nq, "n_attempts": na})
    return templates.TemplateResponse(
        "admin_featured.html", {"request": request, "user": admin, "rows": rows}
    )


@app.post("/admin/featured/{quiz_id}/feature")
async def admin_featured_on(quiz_id: int, db: DbDep, admin: AdminDep):
    r = await db.execute(select(Quiz).where(Quiz.id == quiz_id, Quiz.status == QuizStatus.published))
    q = r.scalar_one_or_none()
    if not q:
        raise HTTPException(404)
    q.featured = True
    q.featured_at = datetime.now(timezone.utc)
    await db.commit()
    return RedirectResponse("/admin/featured", status_code=302)


@app.post("/admin/featured/{quiz_id}/unfeature")
async def admin_featured_off(quiz_id: int, db: DbDep, admin: AdminDep):
    r = await db.execute(select(Quiz).where(Quiz.id == quiz_id))
    q = r.scalar_one_or_none()
    if not q:
        raise HTTPException(404)
    q.featured = False
    q.featured_at = None
    await db.commit()
    return RedirectResponse("/admin/featured", status_code=302)


@app.post("/admin/featured/{quiz_id}/delete")
async def admin_quiz_delete(quiz_id: int, db: DbDep, admin: AdminDep):
    r = await db.execute(select(Quiz).where(Quiz.id == quiz_id))
    q = r.scalar_one_or_none()
    if not q:
        raise HTTPException(404)
    await db.delete(q)
    await db.commit()
    return RedirectResponse("/admin/featured", status_code=302)


app.mount("/static", StaticFiles(directory="static"), name="static")
