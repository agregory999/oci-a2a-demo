-- Reset only the Oracle AI Database Agent sample artifacts.
--
-- Run from SQLcl as ADMIN.  This is intentionally destructive for the named
-- sample tools, task, agent, team, package, and installer procedures.  It
-- preserves the target user's credential, Select AI profile, sample data,
-- SELECTAI_AGENT_CONFIG table, grants, and OAuth registrations.
--
-- Follow this with Oracle's current oracle_ai_database_agent_tool.sql and
-- oracle_ai_database_agent.sql scripts, both run as ADMIN with the same
-- target schema and A2A_PROFILE.

SET SERVEROUTPUT ON SIZE UNLIMITED
SET VERIFY OFF

ACCEPT target_schema CHAR PROMPT 'Target schema to reset (for example DEMO_SALES): '

DECLARE
  l_target_schema VARCHAR2(128) := DBMS_ASSERT.SIMPLE_SQL_NAME(UPPER('&target_schema'));

  PROCEDURE note(p_message IN VARCHAR2) IS
  BEGIN
    DBMS_OUTPUT.PUT_LINE(p_message);
  END;

  PROCEDURE set_current_schema(p_schema IN VARCHAR2) IS
  BEGIN
    EXECUTE IMMEDIATE 'ALTER SESSION SET CURRENT_SCHEMA = ' || p_schema;
    note('Current schema: ' || p_schema);
  END;

  PROCEDURE reset_registry(p_schema IN VARCHAR2) IS
    PROCEDURE try_drop(p_kind IN VARCHAR2, p_name IN VARCHAR2) IS
    BEGIN
      CASE p_kind
        WHEN 'TEAM' THEN DBMS_CLOUD_AI_AGENT.DROP_TEAM(p_name);
        WHEN 'AGENT' THEN DBMS_CLOUD_AI_AGENT.DROP_AGENT(p_name);
        WHEN 'TASK' THEN DBMS_CLOUD_AI_AGENT.DROP_TASK(p_name);
        WHEN 'TOOL' THEN DBMS_CLOUD_AI_AGENT.DROP_TOOL(p_name);
      END CASE;
      note('Dropped ' || p_kind || ' ' || p_name || ' from ' || p_schema);
    EXCEPTION
      WHEN OTHERS THEN
        note('Did not drop ' || p_kind || ' ' || p_name || ' from ' || p_schema || ': ' || SQLERRM);
    END;
  BEGIN
    set_current_schema(p_schema);
    try_drop('TEAM',  'ORACLE_AI_DATABASE_AGENT');
    try_drop('AGENT', 'ORACLE_AI_DATABASE_AGENT_ROLE');
    try_drop('TASK',  'ORACLE_AI_DATABASE_TASK');
    try_drop('TOOL',  'SQL_TOOL');
    try_drop('TOOL',  'DISTINCT_VALUES_CHECK');
    try_drop('TOOL',  'RANGE_VALUES_CHECK');
    try_drop('TOOL',  'GENERATE_CHART');
  END;

  PROCEDURE try_drop_ddl(p_statement IN VARCHAR2) IS
  BEGIN
    EXECUTE IMMEDIATE p_statement;
    note('Executed: ' || p_statement);
  EXCEPTION
    WHEN OTHERS THEN
      note('Did not execute ' || p_statement || ': ' || SQLERRM);
  END;

BEGIN
  -- The official scripts run as ADMIN but set CURRENT_SCHEMA to the target.
  -- Try both locations because existing installs can have release-specific
  -- catalog ownership behavior. The names are exact Oracle sample names.
  reset_registry(l_target_schema);
  IF l_target_schema <> 'ADMIN' THEN
    reset_registry('ADMIN');
  END IF;

  -- Clear only the sample's profile mapping; preserve the configuration table.
  BEGIN
    EXECUTE IMMEDIATE
      'DELETE FROM ' || l_target_schema || '.SELECTAI_AGENT_CONFIG '
      || 'WHERE "KEY" = ''AGENT_AI_PROFILE'' '
      || 'AND "AGENT" = ''ORACLE_AI_DATABASE_AGENT''';
    note('Cleared the sample profile mapping from ' || l_target_schema || '.SELECTAI_AGENT_CONFIG');
  EXCEPTION
    WHEN OTHERS THEN
      note('Did not clear the sample profile mapping: ' || SQLERRM);
  END;

  -- Target-schema artifacts created after ALTER SESSION SET CURRENT_SCHEMA.
  try_drop_ddl('DROP PROCEDURE ' || l_target_schema || '.DATA_RETRIEVAL_AGENT');
  try_drop_ddl('DROP PROCEDURE ' || l_target_schema || '.INITIALIZE_ORACLE_AI_DATA_RETRIEVAL_TOOLS');
  try_drop_ddl('DROP PACKAGE ' || l_target_schema || '.ORACLE_AI_DATA_RETRIEVAL_FUNCTIONS');

  -- The first helper procedure in the tool script is created before its
  -- CURRENT_SCHEMA switch, so it is normally ADMIN-owned.
  try_drop_ddl('DROP PROCEDURE ADMIN.INITIALIZE_ORACLE_AI_DATA_RETRIEVAL_AGENT');

  EXECUTE IMMEDIATE 'ALTER SESSION SET CURRENT_SCHEMA = ADMIN';
  COMMIT;
  note('Sample reset complete. Target data, credential, profile, and grants were preserved.');
END;
/

-- Post-reset inspection: all rows below should be absent for the target and ADMIN.
SELECT owner, object_name, object_type, status
FROM dba_objects
WHERE owner IN (UPPER('&target_schema'), 'ADMIN')
  AND object_name IN (
    'DATA_RETRIEVAL_AGENT',
    'INITIALIZE_ORACLE_AI_DATA_RETRIEVAL_AGENT',
    'INITIALIZE_ORACLE_AI_DATA_RETRIEVAL_TOOLS',
    'ORACLE_AI_DATA_RETRIEVAL_FUNCTIONS'
  )
ORDER BY owner, object_name, object_type;

SET VERIFY ON
