"""Data model and session. SQLAlchemy 2 async, mirroring HELIX's conventions
so this ports across rather than needing translation.

The shape follows what the research established:

  * A COMPANY is the legal entity — keyed on employer FEIN, because that is what
    already groups the 240 branches into 19 entities, and the legal entity is
    what COMPANY NAME on the letter must name.
  * A TEMPLATE belongs to a company and covers MANY branches.
  * A VERSION is immutable and checksummed. An issued letter has to stay
    reproducible years later, so nothing overwrites a published body.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import AsyncIterator

from sqlalchemy import (
    BigInteger, Boolean, DateTime, ForeignKey, Index, Integer,
    String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.config import settings


class Base(DeclarativeBase):
    pass


def _uuid() -> str:
    return uuid.uuid4().hex[:16]


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class Company(Base, TimestampMixin):
    """A legal entity. What COMPANY NAME resolves to on the letter."""
    __tablename__ = "companies"

    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=_uuid)
    fein: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    legal_name: Mapped[str] = mapped_column(String(255), nullable=False)
    short_name: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    #: Never auto-assigned from a sync. A wrong legal name on a binding offer is
    #: the failure this whole design exists to prevent, so a human confirms it.
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    logo_asset_id: Mapped[str | None] = mapped_column(ForeignKey("assets.id", ondelete="SET NULL"))

    branches: Mapped[list["Branch"]] = relationship(back_populates="company", lazy="selectin")
    logo: Mapped["Asset | None"] = relationship(lazy="selectin")


class Branch(Base, TimestampMixin):
    __tablename__ = "branches"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    #: The branch CODE. `name` holds the employer legal name and repeats across
    #: every branch of that entity, so the code is the only thing distinguishing
    #: nine Harrisburg branches that all read "Global Empire LLC".
    code: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    fein: Mapped[str] = mapped_column(String(32), default="", nullable=False, index=True)
    city: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    state: Mapped[str] = mapped_column(String(8), default="", nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    company_id: Mapped[str | None] = mapped_column(ForeignKey("companies.id", ondelete="SET NULL"))

    company: Mapped["Company | None"] = relationship(back_populates="branches", lazy="selectin")


class Asset(Base, TimestampMixin):
    """Brand logo or icon. Referenced by companies and templates, never copied."""
    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), default="logo", nullable=False)
    mime: Mapped[str] = mapped_column(String(128), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)


class Template(Base, TimestampMixin):
    __tablename__ = "templates"

    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    company_id: Mapped[str | None] = mapped_column(ForeignKey("companies.id", ondelete="SET NULL"))
    #: The client's taxonomy: hourly | salary | commission | contractor
    category: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    #: outside_sales | contractor_hourly | contractor_flat | contractor_commission
    subdivision: Mapped[str] = mapped_column(String(48), default="", nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    #: The approved .docx, write-once per version. This is the artifact; every
    #: other representation is derived.
    source_filename: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    source_path: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), default="", nullable=False)

    company: Mapped["Company | None"] = relationship(lazy="selectin")
    versions: Mapped[list["TemplateVersion"]] = relationship(
        back_populates="template", lazy="selectin", cascade="all, delete-orphan",
        order_by="TemplateVersion.version.desc()")

    __table_args__ = (
        Index("ix_templates_scope", "company_id", "category", "subdivision"),
    )


class TemplateVersion(Base):
    """Append-only. Nothing here is ever updated."""
    __tablename__ = "template_versions"

    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=_uuid)
    template_id: Mapped[str] = mapped_column(
        ForeignKey("templates.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_path: Mapped[str] = mapped_column(String(255), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    note: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)

    template: Mapped["Template"] = relationship(back_populates="versions")

    __table_args__ = (UniqueConstraint("template_id", "version", name="uq_template_version"),)


class AiRun(Base):
    """Audit of every model call. Append-only.

    A suggestion that shaped a legal document has to be reconstructable later:
    which model, what it proposed, whether a human accepted it, and who. The
    prompt digest lets you prove the same input was sent without storing a copy
    of a document that may contain personal data.
    """
    __tablename__ = "ai_runs"

    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=_uuid)
    template_id: Mapped[str | None] = mapped_column(
        ForeignKey("templates.id", ondelete="CASCADE"))
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    prompt_sha256: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    result: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    #: null = still a suggestion. Nothing an LLM produced is applied implicitly.
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_by: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)


class TemplateBranch(Base):
    """Many-to-many. One template covers many branches."""
    __tablename__ = "template_branches"

    template_id: Mapped[str] = mapped_column(
        ForeignKey("templates.id", ondelete="CASCADE"), primary_key=True)
    branch_id: Mapped[int] = mapped_column(
        ForeignKey("branches.id", ondelete="CASCADE"), primary_key=True)

    __table_args__ = (Index("ix_tb_branch", "branch_id"),)


# --------------------------------------------------------------------------- #

engine = create_async_engine(settings.database_url, pool_pre_ping=True, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def create_all() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
