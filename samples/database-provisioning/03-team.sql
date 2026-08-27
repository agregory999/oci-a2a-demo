BEGIN
  FOR name IN (SELECT 'DATABASE_PROVISIONING_TEAM' n, 'TEAM' k FROM dual UNION ALL SELECT 'PROVISION_DATABASE_TASK','TASK' FROM dual UNION ALL SELECT 'ADBS_PROVISIONING_TOOL','TOOL' FROM dual UNION ALL SELECT 'LIST_SUBSCRIBED_REGIONS_TOOL','TOOL' FROM dual UNION ALL SELECT 'DATABASE_ADVISOR','AGENT' FROM dual) LOOP
    BEGIN
      CASE name.k WHEN 'TEAM' THEN DBMS_CLOUD_AI_AGENT.DROP_TEAM(name.n); WHEN 'TASK' THEN DBMS_CLOUD_AI_AGENT.DROP_TASK(name.n, TRUE); WHEN 'TOOL' THEN DBMS_CLOUD_AI_AGENT.DROP_TOOL(name.n); WHEN 'AGENT' THEN DBMS_CLOUD_AI_AGENT.DROP_AGENT(name.n); END CASE;
    EXCEPTION WHEN OTHERS THEN NULL;
    END;
  END LOOP;
  DBMS_CLOUD_AI_AGENT.CREATE_AGENT(
    agent_name => 'DATABASE_ADVISOR',
    attributes => '{"profile_name":"{{PROFILE_NAME}}","role":"You are a Senior Oracle AI Database Architect. Gather requirements, explain the proposed configuration, and never provision until the user confirms.","enable_human_tool":true}',
    status => 'ENABLED',
    description => 'Database provisioning advisor'
  );
  DBMS_CLOUD_AI_AGENT.CREATE_TOOL(
    tool_name => 'LIST_SUBSCRIBED_REGIONS_TOOL',
    attributes => '{"instruction":"List subscribed OCI regions for the tenancy.","function":"list_subscribed_regions"}',
    status => 'ENABLED',
    description => 'Lists subscribed OCI regions'
  );
  DBMS_CLOUD_AI_AGENT.CREATE_TOOL(
    tool_name => 'ADBS_PROVISIONING_TOOL',
    attributes => '{"instruction":"Provision an Autonomous AI Database only after explicit confirmation.","function":"provision_adbs_tool"}',
    status => 'ENABLED',
    description => 'Provisions an Autonomous AI Database'
  );
  DBMS_CLOUD_AI_AGENT.CREATE_TASK(
    task_name => 'PROVISION_DATABASE_TASK',
    attributes => q'~{"instruction":"Help provision an Oracle Autonomous AI Database. Use LIST_SUBSCRIBED_REGIONS_TOOL to choose a subscribed region. Gather all required values, summarize the proposal, and use the human-input tool to request final confirmation before using ADBS_PROVISIONING_TOOL. Do not complete the task merely by asking for confirmation: pause it for the user's input. User request: {query}","tools":["LIST_SUBSCRIBED_REGIONS_TOOL","ADBS_PROVISIONING_TOOL"],"enable_human_tool":true}~',
    status => 'ENABLED',
    description => 'Interactive database provisioning task'
  );
  DBMS_CLOUD_AI_AGENT.CREATE_TEAM(
    team_name => 'DATABASE_PROVISIONING_TEAM',
    attributes => '{"agents":[{"name":"DATABASE_ADVISOR","task":"PROVISION_DATABASE_TASK"}],"process":"sequential"}',
    status => 'ENABLED',
    description => 'Human-confirmed Autonomous Database provisioning team'
  );
  -- CREATE_TEAM above sets status => ENABLED. Calling ENABLE_TEAM again raises
  -- ORA-20053 when the team is already in that desired state.
END;
/
