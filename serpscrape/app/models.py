from __future__ import annotations

from datetime import datetime

from sqlalchemy import ARRAY, BigInteger, Boolean, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    encrypted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ApiToken(Base):
    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    is_ui_bootstrap: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    keywords: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    engines: Mapped[list[str]] = mapped_column(ARRAY(String(32)), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False, default="US")
    proxy_config: Mapped[str | None] = mapped_column(Text, nullable=True)  # encrypted JSON
    per_page_delay_ms: Mapped[int] = mapped_column(default=1500, nullable=False)
    per_keyword_delay_ms: Mapped[int] = mapped_column(default=5000, nullable=False)
    notify_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="queued", nullable=False, index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    progress: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    results: Mapped[list["TaskResult"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )


class TaskResult(Base):
    __tablename__ = "task_results"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    engine: Mapped[str] = mapped_column(String(32), nullable=False)
    keyword: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    scraped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    task: Mapped[Task] = relationship(back_populates="results")

    __table_args__ = (
        Index("ix_task_results_task_engine_keyword_position", "task_id", "engine", "keyword", "position"),
    )
