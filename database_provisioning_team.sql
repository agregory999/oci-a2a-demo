begin
   dbms_cloud_ai_agent.drop_team(team_name => 'DATABASE_PROVISIONING_TEAM');
end;
/
begin
   dbms_cloud_ai_agent.drop_task(task_name => 'PROVISION_DATABASE_TASK');
end;
/
begin
   dbms_cloud_ai_agent.drop_tool(tool_name => 'ADBS_PROVISIONING_TOOL');
end;
/
begin
   dbms_cloud_ai_agent.drop_tool(tool_name => 'LIST_SUBSCRIBED_REGIONS_TOOL');
end;
/
begin
   dbms_cloud_ai_agent.drop_agent(agent_name => 'DATABASE_ADVISOR');
end;
/

--Recreate in order
begin
   dbms_cloud_ai_agent.create_agent(
      agent_name  => 'DATABASE_ADVISOR',
      attributes  => '{"profile_name": "A2A_PROFILE",
                    "role": "You are a Senior Oracle AI Database Architect with expertise in Autonomous AI Database best practices, ' || 'security configurations, and high availability solutions."}'
      ,
      description => 'Expert database advisor for provisioning Autonomous AI Databases'
   );
end;
/
begin
   dbms_cloud_ai_agent.create_tool(
      tool_name   => 'LIST_SUBSCRIBED_REGIONS_TOOL',
      attributes  => '{"instruction": "This tool lists all Oracle Cloud regions that are subscribed by current user tenancy. ' || 'It helps users choose which region to deploy their Autonomous AI Database. ",
                    "function" : "list_subscribed_regions"}',
      description => 'Tool for listing Oracle Cloud subscribed regions'
   );
end;
/
begin
   dbms_cloud_ai_agent.create_tool(
      tool_name   => 'ADBS_PROVISIONING_TOOL',
      attributes  => '{
  "instruction": "This tool provisions an Oracle Autonomous AI Database. ",
  "function": "provision_adbs_tool"
}',
      description => 'Tool for provisioning Oracle Autonomous AI Databases'
   );
end;
/
begin
   dbms_cloud_ai_agent.create_task(
      task_name   => 'PROVISION_DATABASE_TASK',
      attributes  => '{
  "instruction": "Help the user provision an Oracle Autonomous AI Database. Use LIST_SUBSCRIBED_REGIONS_TOOL to list the regions available to the tenancy. Use ADBS_PROVISIONING_TOOL only after you have gathered all required information. Before provisioning, summarize the proposed database configuration and obtain the users final confirmation.",
  "tools": [
    "LIST_SUBSCRIBED_REGIONS_TOOL",
    "ADBS_PROVISIONING_TOOL"
  ],
  "enable_human_tool": true
}',
      description => 'Task for interactive database provisioning'
   );
end;
/

begin
   dbms_cloud_ai_agent.create_team(
      team_name  => 'DATABASE_PROVISIONING_TEAM',
      attributes => '{
  "agents": [
    {
      "name": "DATABASE_ADVISOR",
      "task": "PROVISION_DATABASE_TASK"
    }
  ],
  "process": "sequential"
}'
   );
end;
/
EXEC DBMS_CLOUD_AI_AGENT.set_team(team_name  => 'DATABASE_PROVISIONING_TEAM');