from openviking_cli.utils.config.transaction_config import TransactionConfig


def test_lock_expire_defaults_to_thirty_seconds():
    assert TransactionConfig().lock_expire == 30
