# SQL Validation Companion

Use this guide alongside the application to inspect each step from SQLcl. It
follows the left navigation: **Setup** first, then **A2A testing**, then
cleanup. Each section names the UI page, database user, and whether the SQL is
read-only **VERIFY** or an optional change-making **ACTION**.

Replace `DEMO_SALES`, `PROV2`, `A2A_PROFILE`, and `A2ADEMO_LOW` with your own
values. Do not put passwords, private keys, OAuth codes/tokens, or provider
keys in a checked-in script.

## Setup → Infra: Wallet and ADMIN connection

Install SQLcl from Oracle's [SQLcl download and installation
page](https://www.oracle.com/database/sqldeveloper/technologies/sqlcl/). The
app stores wallets under the ignored `wallet/` directory. Use the wallet's
service alias, such as `A2ADEMO_LOW`.


### Setup → Wallet / ADMIN / Target connection

Once the wallet has been downloaded within the project, you will see it in the filesystem as `<project>/wallet/ocid/wallet.zip`.  SQLcL requires this path as a cloudconfig, and then connections as any user are allowed.

Once the database is running, the wallet is downloaded, and a target schema has been created in the app, the following should work from SQLcL command line:

```sql
prompt> sql /nolog
set cloudconfig /absolute/path/to/wallet.zip
CONNECT ADMIN@A2ADEMO_LOW

SELECT SYS_CONTEXT('USERENV', 'SESSION_USER') AS session_user,
       SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA') AS current_schema
FROM dual;

CONNECT S1@A2ADEMO_LOW

SELECT SYS_CONTEXT('USERENV', 'SESSION_USER') AS session_user,
       SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA') AS current_schema
FROM dual;
```

This verifies that both ADMIN and target user exist with the correct password.

You can and should maintain 2 windows or tabs for SQLcL, as the rest of this guide refers to queries by 
which user should run them.  Pay close attention to the correct user to run as.

## Setup → Target schema and Target privileges

### UI: Target schema

As `ADMIN`, this will verify the existence of the target schema

```sql
SELECT username, account_status, created
FROM dba_users
WHERE username = 'S1';
```

### UI: Target privileges

As `ADMIN`, this will verify the grants for the target schema

```sql
SELECT table_name AS object_name, privilege
FROM dba_tab_privs
WHERE grantee = 'S1'
  AND privilege = 'EXECUTE'
  AND table_name IN (
    'DBMS_CLOUD', 'DBMS_CLOUD_AI',
    'DBMS_CLOUD_AI_AGENT', 'DBMS_CLOUD_PIPELINE'
  )
ORDER BY object_name;
```

If you need to apply a grant, the following will work:

```sql
GRANT EXECUTE ON DBMS_CLOUD_AI TO S1;
GRANT EXECUTE ON DBMS_CLOUD_AI_AGENT TO S1;
GRANT EXECUTE ON DBMS_CLOUD_PIPELINE TO S1;
```

## Setup → Resource Principal

Resource Principal is optional for API-key Select AI profiles but required for
the Resource Principal profile option and Database Provisioning package.
Complete OCI IAM/dynamic-group preparation first; see
[DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md).

Once the Resource Principal page in the UI has been completed, verify in SQL as follows.

### UI: Resource Principal — ADMIN

As `ADMIN`, this will verify whether Resource Principal is enabled

```sql
SELECT owner, credential_name
FROM dba_credentials
WHERE owner = 'ADMIN'
  AND credential_name = 'OCI$RESOURCE_PRINCIPAL';
```

If Resource Principal is enabled, you will see:

```OWNER    CREDENTIAL_NAME           
________ _________________________ 
ADMIN    OCI$RESOURCE_PRINCIPAL   
```

### UI: Resource Principal — Target

As `ADMIN`, this will verify whether Resource Principal is enabled for the target schema

```sql
SELECT grantee, owner, table_name, privilege, grantor
FROM dba_tab_privs
WHERE owner = 'ADMIN'
  AND table_name = 'OCI$RESOURCE_PRINCIPAL'
  AND grantee = 'S1';
```

Correct a missing Resource Principal:

```sql
EXEC DBMS_CLOUD_ADMIN.ENABLE_RESOURCE_PRINCIPAL();
EXEC DBMS_CLOUD_ADMIN.ENABLE_RESOURCE_PRINCIPAL(username => 'S1');
```

## Setup → Target Select AI

As the target schema owner, this verifies the Select AI Profile and Credential after creation on the "Target Select AI" page in the UI.

### UI: Target Select AI — credential and profile

Run these as the target schema user.

Credentials (May not exist if you use Resource Principal):
```sql
SELECT credential_name FROM user_credentials ORDER BY credential_name;
```

Select AI Profile:
```sql
SELECT profile_name, status
FROM user_cloud_ai_profiles
WHERE profile_name = 'A2A_PROFILE';
```
Example output:
```
PROFILE_NAME    STATUS     
_______________ __________ 
A2A_PROFILE     ENABLED   
```

Profile Attributes:
```sql
SELECT attribute_name, attribute_value
FROM user_cloud_ai_profile_attributes
WHERE profile_name = 'A2A_PROFILE'
ORDER BY attribute_name;
```

Example output:
```
ATTRIBUTE_NAME        ATTRIBUTE_VALUE                                                                     
_____________________ ___________________________________________________________________________________ 
credential_name       OCI$RESOURCE_PRINCIPAL                                                              
model                 xai.grok-4.3                                                                        
oci_compartment_id    ocid1.compartment.oc1..xxxxxx    
provider              oci                                                                                 
region                us-ashburn-1   
```

An API-key profile normally has a user credential such as `GENAI_CRED`.
Resource Principal profiles use `OCI$RESOURCE_PRINCIPAL` but do not create a
user-owned credential row.

### UI: Target Select AI — live profile test

As target user, verify the Select AI Profile, Credential, and Model all in a single query.

```sql
SELECT DBMS_CLOUD_AI.GENERATE(
  prompt       => 'Reply exactly: Target Select AI works.',
  profile_name => 'A2A_PROFILE',
  action       => 'chat'
) AS model_response
FROM dual;
```

Expected Output:
```
MODEL_RESPONSE             
__________________________ 
Target Select AI works.    
```

**IMPORTANT:** If this is not working, or hangs, stop here and fix it before continuing.  You may need to try a different model, test GenAI chat in the OCI Console "Playground" for your region, set up additional IAM permissions for Resource Principal, or verify an API Key.  

## Setup → Published teams

Published-team refresh is external A2A discovery and requires OAuth; it is not
a dictionary view. First validate the owning schema's installed team.

### UI: Published teams — installed team

**VERIFY — target schema.**

```sql
SET LONG 1000000
SET LONGCHUNKSIZE 32767
SET PAGESIZE 0
SET LINESIZE 32767

SELECT DBMS_CLOUD_AI_AGENT.LIST_TEAMS() AS available_teams FROM dual;
```

For Sales Data:

```sql
SELECT DBMS_CLOUD_AI_AGENT.GET_DEFINITION(
  object_type => 'AGENT', object_name => 'ORACLE_AI_DATABASE_AGENT_ROLE'
) FROM dual;
SELECT DBMS_CLOUD_AI_AGENT.GET_DEFINITION(
  object_type => 'TASK', object_name => 'ORACLE_AI_DATABASE_TASK'
) FROM dual;
SELECT DBMS_CLOUD_AI_AGENT.GET_DEFINITION(
  object_type => 'TEAM', object_name => 'ORACLE_AI_DATABASE_AGENT'
) FROM dual;
```

For Database Provisioning, substitute `DATABASE_ADVISOR`,
`PROVISION_DATABASE_TASK`, and `DATABASE_PROVISIONING_TEAM`.

**VERIFY — ADMIN.** Cross-schema status and compiler diagnostics:

```sql
SELECT owner, object_name, object_type, status
FROM dba_objects
WHERE owner IN ('DEMO_SALES', 'PROV2')
  AND object_name IN (
    'SELECTAI_AGENT_CONFIG', 'ORACLE_AI_DATA_RETRIEVAL_FUNCTIONS',
    'ORACLE_AI_DATABASE_AGENT_ROLE', 'ORACLE_AI_DATABASE_TASK',
    'ORACLE_AI_DATABASE_AGENT', 'DATABASE_ADVISOR',
    'PROVISION_DATABASE_TASK', 'DATABASE_PROVISIONING_TEAM',
    'LIST_SUBSCRIBED_REGIONS', 'LIST_ADBS_PROVISIONING_OPTIONS',
    'ADBS_PROVISIONING_TOOL', 'LOG_ADBS_PROVISIONING_REQUEST',
    'ADBS_PROVISIONING_REQUEST_LOG'
  )
ORDER BY owner, object_type, object_name;

SELECT owner, name, type, line, position, text
FROM dba_errors
WHERE owner IN ('DEMO_SALES', 'PROV2')
  AND name IN ('ORACLE_AI_DATA_RETRIEVAL_FUNCTIONS', 'PROVISION_ADBS_TOOL')
ORDER BY owner, name, sequence;
```

## Setup → Select AI Config

`SELECTAI_AGENT_CONFIG` is an optional, sample-owned table. It is not created
by Select AI profile setup and will not exist for every Oracle sample. Use the
**Select AI Agent Configs** page only after the sample's documented SQLcl
installation has created this table.

### UI: Select AI Agent Configs — inspect mappings

**VERIFY — target schema.** Confirm whether the installed sample owns the
optional configuration table, then display the agent-specific values it uses.

This verifies if the table exists:
```sql
SELECT table_name
FROM user_tables
WHERE table_name = 'SELECTAI_AGENT_CONFIG';
```

Keys in the table if it exists will map to the JSON that some of the samples ask for.  The app also
exposes the ability to update or add keys.  These can be read by the functions, agents, or teams.
```sql
SELECT "AGENT", "KEY", "VALUE"
FROM selectai_agent_config
ORDER BY "AGENT", "KEY";
```
Example:
```
AGENT                 KEY                          VALUE                     
_____________________ ____________________________ _________________________ 
OCI_OBJECT_STORAGE    CREDENTIAL_NAME              OCI$RESOURCE_PRINCIPAL    
OCI_OBJECT_STORAGE    ENABLE_RESOURCE_PRINCIPAL    YES       
```

To look for or verify a specific key:

```sql
SELECT "VALUE" AS profile_name
FROM selectai_agent_config
WHERE "AGENT" = 'OCI_OBJECT_STORAGE'
  AND "KEY" = 'CREDENTIAL_NAME';
```
Example:
```
PROFILE_NAME              
_________________________ 
OCI$RESOURCE_PRINCIPAL    
```

### UI: Select AI Agent Configs — add or update a key

**ACTION — target schema.** These statements affect only one named agent/key
pair; they do not replace all configuration. First inspect the mapping above.
Use `INSERT` when the key is new, or `UPDATE` when it already exists. Replace
all example values; use secret OCIDs rather than secret material.

Add a new key:

```sql
INSERT INTO selectai_agent_config ("KEY", "VALUE", "AGENT")
VALUES ('MY_CONFIG_KEY', 'my value', 'ORACLE_AI_DATABASE_AGENT');

COMMIT;
```

Change an existing key:

```sql
UPDATE selectai_agent_config
SET "VALUE" = 'A2A_PROFILE'
WHERE "AGENT" = 'ORACLE_AI_DATABASE_AGENT'
  AND "KEY" = 'AGENT_AI_PROFILE';

COMMIT;
```

SQLcl reports the number of updated rows. If `UPDATE` reports `0 rows`, the
key does not exist; use the `INSERT` example instead.

Verify the exact row after either change:

```sql
SELECT "AGENT", "KEY", "VALUE"
FROM selectai_agent_config
WHERE "AGENT" = 'ORACLE_AI_DATABASE_AGENT'
  AND "KEY" = 'AGENT_AI_PROFILE';
```

### UI: Select AI Agent Configs — delete a key

**ACTION — target schema.** Inspect the row first, then delete only the exact
agent/key pair. Deleting `AGENT_AI_PROFILE` can make an installed sample fail
until its correct profile mapping is restored.

```sql
SELECT "AGENT", "KEY", "VALUE"
FROM selectai_agent_config
WHERE "AGENT" = 'ORACLE_AI_DATABASE_AGENT'
  AND "KEY" = 'AGENT_AI_PROFILE';

DELETE FROM selectai_agent_config
WHERE "AGENT" = 'ORACLE_AI_DATABASE_AGENT'
  AND "KEY" = 'AGENT_AI_PROFILE';

COMMIT;
```

## A2A Testing → OAuth, Team selection, and Chat

### UI: OAuth · Get Token

OAuth callback/exchange are intentionally not reproduced in SQL. Follow
[OAUTH_VALIDATION.md](OAUTH_VALIDATION.md) for the command-line flow. The
external token must belong to the target schema that owns the team.

### UI: Team selection — native agent call

To test team existence, use the target schema as the user.

```sql
SELECT DBMS_CLOUD_AI_AGENT.LIST_TEAMS() AS available_teams FROM dual;
```
Example:
```
AVAILABLE_TEAMS                                         
_______________________________________________________ 
[{"name":"OCI_OBJECTSTORE_TEAM","description":null}]    
```

Now you can select the team and test the AI Profile and Credential by asking a question.

```sql
EXEC DBMS_CLOUD_AI_AGENT.SET_TEAM('OCI_OBJECTSTORE_TEAM');
select ai agent who are you;
```
Example:
```
SQL> EXEC DBMS_CLOUD_AI_AGENT.SET_TEAM('OCI_OBJECTSTORE_TEAM');

PL/SQL procedure successfully completed.

SQL> select ai agent who are you;

RESPONSE                                                                                                                                                                                                                                                                                 
________________________________________________________________________________________________________________________________________________________________________________________________________________________________________________________________________________________ 
I am OCI_OBJECT_STORAGE_ADVISOR, an OCI Object Storage Advisor and Automation Specialist. I assist with bucket and object management, lifecycle policies, retention rules, replication, multipart uploads, and work request monitoring in Oracle Cloud Infrastructure Object Storage.  
```

### UI: Chat — task and tool history

As the target user, you can see the chat history.  For example, if you chat with the agent in the app,
you will see that those results here.

```sql
SELECT * FROM user_ai_agent_team_history
ORDER BY start_date DESC FETCH FIRST 5 ROWS ONLY;
```
Executed tasks appear here:
```sql
SELECT * FROM user_ai_agent_task_history
ORDER BY start_date DESC FETCH FIRST 10 ROWS ONLY;
```
Tool calls from the agent:
```sql
SELECT * FROM user_ai_agent_tool_history
ORDER BY start_date DESC FETCH FIRST 30 ROWS ONLY;
```

Narrow down to a specific conversation by using the EXEC_ID shown for a specific conversation:
```sql
SELECT * FROM user_ai_agent_task_history
WHERE team_exec_id = 'paste-team-exec-id-here'
ORDER BY start_date;

SELECT * FROM user_ai_agent_tool_history
WHERE team_exec_id = 'paste-team-exec-id-here'
ORDER BY start_date;
```

Read it as `team history → task history → tool history → function result`.
No tool row means a task is still reasoning, waiting for human input, or
failed before selecting a tool; it does not identify a broken tool.

**VERIFY — ADMIN.** Cross-schema task correlation and a running-call check:

```sql
SELECT * FROM dba_ai_agent_task_history
WHERE task_owner = 'DEMO_SALES'
  AND team_exec_id = 'paste-team-exec-id-here'
ORDER BY start_date DESC;

SELECT * FROM dba_ai_agent_tool_history
WHERE tool_owner = 'DEMO_SALES'
  AND team_exec_id = 'paste-team-exec-id-here'
ORDER BY start_date DESC;

SELECT owner, job_name, session_id, running_instance, elapsed_time
FROM dba_scheduler_running_jobs
ORDER BY elapsed_time DESC;

SELECT sid, serial#, username, status, event, wait_class,
       seconds_in_wait, sql_id, module, action
FROM v$session
WHERE username IN ('DEMO_SALES', 'PROV2', 'ADMIN')
ORDER BY logon_time DESC;
```

If a release has different history columns, inspect first:

```sql
SELECT table_name, column_name, column_id, data_type
FROM all_tab_columns
WHERE table_name IN (
  'USER_AI_AGENT_TEAM_HISTORY', 'USER_AI_AGENT_TASK_HISTORY',
  'USER_AI_AGENT_TOOL_HISTORY', 'DBA_AI_AGENT_TEAM_HISTORY',
  'DBA_AI_AGENT_TASK_HISTORY', 'DBA_AI_AGENT_TOOL_HISTORY'
)
ORDER BY table_name, column_id;
```

## Setup → cleanup pages

Follow the UI cleanup order: delete team, clean target resources, then delete
the schema/database when testing is complete.

### Delete Team or Agent (not in UI)

The UI does not specifically surface a way to delete a team or agent, or the associated tools. 
However, this guide shows some queries to do so here.  This is primarily for debugging or retrying
configuration.  

```sql
SELECT DBMS_CLOUD_AI_AGENT.LIST_TEAMS() AS available_teams FROM dual;
```

### UI: Delete target schema

As `ADMIN` user: 
The UI uses`DROP USER <selected schema> CASCADE`

**NOTE:** Any OCI IAM policies remain outside the database and are not affected.

As `ADMIN` you can check for users after the delete schema operation from the app has completed - in this example you are checking for 2 separate target schemas.

```sql
SELECT username, account_status
FROM dba_users
WHERE username IN ('S1', 'S2');
```

If a user exists, you can also check for objects owned by that user.  Depending on the 
sample and what objects are created, you may see different types of objects.
```sql
SELECT owner, object_name, object_type, status
FROM dba_objects
WHERE owner IN ('S1', 'S3')
ORDER BY owner, object_type, object_name;
```

For example, if the Object Storage sample was installed:
```
OWNER    OBJECT_NAME                                OBJECT_TYPE                STATUS     
________ __________________________________________ __________________________ __________ 
S3       SELECTAI_AGENT_CONFIG_PK                   INDEX                      VALID      
S3       SELECTAI_AGENT_CONFIG_UK                   INDEX                      VALID      
S3       SYS_IL0000147393C00003$$                   INDEX                      VALID      
S3       OCI_OBJECTSTORE_TEAM_TASK_0                JOB                        VALID      
S3       SYS_LOB0000147393C00003$$                  LOB                        VALID      
S3       OCI_OBJECT_STORAGE_AGENTS                  PACKAGE                    VALID      
S3       OCI_OBJECT_STORAGE_AGENTS                  PACKAGE BODY               VALID      
S3       INITIALIZE_OBJECT_STORAGE_AGENT            PROCEDURE                  INVALID    
S3       INITIALIZE_OBJECT_STORAGE_TOOLS            PROCEDURE                  VALID      
S3       INSTALL_OCI_OBJECTSTORE_AGENT              PROCEDURE                  VALID      
S3       OCI_OBJECTSTORE_TEAM_SEQUENTIAL_PROGRAM    PROGRAM                    VALID      
S3       ISEQ$$_147393                              SEQUENCE                   VALID      
S3       AI$A2A_PROFILE                             SQL TRANSLATION PROFILE    VALID      
S3       AI$AGENT$OCI_OBJECTSTORE_TEAM              SQL TRANSLATION PROFILE    VALID      

OWNER    OBJECT_NAME              OBJECT_TYPE    STATUS    
________ ________________________ ______________ _________ 
S3       SELECTAI_AGENT_CONFIG    TABLE          VALID     

15 rows selected. 
```

**NOTE:** Manually dropping a user with cascade is safe for the demo, as long as you then create a 
new target schema and connect to it from the app.

### UI: Resource Principal cleanup

Run as `ADMIN`:

```sql
EXEC DBMS_CLOUD_ADMIN.DISABLE_RESOURCE_PRINCIPAL(username => 'DEMO_SALES');
```
Check again for resource principal, looking for the target user:
```sql
SELECT grantee, owner, table_name
FROM dba_tab_privs
WHERE owner = 'ADMIN'
  AND table_name = 'OCI$RESOURCE_PRINCIPAL';
```
Example output (showing S1 user):
```GRANTEE             OWNER    TABLE_NAME                
___________________ ________ _________________________ 
C##CLOUD$SERVICE    ADMIN    OCI$RESOURCE_PRINCIPAL    
GRAPH$METADATA      ADMIN    OCI$RESOURCE_PRINCIPAL    
ODI_REPO_USER       ADMIN    OCI$RESOURCE_PRINCIPAL    
S1                  ADMIN    OCI$RESOURCE_PRINCIPAL    
```

To remove ALL resource principals:
```sql
EXEC DBMS_CLOUD_ADMIN.DISABLE_RESOURCE_PRINCIPAL();
```
Verify:
```sql
SELECT owner, credential_name
FROM dba_credentials
WHERE credential_name = 'OCI$RESOURCE_PRINCIPAL';
```
Should show:
```
no rows selected
```

### UI: Delete Database

Use the UI’s **Delete database** page, OCI Console, or OCI CLI only after
retaining needed diagnostics and disposing of local wallet files securely.

You must disconnect from all SQLcL sessions before doing this.
