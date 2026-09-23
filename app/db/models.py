"""Relational schema.

Two clearly separated groups:
* Long-term customer data: users, accounts, orders, tickets (support records).
* Conversation (thread) memory: threads, messages, feedback.
Plus operational tables: documents (KB registry), request_logs (monitoring), eval_runs.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Base(DeclarativeBase):
    pass


# ---------------- long-term customer data ----------------
class User(Base):
    __tablename__ = "users"
    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(120))
    account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.account_id"), nullable=True)
    role: Mapped[str] = mapped_column(String(32), default="customer")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Account(Base):
    __tablename__ = "accounts"
    account_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    email: Mapped[str] = mapped_column(String(200))
    customer_name: Mapped[str] = mapped_column(String(120))
    plan: Mapped[str] = mapped_column(String(32))
    workspace_id: Mapped[str] = mapped_column(String(32))
    verified: Mapped[bool] = mapped_column(Boolean, default=False)


class Order(Base):
    __tablename__ = "orders"
    order_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.account_id"), index=True)
    invoice_id: Mapped[str] = mapped_column(String(32), unique=True)
    plan: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    amount_usd: Mapped[float] = mapped_column(Float)
    charged_at: Mapped[str] = mapped_column(String(32))


class Ticket(Base):
    __tablename__ = "tickets"
    ticket_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    account_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    workspace_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    thread_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    category: Mapped[str] = mapped_column(String(32))
    priority: Mapped[str] = mapped_column(String(16), default="normal")
    status: Mapped[str] = mapped_column(String(32), default="open")
    summary: Mapped[str] = mapped_column(Text)
    product_area: Mapped[str | None] = mapped_column(String(64), nullable=True)
    steps_tried: Mapped[list] = mapped_column(JSON, default=list)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    related_ids: Mapped[list] = mapped_column(JSON, default=list)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------- thread memory ----------------
class Thread(Base):
    __tablename__ = "threads"
    thread_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("thr"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="New conversation")
    summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    messages: Mapped[list["Message"]] = relationship(
        back_populates="thread", order_by="Message.created_at", cascade="all, delete-orphan"
    )


class Message(Base):
    __tablename__ = "messages"
    message_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("msg"))
    thread_id: Mapped[str] = mapped_column(ForeignKey("threads.thread_id"), index=True)
    role: Mapped[str] = mapped_column(String(16))  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    route: Mapped[str | None] = mapped_column(String(32), nullable=True)
    citations: Mapped[list] = mapped_column(JSON, default=list)
    tool_events: Mapped[list] = mapped_column(JSON, default=list)
    trajectory: Mapped[list] = mapped_column(JSON, default=list)
    needs_escalation: Mapped[bool] = mapped_column(Boolean, default=False)
    ticket_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    thread: Mapped[Thread] = relationship(back_populates="messages")


class Feedback(Base):
    __tablename__ = "feedback"
    feedback_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("fb"))
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.message_id"), index=True)
    user_id: Mapped[str] = mapped_column(String(64))
    helpful: Mapped[bool] = mapped_column(Boolean)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------- operational ----------------
class Document(Base):
    __tablename__ = "documents"
    doc_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    product: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(32))
    source_type: Mapped[str] = mapped_column(String(64))
    trust_level: Mapped[str] = mapped_column(String(32))
    filename: Mapped[str] = mapped_column(String(300))
    content: Mapped[str] = mapped_column(Text)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    indexed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RequestLog(Base):
    __tablename__ = "request_logs"
    request_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    thread_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    endpoint: Mapped[str] = mapped_column(String(64))
    route: Mapped[str | None] = mapped_column(String(32), nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    status_code: Mapped[int] = mapped_column(Integer, default=200)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    needs_escalation: Mapped[bool] = mapped_column(Boolean, default=False)
    model_calls: Mapped[int] = mapped_column(Integer, default=0)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class EvalRun(Base):
    __tablename__ = "eval_runs"
    run_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("evr"))
    status: Mapped[str] = mapped_column(String(16), default="running")
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    report_path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
