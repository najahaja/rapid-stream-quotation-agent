"""SQLAlchemy ORM models for the database."""

import uuid
from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    customer_id = Column(String, nullable=True)  # Authenticated owner
    created_at = Column(DateTime, default=datetime.utcnow)
    # JSON-serialized dict holding extracted fields accumulated across turns
    accumulated_state = Column(Text, default="{}")

    messages = relationship(
        "Message", back_populates="conversation", order_by="Message.created_at"
    )
    drafts = relationship("QuotationDraft", back_populates="conversation")


class Message(Base):
    __tablename__ = "messages"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id = Column(String, ForeignKey("conversations.id"), nullable=False)
    role = Column(String, nullable=False)  # "customer" or "agent"
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    conversation = relationship("Conversation", back_populates="messages")


class QuotationDraft(Base):
    __tablename__ = "quotation_drafts"
    __table_args__ = (
        UniqueConstraint("request_id", name="uq_quotation_drafts_request_id"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    request_id = Column(String, nullable=False, unique=True)
    conversation_id = Column(String, ForeignKey("conversations.id"), nullable=False)
    customer_id = Column(String, nullable=True)  # Authenticated owner
    sku = Column(String, nullable=True)
    configuration = Column(Text, nullable=True)  # JSON string
    extraction_data = Column(
        Text, nullable=True
    )  # JSON string for idempotency restoration
    stock_check_data = Column(
        Text, nullable=True
    )  # JSON string for idempotency restoration
    quantity = Column(Integer, nullable=True)
    unit_selling_price = Column(Float, nullable=True)  # None if pending
    total_price = Column(Float, nullable=True)  # None if pending or partial
    stock_available = Column(Integer, nullable=True)
    stock_requested = Column(Integer, nullable=True)
    lead_time = Column(String, nullable=True)
    status = Column(String, nullable=False)  # See QuotationStatus enum
    clarification_question = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    reviewer_id = Column(String, nullable=True)
    approved_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    conversation = relationship("Conversation", back_populates="drafts")
