"""MySQL HA取连接前校验；已进入事务的业务SQL不自动重放。"""

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DisconnectionError, InvalidRequestError

from openviking.storage.mysql_session import (
    create_mysql_engine,
    require_writable_checkout,
)


@pytest.mark.parametrize("read_only", [0, 1, None])
def test_mysql_checkout_rejects_old_primary_before_business_sql(read_only):
    connection = Mock()
    cursor = connection.cursor.return_value
    cursor.fetchone.return_value = None if read_only is None else (read_only,)
    if read_only == 0:
        require_writable_checkout(connection, Mock(), Mock())
    else:
        with pytest.raises(DisconnectionError, match="not writable"):
            require_writable_checkout(connection, Mock(), Mock())
    cursor.execute.assert_called_once_with("SELECT @@global.read_only")
    cursor.close.assert_called_once()
    connection.rollback.assert_called_once()


def test_mysql_factory_registers_only_explicit_ha_guard():
    with patch("openviking.storage.mysql_session.create_async_engine") as factory:
        with patch("openviking.storage.mysql_session.event.listen") as listen:
            create_mysql_engine("mysql+aiomysql://test", require_writable=False)
            listen.assert_not_called()
            create_mysql_engine("mysql+aiomysql://test", require_writable=True)
            listen.assert_called_once_with(
                factory.return_value.sync_engine, "checkout", require_writable_checkout
            )
            assert all("require_writable" not in call.kwargs for call in factory.call_args_list)


def test_real_mysql_secondary_is_rejected_before_transaction():
    replica = os.environ.get("MYSQL_TEST_REPLICA_HOST")
    if not replica:
        pytest.skip("isolated MySQL replica read-only probe required")
    config = json.loads(Path("/secrets/state/config.json").read_text())
    url = URL.create(
        "mysql+aiomysql", username=config["user"], password=config["password"],
        host=replica, port=config["port"], database=config["database"],
    )

    async def probe():
        engine = create_mysql_engine(url, require_writable=True, pool_size=1, max_overflow=0)
        executed = False
        try:
            with pytest.raises(InvalidRequestError) as error:
                async with engine.begin() as connection:
                    executed = True
                    await connection.execute(text("SELECT 1"))
            assert "connection is closed" in str(error.value).lower()
            assert executed is False
        finally:
            await engine.dispose()

    asyncio.run(probe())
