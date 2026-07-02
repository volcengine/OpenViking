-- Enable the pgvector extension on first boot of the pgvector/pgvector:pg16
-- container (mounted into /docker-entrypoint-initdb.d). OpenViking's adapter
-- also runs this under an advisory lock at connect time (create_extension=true),
-- so this file is belt-and-suspenders for pre-provisioned / CI deployments.
CREATE EXTENSION IF NOT EXISTS vector;
