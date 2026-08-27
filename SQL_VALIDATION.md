# SQL Validation Companion

This file is the companion for SQL shown, tested, or validated by the Oracle
A2A demo console. Run queries from a SQL worksheet connected as the indicated
database user. Never place passwords, OAuth tokens, private keys, or API keys
in this file.

## Package ownership and published-team check

Use the package that was deployed. The Sales Data Query package is documented
throughout this file. The local Database Provisioning package is different: its
agent objects are created as the provisioning schema (for example `ADBPROV`),
not as ADMIN.

Run this as the provisioning schema. `LIST_TEAMS()` is the authoritative
owner-side check and avoids release-specific dictionary column names.

```sql
SET LONG 100000
SET LONGCHUNKSIZE 100000

SELECT SYS_CONTEXT('USERENV', 'SESSION_USER') AS session_user
FROM dual;

SELECT DBMS_CLOUD_AI_AGENT.LIST_TEAMS() AS available_teams
FROM dual;

SELECT DBMS_CLOUD_AI_AGENT.DESCRIBE_TEAM(
  team_name => 'DATABASE_PROVISIONING_TEAM'
) AS team_card
FROM dual;
```

Expected: `DATABASE_PROVISIONING_TEAM` appears in `available_teams`, and the
team card describes `DATABASE_ADVISOR`, `PROVISION_DATABASE_TASK`, and the two
provisioning tools. If it does not, redeploy the package using the target
schema password. Do not rely on `ALTER SESSION SET CURRENT_SCHEMA`; it does
not change `SESSION_USER`.

To inspect dictionary columns before using named queries on a particular
database release:

```sql
SELECT column_name, column_id, data_type
FROM user_tab_columns
WHERE table_name = 'USER_AI_AGENT_TEAMS'
ORDER BY column_id;

SELECT * FROM user_ai_agent_teams;
```

## ADMIN - Select AI Creds

Queries for the Credentials that Select AI Uses

```sql
SELECT *
FROM user_credentials
WHERE credential_name = 'GENAI_CRED';
```

```sql
BEGIN
  DBMS_CLOUD.DROP_CREDENTIAL('GENAI_CRED');
END;
/
```

## ADMIN - Select AI Profile

Show the Select AI Profile is working

```sql
DECLARE
  l_profile VARCHAR2(128);
BEGIN
  DBMS_CLOUD_AI.SET_PROFILE('PROF2');
  l_profile := DBMS_CLOUD_AI.GET_PROFILE;
  DBMS_OUTPUT.PUT_LINE('Active profile: ' || NVL(l_profile, '<NULL>'));
END;
/
```

## ADMIN - Test Select AI

Queries to test Select AI

```sql
SELECT DBMS_CLOUD_AI.GENERATE(
  prompt       => 'Reply exactly: Select AI is ready.',
  profile_name => 'A2A_PROFILE',
  action       => 'chat'
)
FROM dual;
```

## ADMIN - Verify Agent and Team

Queries to validate that the agent and agent team are created in the correct schema

```sql
-- Run as ADMIN. Replace DEMO_SALES if a different target schema was used.
-- The current Oracle sample creates these four Select AI Agent objects.
-- Dictionary-view columns differ across Autonomous Database releases.
-- Start with SELECT * to verify the team row and learn this database's column names.
SELECT *
FROM dba_ai_agent_teams
WHERE owner IN (UPPER('DEMO_SALES'), 'ADMIN');

SELECT owner, agent_name, status, description
FROM dba_ai_agents
WHERE agent_name = 'ORACLE_AI_DATABASE_AGENT_ROLE';

SELECT *
FROM dba_ai_agent_tasks
WHERE owner IN (UPPER('DEMO_SALES'), 'ADMIN');

SELECT *
FROM dba_ai_agent_tools
WHERE owner IN (UPPER('DEMO_SALES'), 'ADMIN');
```

The team row should identify `ORACLE_AI_DATABASE_AGENT` and be `ENABLED`; the
tool query should return four rows. For the **Sales Data Query** package, the
upstream Oracle sample is launched as ADMIN and uses `CURRENT_SCHEMA` plus a
target-schema definer-rights procedure,
so first establish the actual catalog owner rather than assuming it is the
target user. The following release-discovery query is useful before running
more specific checks. The database used by this console exposes the plural task
view (`DBA_AI_AGENT_TASKS`), while some Oracle documentation releases show the
singular spelling.

```sql
SELECT table_name, column_name, column_id, data_type
FROM all_tab_columns
WHERE table_name IN (
  'DBA_AI_AGENT_TEAMS',
  'DBA_AI_AGENT_TASKS',
  'DBA_AI_AGENT_TASK',
  'DBA_AI_AGENTS',
  'DBA_AI_AGENT_TOOLS'
)
ORDER BY table_name, column_id;
```

The following queries inspect the stored attributes and supporting schema
objects. Use `SELECT *` intentionally: Oracle’s attribute-view columns can
vary between database releases.

```sql
SELECT *
FROM dba_ai_agent_team_attributes
WHERE owner = UPPER('DEMO_SALES');

SELECT *
FROM dba_ai_agents_attributes
WHERE owner = UPPER('DEMO_SALES')
  AND agent_name = 'ORACLE_AI_DATABASE_AGENT_ROLE';

SELECT *
FROM dba_ai_agent_task_attributes
WHERE owner = UPPER('DEMO_SALES');

SELECT *
FROM dba_ai_agent_tool_attributes
WHERE owner = UPPER('DEMO_SALES');

SELECT "KEY", "VALUE", "AGENT"
FROM demo_sales.selectai_agent_config
WHERE "AGENT" = 'ORACLE_AI_DATABASE_AGENT'
ORDER BY "KEY";

SELECT owner, object_name, object_type, status
FROM all_objects
WHERE owner = UPPER('DEMO_SALES')
  AND object_name IN (
    'SELECTAI_AGENT_CONFIG',
    'ORACLE_AI_DATA_RETRIEVAL_FUNCTIONS',
    'INITIALIZE_ORACLE_AI_DATA_RETRIEVAL_AGENT',
    'INITIALIZE_ORACLE_AI_DATA_RETRIEVAL_TOOLS',
    'DATA_RETRIEVAL_AGENT'
  )
ORDER BY object_type, object_name;

SELECT owner, name, type, line, position, text
FROM all_errors
WHERE owner = UPPER('DEMO_SALES')
  AND name IN (
    'ORACLE_AI_DATA_RETRIEVAL_FUNCTIONS',
    'INITIALIZE_ORACLE_AI_DATA_RETRIEVAL_AGENT',
    'INITIALIZE_ORACLE_AI_DATA_RETRIEVAL_TOOLS',
    'DATA_RETRIEVAL_AGENT'
  )
ORDER BY name, sequence;
```

## DEMO_SALES - Demo Data

The controlled `DEMO_SALES` schema/data page and the Oracle AI Database Agent
installer UI are available in the console. The sections below provide safe
preflight, inspection, and validation queries. Add environment-specific
installation and chat-validation observations to the test log.

## Target-schema preflight (ADMIN)

Replace `DEMO_SALES` with the selected target schema.

```sql
SELECT username, account_status
FROM dba_users
WHERE username = UPPER('DEMO_SALES');

SELECT owner, table_name, num_rows
FROM all_tables
WHERE owner = UPPER('DEMO_SALES')
ORDER BY table_name;

SELECT owner, table_name, column_name, data_type, nullable
FROM all_tab_columns
WHERE owner = UPPER('DEMO_SALES')
ORDER BY table_name, column_id;

-- The sample agent executes as DEMO_SALES, so its profile must be owned by
-- DEMO_SALES rather than only by ADMIN.
SELECT owner, profile_name, status, created, last_modified
FROM dba_cloud_ai_profiles
WHERE owner = UPPER('DEMO_SALES')
  AND profile_name = 'A2A_PROFILE';

SELECT table_name, privilege
FROM dba_tab_privs
WHERE grantee = UPPER('DEMO_SALES')
  AND (table_name IN ('DBMS_CLOUD', 'DBMS_CLOUD_AI', 'DBMS_CLOUD_AI_AGENT')
       OR table_name LIKE 'DBMS_CLOUD$PDBCS%')
ORDER BY table_name;
```

Connect as the target schema to verify the credential/profile where the agent
tools run. Do not run this as `ADMIN`.

```sql
SELECT profile_name, status
FROM user_cloud_ai_profiles
WHERE profile_name = 'A2A_PROFILE';

SELECT credential_name
FROM user_credentials
WHERE credential_name = 'GENAI_CRED';

SELECT DBMS_CLOUD_AI.GENERATE(
  prompt       => 'Reply exactly: Target schema Select AI is ready.',
  profile_name => 'A2A_PROFILE',
  action       => 'chat'
)
FROM dual;
```

## Oracle AI Database Agent sample reference

Oracle's sample installer is designed to run as `ADMIN`, targeting a selected
schema and Select AI profile. It installs configuration, package, and tools
before it creates the task, agent, and team. The planned console will use the
same ordered flow and will add its exact, reviewable SQL here before execution.

Expected sample objects:

- `SELECTAI_AGENT_CONFIG`
- `ORACLE_AI_DATA_RETRIEVAL_FUNCTIONS`
- `ORACLE_AI_DATABASE_TASK`
- `ORACLE_AI_DATABASE_AGENT_ROLE`
- `ORACLE_AI_DATABASE_AGENT`

## Agent and team validation

Run the following as `ADMIN` after the console reports a successful sample
installation. Replace `DEMO_SALES` when you chose a different target schema.

```sql
-- The sample's configuration binds the agent to the requested Select AI profile.
SELECT "KEY", "VALUE", "AGENT"
FROM demo_sales.selectai_agent_config
WHERE agent = 'ORACLE_AI_DATABASE_AGENT'
ORDER BY "KEY";

-- The package, configuration table, and controlled data tables must be valid.
SELECT owner, object_name, object_type, status
FROM all_objects
WHERE owner = UPPER('DEMO_SALES')
  AND object_name IN (
    'SELECTAI_AGENT_CONFIG',
    'ORACLE_AI_DATA_RETRIEVAL_FUNCTIONS',
    'DEMO_CUSTOMERS',
    'DEMO_PRODUCTS',
    'DEMO_ORDERS'
  )
ORDER BY object_type, object_name;

-- Confirm the expected demo-data row counts.
SELECT 'DEMO_CUSTOMERS' AS table_name, COUNT(*) AS row_count
FROM demo_sales.demo_customers
UNION ALL
SELECT 'DEMO_PRODUCTS', COUNT(*) FROM demo_sales.demo_products
UNION ALL
SELECT 'DEMO_ORDERS', COUNT(*) FROM demo_sales.demo_orders;
```

Connect as the target schema to validate tool registration. The Oracle sample
itself uses `USER_AI_AGENT_TOOLS` for this check.

```sql
SELECT tool_name, description
FROM user_ai_agent_tools
WHERE tool_name IN (
  'SQL_TOOL',
  'DISTINCT_VALUES_CHECK',
  'RANGE_VALUES_CHECK',
  'GENERATE_CHART'
)
ORDER BY tool_name;
```

The authoritative end-to-end team check is A2A discovery: obtain a token in
the console, refresh teams, and confirm `ORACLE_AI_DATABASE_AGENT` is listed.
Then load its Agent Card and send a chat test in **A2A Testing → Team Selection
and Chat**. Add the resulting
test prompts and expected answers below after your first successful run.

## ADMIN - Chat activity and task history

The A2A chat request runs under the database user represented by the external
OAuth token. For this demo that is commonly `DEMO_SALES`. History can contain
user prompts and generated results, so treat its output as sensitive test data.

First discover the history views and their release-specific columns:

```sql
SELECT table_name, column_name, column_id, data_type
FROM all_tab_columns
WHERE table_name IN (
  'DBA_AI_AGENT_TEAM_HISTORY',
  'DBA_AI_AGENT_TASK_HISTORY',
  'DBA_AI_AGENT_TOOL_HISTORY'
)
ORDER BY table_name, column_id;
```

Then inspect activity for the target schema. Each history view has its own
owner column: `TEAM_OWNER`, `TASK_OWNER`, or `TOOL_OWNER`. `SELECT *` is
otherwise deliberate because columns vary by Autonomous Database release.

```sql
SELECT *
FROM dba_ai_agent_team_history
WHERE team_owner = UPPER('DEMO_SALES');

SELECT *
FROM dba_ai_agent_task_history
WHERE task_owner = UPPER('DEMO_SALES');

SELECT *
FROM dba_ai_agent_tool_history
WHERE tool_owner = UPPER('DEMO_SALES');
```

For a failed console task, begin with the most recent runs for the published
sample team. Copy its `TEAM_EXEC_ID`, then use that identifier to inspect the
task and tool records belonging to the same execution. This avoids assuming an
undocumented error-message column while still exposing all release-specific
failure detail.

```sql
SELECT *
FROM dba_ai_agent_team_history
WHERE team_owner = UPPER('DEMO_SALES')
  AND team_name = 'ORACLE_AI_DATABASE_AGENT'
ORDER BY start_date DESC
FETCH FIRST 20 ROWS ONLY;

-- Replace the value with TEAM_EXEC_ID from the failed team-history row.
SELECT *
FROM dba_ai_agent_task_history
WHERE task_owner = UPPER('DEMO_SALES')
  AND team_exec_id = 'paste-team-exec-id-here';

SELECT *
FROM dba_ai_agent_tool_history
WHERE tool_owner = UPPER('DEMO_SALES')
  AND team_exec_id = 'paste-team-exec-id-here';
```

If a history view is absent on the current database release, use the
column-discovery query above as the authoritative result; do not substitute an
ORDS or OAuth audit view for A2A chat activity.

### Scheduler correlation for a task that remains RUNNING

Agent task history connects the database scheduler to the agent framework. A
`RUNNING` task may therefore have no active foreground `DEMO_SALES` session in
`V$SESSION`. Inspect the matching team-history row first because it records
the release-specific job information, then inspect scheduler activity as
ADMIN.

```sql
SELECT *
FROM dba_ai_agent_team_history
WHERE team_owner = UPPER('DEMO_SALES')
  AND team_exec_id = 'paste-team-exec-id-here';

SELECT owner, job_name, session_id, running_instance, elapsed_time
FROM dba_scheduler_running_jobs
ORDER BY elapsed_time DESC;

SELECT owner, job_name, status, error#, actual_start_date, run_duration,
       additional_info
FROM dba_scheduler_job_run_details
ORDER BY log_date DESC
FETCH FIRST 50 ROWS ONLY;
```

Match the job name in the team-history row to the scheduler output. No
matching job while the task remains `RUNNING` is evidence of a database agent
state-transition problem before any registered tool invocation.

## Target-schema profile endpoint diagnosis

Run as the target schema when profile creation succeeds but its live test fails.
This shows the non-secret profile attributes that control provider routing.

```sql
SELECT attribute_name, attribute_value
FROM user_cloud_ai_profile_attributes
WHERE profile_name = 'A2A_PROFILE'
ORDER BY attribute_name;
```

For a comparison between a working and a failing target schema, run as ADMIN:

```sql
SELECT owner, profile_name, attribute_name, attribute_value
FROM dba_cloud_ai_profile_attributes
WHERE owner IN (UPPER('DEMO_SALES'), UPPER('<OTHER_TARGET_SCHEMA>'))
  AND profile_name = 'A2A_PROFILE'
ORDER BY owner, attribute_name;
```

For OCI Generative AI, verify `provider`, `credential_name`,
`oci_compartment_id`, `region`, and `model`. If the live test reports a URL
that still contains the literal `my$cloud_domain`, record the database version,
profile attributes, and exact error. That is an unresolved database-side OCI
endpoint substitution, not an OAuth or A2A token failure.

## Database-native Agent runtime diagnosis

Run these as the target schema, for example `DEMO_SALES`. They isolate the
database agent runtime from the external A2A transport.

```sql
-- Set the published team for this SQLcl session.
EXEC DBMS_CLOUD_AI_AGENT.SET_TEAM('ORACLE_AI_DATABASE_AGENT');

-- SQLcl may interpret ? as a JDBC bind placeholder; start without it.
select ai agent who are you;
```

The installed object names are intentionally different:

- team: `ORACLE_AI_DATABASE_AGENT`
- task: `ORACLE_AI_DATABASE_TASK`
- database agent: `ORACLE_AI_DATABASE_AGENT_ROLE`

Use the definitions to confirm the actual profile, task, and team wiring:

```sql
SET LONG 1000000
SET LONGCHUNKSIZE 32767
SET LINESIZE 32767
SET PAGESIZE 0

SELECT DBMS_CLOUD_AI_AGENT.GET_DEFINITION(
  object_type => 'AGENT',
  object_name => 'ORACLE_AI_DATABASE_AGENT_ROLE'
) FROM dual;

SELECT DBMS_CLOUD_AI_AGENT.GET_DEFINITION(
  object_type => 'TASK',
  object_name => 'ORACLE_AI_DATABASE_TASK'
) FROM dual;

SELECT DBMS_CLOUD_AI_AGENT.GET_DEFINITION(
  object_type => 'TEAM',
  object_name => 'ORACLE_AI_DATABASE_AGENT'
) FROM dual;
```

When a SQLcl or A2A request remains `RUNNING`, use the supported user history
views first. No tool-history rows means the runtime has not invoked `SQL_TOOL`,
`DISTINCT_VALUES_CHECK`, `RANGE_VALUES_CHECK`, or `GENERATE_CHART` yet.

```sql
SELECT *
FROM user_ai_agent_team_history
ORDER BY start_date DESC;

SELECT *
FROM user_ai_agent_task_history
ORDER BY start_date DESC;

SELECT *
FROM user_ai_agent_tool_history
ORDER BY start_date DESC;
```

While a target-schema SQLcl prompt is actively hung, use a separate ADMIN
window to capture its database session and private-memory state twice, roughly
30 seconds apart:

```sql
SELECT s.sid,
       s.serial#,
       s.status,
       s.event,
       s.wait_class,
       s.seconds_in_wait,
       s.sql_id,
       s.module,
       s.action,
       ROUND(p.pga_used_mem / 1024 / 1024, 1) AS pga_used_mb,
       ROUND(p.pga_alloc_mem / 1024 / 1024, 1) AS pga_alloc_mb,
       ROUND(p.pga_freeable_mem / 1024 / 1024, 1) AS pga_freeable_mb
FROM v$session s
JOIN v$process p ON p.addr = s.paddr
WHERE s.username = UPPER('DEMO_SALES')
ORDER BY s.logon_time DESC;
```

If direct `DBMS_CLOUD_AI.GENERATE` succeeds but this native agent test and A2A
chat both remain `RUNNING`, preserve these query results with the database
version for the database-service investigation. Do not treat it as an OAuth
or local-console failure.

For a complete end-user-to-ADMIN SQLcl walkthrough of the installed tools,
agent, task, team, and a `RUNNING` investigation, see
[AGENT_TEAM_RUNBOOK.md](AGENT_TEAM_RUNBOOK.md).

## Test log

Use this section for environment-specific prompts, expected results, and SQL
observations. Do not record credentials or tokens.
