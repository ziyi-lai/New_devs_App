from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
import logging
from ..config import settings

logger = logging.getLogger(__name__)


def to_async_database_url(url: str) -> str:
    """SQLAlchemy asyncio needs the asyncpg driver in the URL."""
    if url.startswith("postgresql+asyncpg://"):
        return url
    if url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://"):]
    return url


class DatabasePool:
    def __init__(self):
        self.engine = None
        self.session_factory = None

    async def initialize(self):
        """Initialize database connection pool."""
        if self.session_factory is not None:
            return
        try:
            # Use DATABASE_URL. The previous pool built a URL from Supabase
            # fields this app does not configure, so revenue queries never ran.
            database_url = to_async_database_url(settings.database_url)

            # create_async_engine rejects the sync QueuePool. The default
            # asyncio pool is the one that can actually check out connections.
            self.engine = create_async_engine(
                database_url,
                pool_size=5,
                max_overflow=5,
                pool_pre_ping=True,
                pool_recycle=3600,
                echo=False,
            )

            self.session_factory = async_sessionmaker(
                bind=self.engine,
                class_=AsyncSession,
                expire_on_commit=False,
            )

            logger.info("Database connection pool initialized")

        except Exception as e:
            logger.error("Database pool initialization failed: %s", e)
            self.engine = None
            self.session_factory = None

    async def close(self):
        """Close database connections."""
        if self.engine:
            await self.engine.dispose()
        self.engine = None
        self.session_factory = None

    @asynccontextmanager
    async def get_session(self):
        """Yield a session. Callers use `async with pool.get_session()`."""
        if not self.session_factory:
            raise Exception("Database pool not initialized")
        async with self.session_factory() as session:
            yield session


# Global database pool instance
db_pool = DatabasePool()


async def get_db_session():
    """Dependency to get database session."""
    if db_pool.session_factory is None:
        await db_pool.initialize()
    async with db_pool.get_session() as session:
        yield session
