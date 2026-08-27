# Oracle AI Database Agent Sample — SQLcl Runbook

> Scope: this runbook covers the Oracle upstream **Sales Data Query** sample.
> For the local **Database Provisioning** package, use the package section in
> `SQL_VALIDATION.md`: its objects must be owned by the provisioning schema
> (for example `ADBPROV`), and `LIST_TEAMS()` must return
> `DATABASE_PROVISIONING_TEAM` when run as that user.

This runbook explains the Oracle sample installed by the console and provides
a diagnostic test sequence that separates four things that can otherwise look
like one failed “chat”:

1. the target schema's Select AI profile and provider access;
2. the PL/SQL package and registered tools;
3. Select AI Agent task/agent/team orchestration; and
4. the external OAuth/A2A transport.

Run SQL marked **Target schema** as the schema that owns the sample objects,
for example `DEMO_SALES`. Run SQL marked **ADMIN** from a separate ADMIN SQLcl
connection. Never put passwords, signing keys, client secrets, authorization
codes, or bearer tokens in this file or its output.

The console downloads the current Oracle sample scripts at install time:

- [tool installer](https://github.com/oracle-devrel/oracle-autonomous-database-samples/blob/main/google-gemini-marketplace-agents/oracle_ai_database_agent/oracle_ai_database_agent_tool.sql)
- [agent/team installer](https://github.com/oracle-devrel/oracle-autonomous-database-samples/blob/main/google-gemini-marketplace-agents/oracle_ai_database_agent/oracle_ai_database_agent.sql)

The statements below are inspection and test statements; they do not replace
the console installer. The install scripts are idempotent by replacement: they
drop/recreate the task, role agent, team, and tool definitions.

If a sample package body is `INVALID`, do not proceed to agent chat tests.
Use [SAMPLE_AGENT_RESET.sql](SAMPLE_AGENT_RESET.sql) from SQLcl as `ADMIN` to
remove only the named sample artifacts, then run Oracle's two current sample
scripts directly from SQLcl. The reset deliberately preserves target data,
credentials, profiles, grants, and OAuth registrations.

The console now verifies the package specification and body after the tool
script. Oracle can accept `CREATE PACKAGE BODY` DDL while retaining an
`INVALID` body and its `DBA_ERRORS`; the console stops before creating the
team and records the downloaded-script SHA-256 in installer debug output.

## 1. What the sample creates

The sample creates this chain in the selected target schema:

```text
External A2A message / SELECT AI AGENT prompt
                  │
                  ▼
Team: ORACLE_AI_DATABASE_AGENT
  process: sequential
                  │
                  ▼
Agent: ORACLE_AI_DATABASE_AGENT_ROLE
  profile_name: A2A_PROFILE
  role: professional SQL / PL/SQL data analyst
                  │
                  ▼
Task: ORACLE_AI_DATABASE_TASK
  instruction: analyze {query}; choose tools; format response
  tools: SQL_TOOL, DISTINCT_VALUES_CHECK,
         RANGE_VALUES_CHECK, GENERATE_CHART
                  │
                  ▼
Package: ORACLE_AI_DATA_RETRIEVAL_FUNCTIONS
  reads SELECTAI_AGENT_CONFIG.AGENT_AI_PROFILE
  calls DBMS_CLOUD_AI.GENERATE and/or runs returned SQL
                  │
                  ▼
Target-schema credential + A2A_PROFILE + controlled data tables
```

Names that are easy to confuse:

| Object type | Name | Purpose |
| --- | --- | --- |
| Published A2A team / SQL team | `ORACLE_AI_DATABASE_AGENT` | Entry point shown by A2A discovery and Agent Card. |
| Database agent | `ORACLE_AI_DATABASE_AGENT_ROLE` | The LLM role bound to the profile. |
| Task | `ORACLE_AI_DATABASE_TASK` | The instruction and tool list used for each prompt. |
| Configuration table | `SELECTAI_AGENT_CONFIG` | Stores the profile name for the package functions. |
| Package | `ORACLE_AI_DATA_RETRIEVAL_FUNCTIONS` | Implements the registered PL/SQL tools. |

The tool installer also creates/reuses `SELECTAI_AGENT_CONFIG`, grants the
package access the target schema needs, compiles the package, and registers
the four tools. The agent/team installer writes the chosen profile into the
configuration table, then replaces the task, agent, and team.

## 2. Target schema — prove the prerequisites first

Connect with SQLcl as the target schema. These are read-only except
`SET_PROFILE`, which changes only the current SQLcl session.

```sql
SELECT SYS_CONTEXT('USERENV', 'SESSION_USER') AS session_user
FROM dual;

SELECT profile_name, status
FROM user_cloud_ai_profiles
WHERE profile_name = 'A2A_PROFILE';

SELECT credential_name
FROM user_credentials
WHERE credential_name = 'GENAI_CRED';

SELECT attribute_name, attribute_value
FROM user_cloud_ai_profile_attributes
WHERE profile_name = 'A2A_PROFILE'
ORDER BY attribute_name;
```

Then prove the target profile can call its provider without involving an
agent, task, tool, OAuth token, or A2A endpoint:

```sql
SELECT DBMS_CLOUD_AI.GENERATE(
  prompt       => 'Reply exactly: Target Select AI works.',
  profile_name => 'A2A_PROFILE',
  action       => 'chat'
) AS provider_response
FROM dual;
```

Expected: `Target Select AI works.` A failure here is a profile, credential,
provider, endpoint, or target-schema privilege problem. It is not an A2A
problem.

For OCI profiles, an attempted URL containing literal `my$cloud_domain` means
the database did not resolve its OCI cloud-domain substitution. Capture the
profile attributes, database version, and exact error before recreating
credentials or agents.

## 3. Catalog ownership and release compatibility

Start with the view definitions. The Select AI Agent dictionary has changed
between Autonomous Database releases, so this prevents a misleading
`ORA-00904` from being interpreted as an installation failure.

```sql
SELECT table_name, column_name, column_id, data_type
FROM all_tab_columns
WHERE table_name LIKE 'USER_AI_AGENT%'
   OR table_name LIKE 'DBA_AI_AGENT%'
ORDER BY table_name, column_id;
```

Then run these column-independent target-schema queries. `SELECT *` is
deliberate: use the preceding result to choose named columns only after
confirming what this database release exposes.

```sql
SELECT * FROM user_ai_agent_tools;
SELECT * FROM user_ai_agents;
SELECT * FROM user_ai_agent_tasks;
SELECT * FROM user_ai_agent_teams;
```

An empty `USER_AI_AGENTS` result while connected as `DEMO_SALES` does **not**
by itself prove the installer failed. The current Oracle sample is launched
from an ADMIN connection, changes `CURRENT_SCHEMA`, and invokes a target-schema
definer-rights procedure. Check the catalog owner from ADMIN before taking any
corrective action:

```sql
SELECT owner, agent_name, status, created, last_modified
FROM dba_ai_agents
WHERE agent_name = 'ORACLE_AI_DATABASE_AGENT_ROLE';

SELECT *
FROM dba_ai_agent_teams
WHERE owner IN (UPPER('DEMO_SALES'), 'ADMIN');

SELECT *
FROM dba_ai_agent_tasks
WHERE owner IN (UPPER('DEMO_SALES'), 'ADMIN');
```

If the agent is owned by `ADMIN`, use the ADMIN-owned team in the DBA history
views; do not reinstall only to make `USER_AI_AGENTS` non-empty. The stronger
end-user check is `GET_DEFINITION` in the next section and then a native team
run. If it is owned by neither `DEMO_SALES` nor `ADMIN`, preserve the installer
debug trace and inspect all DBA rows before rerunning the installer.

Confirm the profile binding consumed by the package functions:

```sql
SELECT "KEY", "VALUE", "AGENT"
FROM selectai_agent_config
WHERE "AGENT" = 'ORACLE_AI_DATABASE_AGENT'
ORDER BY "KEY";
```

Expected configuration row:

```text
AGENT_AI_PROFILE | A2A_PROFILE | ORACLE_AI_DATABASE_AGENT
```

## 4. Target schema — inspect the executable definitions

These are the clearest way to see the current installed JSON and eliminate
name confusion. Set SQLcl output settings first because definitions are CLOBs.

```sql
SET LONG 1000000
SET LONGCHUNKSIZE 32767
SET LINESIZE 32767
SET PAGESIZE 0

SELECT DBMS_CLOUD_AI_AGENT.GET_DEFINITION(
  object_type => 'AGENT',
  object_name => 'ORACLE_AI_DATABASE_AGENT_ROLE'
) AS agent_definition
FROM dual;

SELECT DBMS_CLOUD_AI_AGENT.GET_DEFINITION(
  object_type => 'TASK',
  object_name => 'ORACLE_AI_DATABASE_TASK'
) AS task_definition
FROM dual;

SELECT DBMS_CLOUD_AI_AGENT.GET_DEFINITION(
  object_type => 'TEAM',
  object_name => 'ORACLE_AI_DATABASE_AGENT'
) AS team_definition
FROM dual;
```

In the agent definition, confirm `"profile_name":"A2A_PROFILE"`. In the
task definition, confirm the four expected tools. In the team definition,
confirm the role agent is paired with the task and `process` is `sequential`.

## 5. Target schema — test tool discovery before orchestration

`DESCRIBE_TOOL` is read-only and returns the exact input schema to use with
that installed release. Run it for each tool before calling a tool directly:

```sql
SELECT DBMS_CLOUD_AI_AGENT.DESCRIBE_TOOL('SQL_TOOL') FROM dual;
SELECT DBMS_CLOUD_AI_AGENT.DESCRIBE_TOOL('DISTINCT_VALUES_CHECK') FROM dual;
SELECT DBMS_CLOUD_AI_AGENT.DESCRIBE_TOOL('RANGE_VALUES_CHECK') FROM dual;
SELECT DBMS_CLOUD_AI_AGENT.DESCRIBE_TOOL('GENERATE_CHART') FROM dual;
```

`RUN_TOOL` is an execution test. `SQL_TOOL` can generate and execute SQL, so
use a clearly read-only prompt against controlled data and review the result.
First confirm its input property name from `DESCRIBE_TOOL`; the sample's
function normally exposes `USER_PROMPT`.

```sql
SELECT DBMS_CLOUD_AI_AGENT.RUN_TOOL(
  tool_name => 'SQL_TOOL',
  input     => '{"USER_PROMPT":"How many rows are in DEMO_CUSTOMERS?"}'
) AS tool_result
FROM dual;
```

If `DESCRIBE_TOOL` shows a different property name, use that exact property
instead. A successful direct tool call proves the package/config/profile path
through the first tool. It does **not** prove the task/agent orchestrator.

## 6. Target schema — run the native agent path

This starts a database Agent run without external OAuth or A2A HTTP. It is the
most important comparison when browser chat remains `RUNNING`.

```sql
EXEC DBMS_CLOUD_AI_AGENT.SET_TEAM('ORACLE_AI_DATABASE_AGENT');

SELECT DBMS_CLOUD_AI_AGENT.GET_TEAM AS current_team
FROM dual;

select ai agent who are you;
```

`SET_TEAM` affects only the current SQLcl session. The final statement creates
real agent runtime work and should return a response. Start without a question
mark because SQLcl/JDBC versions that do not recognize Select AI syntax can
interpret `?` as a bind variable.

Alternative API test for a stateless application flow. It also starts a real
team run and prints its current state:

```sql
SET SERVEROUTPUT ON
DECLARE
  l_conversation_id VARCHAR2(128);
  l_response        CLOB;
  l_state           VARCHAR2(30);
BEGIN
  l_conversation_id := DBMS_CLOUD_AI.CREATE_CONVERSATION();
  l_response := DBMS_CLOUD_AI_AGENT.RUN_TEAM(
    team_name   => 'ORACLE_AI_DATABASE_AGENT',
    user_prompt => 'Who are you',
    params      => '{"conversation_id":"' || l_conversation_id || '"}'
  );
  l_state := DBMS_CLOUD_AI_AGENT.GET_TEAM_STATE(
    team_name => 'ORACLE_AI_DATABASE_AGENT',
    params    => '{"conversation_id":"' || l_conversation_id || '"}'
  );
  DBMS_OUTPUT.PUT_LINE(l_response);
  DBMS_OUTPUT.PUT_LINE('Conversation ID: ' || l_conversation_id);
  DBMS_OUTPUT.PUT_LINE('State: ' || l_state);
END;
/
```

Possible states include `RUNNING`, `WAITING_FOR_HUMAN`, `SUCCEEDED`, and
`FAILED`. A sample task sets `enable_human_tool` false, so a normal simple
question should not pause for human input.

## 7. Target schema — see what a `RUNNING` native run has reached

After cancelling a long-running SQLcl command with `Ctrl+C`, inspect the most
recent run. These views record database-agent work, not OAuth access tokens.

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

Interpretation:

| Evidence | Meaning |
| --- | --- |
| No team-history row | The native request was not submitted. Check the SQL client/session/team selection. |
| Team and task rows are `RUNNING`; no tool row | The orchestration/initial agent reasoning stage has not invoked a registered tool. The failure is before `SQL_TOOL`. |
| Tool row exists | Inspect tool input/output and package/config/profile access. |
| `FAILED` state | Inspect the task/team row for release-specific error details and the associated tool row. |

The agent history is scheduler-backed. A `RUNNING` task can have no currently
visible foreground `DEMO_SALES` row in `V$SESSION`: task history stores the
input and result handed to the agent framework, while team history records the
associated job information. From ADMIN, inspect the matching team-history row
with `SELECT *` first, then examine current and recent scheduler activity:

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

Use the job name shown in the matching team-history row to narrow the
scheduler queries. If no matching running or completed job is present while
the task remains `RUNNING`, capture that fact with the team/task row; it is a
database agent runtime state-transition issue before tool execution.

## 8. ADMIN — inspect the same execution across users

Use ADMIN to correlate a target-user run with its owner, task records, and any
tool calls. Replace `DEMO_SALES` and the execution ID.

```sql
SELECT *
FROM dba_ai_agent_team_history
WHERE team_owner = UPPER('DEMO_SALES')
  AND team_name = 'ORACLE_AI_DATABASE_AGENT'
ORDER BY start_date DESC
FETCH FIRST 20 ROWS ONLY;

SELECT *
FROM dba_ai_agent_task_history
WHERE task_owner = UPPER('DEMO_SALES')
  AND team_exec_id = 'paste-team-exec-id-here';

SELECT *
FROM dba_ai_agent_tool_history
WHERE tool_owner = UPPER('DEMO_SALES')
  AND team_exec_id = 'paste-team-exec-id-here';
```

While a target-schema SQLcl statement is currently hung, inspect the active
session twice, around 30 seconds apart:

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

`Allocate PGA memory from OS` is an internal private-memory allocation event.
It is normally transient; by itself it is not an OAuth/network wait. If it is
repeated or remains the visible state while an agent run never reaches tool
history, capture the two snapshots with the database version and history rows.

## 9. Relate a browser chat to this runbook

The browser test path is:

```text
OAuth authorization-code token
  → A2A GET /agents/ discovery
  → Agent Card GET
  → A2A JSON-RPC message/send
  → task ID / tasks-get polling
  → database team/task/tool history
```

Therefore:

- token + discovery + Agent Card success prove the external A2A route;
- a native SQLcl `SELECT AI AGENT` run reproducing the same `RUNNING` state
  proves the issue is in the database agent runtime, not the Flask UI, OAuth,
  or A2A transport;
- direct `GENERATE` success plus no tool history narrows the issue to the
  initial agent-orchestration step; and
- a direct successful `RUN_TOOL` narrows it further to task/agent
  orchestration rather than the tool implementation.

For the companion setup and OAuth validation commands, see
[SQL_VALIDATION.md](SQL_VALIDATION.md) and
[OAUTH_VALIDATION.md](OAUTH_VALIDATION.md).
