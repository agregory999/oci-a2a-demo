CREATE OR REPLACE FUNCTION {{TARGET_SCHEMA}}.LIST_SUBSCRIBED_REGIONS
RETURN CLOB AUTHID CURRENT_USER
IS
  l_response DBMS_CLOUD_TYPES.resp;
  l_body CLOB;
  l_status PLS_INTEGER;
BEGIN
  l_response := DBMS_CLOUD.SEND_REQUEST(
    credential_name => 'OCI$RESOURCE_PRINCIPAL',
    uri => 'https://identity.{{IDENTITY_REGION}}.oraclecloud.com/20160918/tenancies/{{TENANCY_OCID}}/regionSubscriptions',
    method => DBMS_CLOUD.METHOD_GET
  );
  l_status := DBMS_CLOUD.GET_RESPONSE_STATUS_CODE(l_response);
  l_body := DBMS_CLOUD.GET_RESPONSE_TEXT(l_response);
  IF l_status <> 200 THEN
    RAISE_APPLICATION_ERROR(-20006, 'OCI ListRegionSubscriptions failed (HTTP ' || l_status || '): ' || DBMS_LOB.SUBSTR(l_body, 1800, 1));
  END IF;
  RETURN l_body;
END;
/

CREATE OR REPLACE FUNCTION {{TARGET_SCHEMA}}.PROVISION_ADBS_TOOL (
  p_region IN VARCHAR2, p_db_name IN VARCHAR2, p_display_name IN VARCHAR2,
  p_workload IN VARCHAR2, p_ecpu_count IN NUMBER, p_storage_tbs IN NUMBER,
  p_enable_data_guard IN VARCHAR2, p_storage_unit IN VARCHAR2 DEFAULT 'TB'
) RETURN CLOB AUTHID CURRENT_USER
IS
  l_request DBMS_CLOUD_OCI_DATABASE_CREATE_AUTONOMOUS_DATABASE_DETAILS_T := DBMS_CLOUD_OCI_DATABASE_CREATE_AUTONOMOUS_DATABASE_DETAILS_T();
  l_response DBMS_CLOUD_OCI_DB_DATABASE_CREATE_AUTONOMOUS_DATABASE_RESPONSE_T;
  l_result JSON_OBJECT_T := JSON_OBJECT_T();
  l_workload VARCHAR2(10) := UPPER(TRIM(p_workload));
  l_unit VARCHAR2(2) := NVL(UPPER(TRIM(p_storage_unit)), 'TB');
  l_request_id VARCHAR2(64) := RAWTOHEX(SYS_GUID());
BEGIN
  IF NOT REGEXP_LIKE(LOWER(TRIM(p_region)), '^[a-z]+-[a-z]+-[0-9]+$') THEN RAISE_APPLICATION_ERROR(-20001, 'Invalid OCI region.'); END IF;
  IF NOT REGEXP_LIKE(UPPER(TRIM(p_db_name)), '^[A-Z][A-Z0-9]{0,29}$') THEN RAISE_APPLICATION_ERROR(-20002, 'Invalid database name.'); END IF;
  IF l_workload NOT IN ('OLTP','DW') OR p_ecpu_count <= 0 OR p_storage_tbs <= 0 THEN RAISE_APPLICATION_ERROR(-20003, 'Invalid workload, ECPU count, or storage.'); END IF;
  IF l_unit NOT IN ('GB','TB') OR (l_unit = 'GB' AND (l_workload <> 'OLTP' OR p_storage_tbs < 20)) THEN RAISE_APPLICATION_ERROR(-20004, 'GB storage is OLTP-only and must be at least 20 GB.'); END IF;
  IF UPPER(TRIM(p_enable_data_guard)) NOT IN ('YES','NO') THEN RAISE_APPLICATION_ERROR(-20005, 'Data Guard must be YES or NO.'); END IF;
  l_request.compartment_id := '{{ALLOWED_COMPARTMENT_ID}}';
  l_request.db_name := UPPER(TRIM(p_db_name));
  l_request.display_name := NVL(TRIM(p_display_name), UPPER(TRIM(p_db_name)));
  l_request.db_workload := l_workload;
  l_request.compute_model := 'ECPU';
  l_request.compute_count := p_ecpu_count;
  l_request.license_model := 'LICENSE_INCLUDED';
  l_request.is_auto_scaling_enabled := 0;
  l_request.is_local_data_guard_enabled := CASE WHEN UPPER(TRIM(p_enable_data_guard)) = 'YES' THEN 1 ELSE 0 END;
  l_request.secret_id := '{{ADMIN_PASSWORD_SECRET_ID}}';
  IF l_unit = 'GB' THEN l_request.data_storage_size_in_g_bs := p_storage_tbs; ELSE l_request.data_storage_size_in_t_bs := p_storage_tbs; END IF;
  l_response := DBMS_CLOUD_OCI_DB_DATABASE.CREATE_AUTONOMOUS_DATABASE(
    create_autonomous_database_details => l_request, opc_retry_token => l_request_id,
    opc_request_id => l_request_id, region => LOWER(TRIM(p_region)), credential_name => 'OCI$RESOURCE_PRINCIPAL');
  l_result.put('result', 'CreateAutonomousDatabase submitted');
  l_result.put('request_id', l_request_id);
  l_result.put('http_status', l_response.status_code);
  l_result.put('response_headers', l_response.headers);
  RETURN l_result.to_clob;
END;
/
