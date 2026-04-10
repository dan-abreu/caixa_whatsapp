import threading
from typing import Optional

from fastapi import HTTPException

from app.database import DatabaseClient, DatabaseError


_db_instance: Optional[DatabaseClient] = None
_db_instance_lock = threading.Lock()


def get_db() -> DatabaseClient:
    global _db_instance
    if _db_instance is not None:
        return _db_instance

    try:
        with _db_instance_lock:
            if _db_instance is None:
                _db_instance = DatabaseClient()
            return _db_instance
    except DatabaseError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc