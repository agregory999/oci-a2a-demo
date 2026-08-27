-- Run as ADMIN before compiling adbs_provision.sql as DEMO_SALES.
--
-- PUBLIC synonyms make these generated OCI PL/SQL SDK objects easy to name,
-- but they do not grant DEMO_SALES the direct privileges required to compile
-- stored PL/SQL that references them.

GRANT EXECUTE ON C##CLOUD$SERVICE.DBMS_CLOUD_OCI_DB_DATABASE
    TO DEMO_SALES;

GRANT EXECUTE ON C##CLOUD$SERVICE.DBMS_CLOUD_OCI_DATABASE_CREATE_AUTONOMOUS_DATABASE_BASE_T
    TO DEMO_SALES;

GRANT EXECUTE ON C##CLOUD$SERVICE.DBMS_CLOUD_OCI_DATABASE_CREATE_AUTONOMOUS_DATABASE_DETAILS_T
    TO DEMO_SALES;

GRANT EXECUTE ON C##CLOUD$SERVICE.DBMS_CLOUD_OCI_DB_DATABASE_CREATE_AUTONOMOUS_DATABASE_RESPONSE_T
    TO DEMO_SALES;

-- Verify effective object privileges after connecting as DEMO_SALES. This is
-- preferable to treating a DBA_TAB_PRIVS lookup as the compilation test.
SELECT table_schema AS owner, table_name AS object_name, privilege, type AS object_type
FROM all_tab_privs
WHERE table_schema = 'C##CLOUD$SERVICE'
  AND table_name IN (
      'DBMS_CLOUD_OCI_DB_DATABASE',
      'DBMS_CLOUD_OCI_DATABASE_CREATE_AUTONOMOUS_DATABASE_BASE_T',
      'DBMS_CLOUD_OCI_DATABASE_CREATE_AUTONOMOUS_DATABASE_DETAILS_T',
      'DBMS_CLOUD_OCI_DB_DATABASE_CREATE_AUTONOMOUS_DATABASE_RESPONSE_T'
  )
ORDER BY object_name;
