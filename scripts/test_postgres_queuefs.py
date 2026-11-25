#!/usr/bin/env python3
"""Run real QueueFS contracts in a disposable local PostgreSQL instance.

Usage: PG_BIN=/path/to/postgres/bin python3 scripts/test_postgres_queuefs.py
Full subprocess output is retained in target/queuefs-postgres-test.log.
"""
import os
from pathlib import Path
import socket
import subprocess
import tempfile


def main():
    root = Path(__file__).resolve().parents[1]
    binaries = Path(os.environ["PG_BIN"])
    log_path = root / "target" / "queuefs-postgres-test.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="queuefs-pg-") as directory, log_path.open("w") as log:
        data = Path(directory) / "data"
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        def run(args, **kwargs):
            subprocess.run(args, check=True, cwd=root, stdout=log, stderr=log, **kwargs)
        run([str(binaries / "initdb"), "-D", str(data), "-A", "trust", "-U", "postgres", "--no-locale", "-E", "UTF8"])
        started = False
        try:
            run([str(binaries / "pg_ctl"), "-D", str(data), "-l", str(Path(directory) / "postgres.log"),
                 "-o", f"-h 127.0.0.1 -p {port} -k {directory} -c fsync=on -c synchronous_commit=on",
                 "-w", "start"])
            started = True
            dsn = f"host=127.0.0.1 port={port} user=postgres dbname=postgres sslmode=disable"
            env = dict(os.environ, OV_TEST_PG_DSN=dsn)
            run([str(binaries / "psql"), dsn, "-v", "ON_ERROR_STOP=1", "-f",
                 str(root / "crates/ragfs/src/plugins/queuefs/postgres_schema.sql")])
            run(["cargo", "test", "-p", "ragfs", "--features", "postgres", "--test",
                 "postgres_queuefs", "--", "--ignored"], env=env)
        finally:
            if started:
                run([str(binaries / "pg_ctl"), "-D", str(data), "-m", "fast", "-w", "stop"])
        print(f"PostgreSQL QueueFS contracts passed. Full log: {log_path}")


if __name__ == "__main__":
    main()
