-- Run once with an administrator/migration role, before starting QueueFS.
BEGIN;
CREATE TABLE public.ov_queuefs_schema (
    version integer PRIMARY KEY CHECK (version = 1)
);
INSERT INTO public.ov_queuefs_schema VALUES (1);
CREATE TABLE public.ov_queuefs_queues (
    namespace text COLLATE "C" NOT NULL CHECK (octet_length(namespace) BETWEEN 1 AND 256),
    name text COLLATE "C" NOT NULL CHECK (octet_length(name) BETWEEN 1 AND 1024),
    PRIMARY KEY (namespace, name)
);
CREATE TABLE public.ov_queuefs_workers (
    namespace text COLLATE "C" NOT NULL CHECK (octet_length(namespace) BETWEEN 1 AND 256),
    owner text NOT NULL,
    lease_expires_at timestamptz NOT NULL,
    PRIMARY KEY (namespace, owner)
);
CREATE TABLE public.ov_queuefs_messages (
    sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    schedule_sequence bigint NOT NULL DEFAULT nextval('public.ov_queuefs_messages_sequence_seq'),
    namespace text COLLATE "C" NOT NULL,
    queue_name text COLLATE "C" NOT NULL,
    message_id text NOT NULL CHECK (octet_length(message_id) BETWEEN 1 AND 128),
    payload text NOT NULL,
    owner text,
    group_key text CHECK (octet_length(group_key) BETWEEN 1 AND 4096),
    serial_key text CHECK (octet_length(serial_key) BETWEEN 1 AND 4096),
    CHECK (group_key IS NULL OR serial_key IS NULL),
    UNIQUE (namespace, queue_name, message_id),
    FOREIGN KEY (namespace, queue_name)
        REFERENCES public.ov_queuefs_queues(namespace, name) ON DELETE CASCADE
);
CREATE INDEX ov_queuefs_pending ON public.ov_queuefs_messages(namespace, queue_name, schedule_sequence)
    WHERE owner IS NULL;
CREATE INDEX ov_queuefs_processing ON public.ov_queuefs_messages(namespace, queue_name, owner)
    WHERE owner IS NOT NULL;
CREATE INDEX ov_queuefs_serial ON public.ov_queuefs_messages(namespace, queue_name, md5(serial_key), sequence)
    WHERE serial_key IS NOT NULL;
CREATE INDEX ov_queuefs_group ON public.ov_queuefs_messages(namespace, queue_name, md5(group_key))
    WHERE group_key IS NOT NULL AND owner IS NOT NULL;
COMMIT;
-- Runtime role needs USAGE on schema public, SELECT on ov_queuefs_schema,
-- SELECT/INSERT/UPDATE/DELETE on the other three tables, and
-- USAGE on public.ov_queuefs_messages_sequence_seq. No CREATE privilege needed.
