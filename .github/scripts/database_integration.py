"""Provision isolated CI backends and reject silently skipped database contracts."""

import argparse
import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ACCOUNT_CASES = {
    "test_mysql_load_creates_default_account",
    "test_distributed_authentication_state_is_shared_and_service_scoped",
}
GROUPS = ("accounts", "redis_mysql", "task_work_mysql", "task_work_postgres", "watch_mysql")


def schema_statements(sql):
    # Shipped DDL uses line comments; discard them before splitting so comments
    # containing semicolons (including the migration template) are not executed.
    sql = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    return [statement.strip() for statement in sql.split(";") if statement.strip()]


def prepare():
    # Imports must fail rather than letting pytest.importorskip hide missing extras.
    import aiomysql  # noqa: F401
    import psycopg
    import pymysql
    import redis
    import sqlalchemy.ext.asyncio  # noqa: F401

    params = json.loads(os.environ["OV_TEST_MYSQL"])
    postgres_params = json.loads(os.environ["OV_TEST_POSTGRES"])
    with psycopg.connect(
        **{
            ("dbname" if key == "database" else key): value
            for key, value in postgres_params.items()
        },
        connect_timeout=10,
    ) as connection:
        assert connection.execute("SELECT 1").fetchone() == (1,)
    watch_params = json.loads(os.environ["OPENVIKING_WATCH_MYSQL_PARAMS"])
    for values in (params, watch_params):
        with pymysql.connect(**values, connect_timeout=10) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                assert cursor.fetchone() == (1,)
    with redis.Redis.from_url(os.environ["OV_TEST_REDIS"], socket_timeout=10) as client:
        assert client.ping(), "Redis did not answer PING"

    # Use the shipped deployment DDL, including migrations, before the fixtures
    # call create_all (which does not migrate an existing schema).
    sql_dir = ROOT / "openviking/server/account_stores/sql"
    schemas = [sql_dir / "init.sql", *sorted((sql_dir / "changes").glob("*.sql"))]
    with pymysql.connect(**params, autocommit=True, connect_timeout=10) as connection:
        with connection.cursor() as cursor:
            for schema in schemas:
                for statement in schema_statements(schema.read_text(encoding="utf-8")):
                    cursor.execute(statement)
    config = {
        "service_name": "database-integration",
        "storage": {"agfs": {"backend": "local"}, "vectordb": {"backend": "local"}},
        "embedding": {
            "dense": {
                "provider": "openai",
                "model": "test-embedder",
                "api_key": "test-key",
                "dimension": 1024,
            }
        },
        "encryption": {"enabled": False},
    }
    Path(os.environ["OPENVIKING_CONFIG_FILE"]).write_text(json.dumps(config), encoding="utf-8")
    print("PostgreSQL, MySQL and Redis are reachable; account deployment schema is installed.")


def database_group(case):
    module = case.get("classname", "")
    name = case.get("name", "")
    if module == "tests.server.test_account_services" and name in ACCOUNT_CASES:
        return "accounts"
    if module == "tests.server.test_redis_mysql_account_store":
        return "redis_mysql"
    # Parameter IDs also contain secondary parameters, e.g. [mysql-stage].
    parameters = name.partition("[")[2].rstrip("]").split("-")
    if "postgres" in parameters and module in {
        "tests.service.test_task_work_contract",
        "tests.service.test_task_store_postgres",
    }:
        return "task_work_postgres"
    if "mysql" in parameters:
        if module == "tests.service.test_task_work_contract":
            return "task_work_mysql"
        if module == "tests.resource.test_watch_store":
            return "watch_mysql"
    return None


def verify(report):
    counts = dict.fromkeys(GROUPS, 0)
    accounts = set()
    problems = []
    for case in ET.parse(report).iter("testcase"):
        group = database_group(case)
        if group is None:
            continue
        name = case.get("name", "")
        if any(case.find(status) is not None for status in ("skipped", "failure", "error")):
            problems.append(f"{group}: {name} did not pass")
            continue
        counts[group] += 1
        if group == "accounts":
            accounts.add(name)
    for group, count in counts.items():
        if not count:
            problems.append(f"No passing database cases in {group}")
    for name in sorted(ACCOUNT_CASES - accounts):
        problems.append(f"Required account contract missing: {name}")
    summary = "\n".join(
        f"- {group}: {count} database cases passed" for group, count in counts.items()
    )
    print(summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as output:
            output.write(f"### Database integration coverage\n\n{summary}\n")
            if problems:
                output.write("\n" + "\n".join(f"- ERROR: {problem}" for problem in problems) + "\n")
    if problems:
        raise RuntimeError("\n".join(problems))
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("prepare")
    verifier = commands.add_parser("verify")
    verifier.add_argument("report", type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare()
    else:
        verify(args.report)


if __name__ == "__main__":
    main()
