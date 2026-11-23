"""Pool environment parsing fails with a user-reportable error."""

from __future__ import annotations

import pytest

from machina.core.exceptions import ValidationError
from machina.db.connection import Database


@pytest.mark.asyncio
async def test_bad_pool_environment_is_a_typed_failure(monkeypatch) -> None:
    monkeypatch.setenv("MACHINA_DB_MAX_POOL", "many")
    database = Database(dsn="postgresql://user@127.0.0.1/example")

    with pytest.raises(ValidationError, match="MACHINA_DB_MAX_POOL"):
        await database.connect()
