-- Runs once, as the postgres superuser, the first time the data volume is created.
-- One instance, three databases: each service owns its own schema and never
-- reaches across into another service's tables.

CREATE DATABASE orders_db;
CREATE DATABASE inventory_db;
CREATE DATABASE payments_db;

-- pg_stat_statements is the first place to look when "the database is slow".
-- The library itself is loaded by the postgres command line in docker-compose.yml;
-- the extension still has to be created per database.
\connect orders_db
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

\connect inventory_db
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

\connect payments_db
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

\connect postgres

-- Throwaway databases for the `db`-marked concurrency tests. They need a real
-- PostgreSQL (SQLite serialises writers, so those tests would pass against broken
-- code), but the test fixtures create_all/drop_all and delete every row between
-- tests - pointed at inventory_db that would drop the schema out from under the
-- running container. Separate databases, same instance, same locking behaviour.
CREATE DATABASE orders_test;
CREATE DATABASE inventory_test;
CREATE DATABASE payments_test;
