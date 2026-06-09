from datetime import datetime
from sqlalchemy import BigInteger, Integer, String, Boolean, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from db.database import Base

class ChatSettings(Base):
    __tablename__ = 'chat_settings'

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    yougile_api_key: Mapped[str] = mapped_column(String, nullable=True)
    project_id: Mapped[str] = mapped_column(String, nullable=True)
    board_id: Mapped[str] = mapped_column(String, nullable=True)
    
    col_participants: Mapped[str] = mapped_column(String, nullable=True)
    col_no_deadline: Mapped[str] = mapped_column(String, nullable=True)
    col_has_deadline: Mapped[str] = mapped_column(String, nullable=True)
    col_urgent: Mapped[str] = mapped_column(String, nullable=True)
    col_done: Mapped[str] = mapped_column(String, nullable=True)
    
    auto_authorize_members: Mapped[bool] = mapped_column(Boolean, default=True)
    session_start_time: Mapped[datetime] = mapped_column(DateTime, nullable=True)

class ChatMember(Base):
    __tablename__ = 'chat_members'
    
    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str] = mapped_column(String, nullable=True)
    role: Mapped[str] = mapped_column(String, default="user")
    is_authorized: Mapped[bool] = mapped_column(Boolean, default=False)

class TaskCache(Base):
    __tablename__ = 'tasks_cache'
    
    id: Mapped[str] = mapped_column(String, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    assigned_to: Mapped[str] = mapped_column(String, nullable=True)
    deadline: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    is_notified: Mapped[bool] = mapped_column(Boolean, default=False)

class PendingMessage(Base):
    __tablename__ = 'pending_messages'
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    username: Mapped[str] = mapped_column(String, nullable=False)
    text: Mapped[str] = mapped_column(String, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
