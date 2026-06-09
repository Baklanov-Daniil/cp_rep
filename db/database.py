import os
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

DATABASE_URL = "sqlite+aiosqlite:///messages.db"

engine = create_async_engine(DATABASE_URL, echo=False)

AsyncSessionMaker = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)

class Base(DeclarativeBase):
    pass

async def init_db():
    import db.models  # noqa: F401 - registers SQLAlchemy models on Base.metadata

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
