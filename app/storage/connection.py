"""Connection."""

from utils.paths import DATA_DIR
from app.storage import transaction_models, market_models  # noqa: F401 -- register ORM tables
import threading
from sqlmodel import Session, SQLModel, create_engine


_CONFIG_DIR = DATA_DIR


_DB_PATH = _CONFIG_DIR / "app.db"


_engine = None


_engine_lock = threading.Lock()


def get_engine():
    global _engine
    if _engine is not None:
        return _engine
    with _engine_lock:
        if _engine is not None:
            return _engine
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(
            f"sqlite:///{_DB_PATH}",
            echo=False,
            connect_args={"check_same_thread": False},
        )
        return _engine


def get_session() -> Session:
    return Session(get_engine())


def init_db() -> None:
    """Create all tables if they don't exist, and run lightweight migrations."""
    from sqlalchemy import text as sa_text

    engine = get_engine()
    SQLModel.metadata.create_all(engine)
    purchase_columns = (
        ("buff_order_id", "TEXT"),
        ("buff_sell_order_id", "TEXT"),
        ("batch_id", "TEXT"),
        ("bill_order_id", "TEXT"),
    )
    with engine.connect() as conn:
        for column_name, column_type in purchase_columns:
            try:
                conn.execute(
                    sa_text(f"ALTER TABLE purchase ADD COLUMN {column_name} {column_type}")
                )
                conn.commit()
            except Exception:
                pass
        # Empty/NULL legacy values are intentionally excluded.  Every real
        # BUFF bill/order id may be recorded at most once.
        try:
            conn.execute(
                sa_text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS "
                    "ux_purchase_buff_order_id ON purchase(buff_order_id) "
                    "WHERE buff_order_id IS NOT NULL AND buff_order_id != ''"
                )
            )
            conn.commit()
        except Exception:
            pass
