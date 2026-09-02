# OCI Autonomous Database A2A Demo

This app was designed to ease the setup process for OCI Autonomous Database Select AI Agents.  These allow
us to set up focused, secure functions in the database, along side with a pluggable LLM model.  The result
is that users do not need to set up and secure and monitor bespoke Agentic AI Applications in the enterprise.

Read more about [Select AI Agent](https://docs.oracle.com/en-us/iaas/autonomous-database-serverless/doc/about-select-ai-agents.html)

Further, Blog-based examples ([such as this one](https://blogs.oracle.com/machinelearning+selectai/build-your-agentic-solution-using-oracle-adb-select-ai-agent)) and [this one](https://blogs.oracle.com/machinelearning+selectai/announcing-oracle-select-ai-pre-built-ai-agents) outline the types of things the database is capable of.

This project strives to combine a guided setup AND a working example of Select AI Agents with the [A2A Protocol](https://github.com/a2aproject/A2A/blob/main/docs/specification.md).  This allows external consumers, such as Google Gemini 
Enterprise, to securely consume Autonomous Database Select AI using A2A.

Guided setup includes the following:
- Provisioning OLTP instance in your tenancy with feature flag
- Preparation using ADMIN account / download of wallet
- Creation of target schema with Credential and AI Profile
- Enablement of Deployment of sample AI Agents / Team
- Creation of required OAUTH client
- Deployment of sample AI Agents / Team
- Testing console with OAuth (as target user) and chat interface
- Cleanup of all assets

While this has been tested against new Autonomous OLTP instances, it can be improved over time to support
existing instances.

## TL;DR

To get started, clone the repository, have a look around, and then use a Python Virtual Environment to run
the server.  It will run locally and then a browser is used for the rest.   

The app assumes an OCI Profile exists on your computer, and that the user in the profile is a member of a group
that has IAM policies set to allow both the management of autonomous databases in a specific compartment and the reading of Vault secrets in whichever compartment they exist in.  Follow the detailed instructions for more.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
FLASK_SECRET_KEY='long-random-value' .venv/bin/python app.py
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000) to use the tool.

The left outline is the workflow navigation. The bottom dock shows recent actions.  On each page, the 
right side contains pertinent help and overall status of team item. 


## Important boundaries

- `ADMIN` performs environment work and grants.
- The target schema owns its credential, Select AI profile, and agent objects.
- OAuth token, client secret, authorization code, passwords, and provider keys
  are never persisted by the console.
- A successful token, discovery, and Agent Card prove external A2A access.
  They do not prove the database Select AI Agent runtime can complete a task.
- Wallets are stored and unzipped under ignored `wallet/` within the project
- Non-secret form state is stored in ignored `last-settings.json`.  Passwords and OAuth client secrets must be kept elsewhere.
## Documentation

- [AGENTS.md](AGENTS.md): contributor and Codex starting point, project
  invariants, and handoff rules.
- [docs/context/INDEX.md](docs/context/INDEX.md): current project handoff and
  append-only context log for upcoming work.
- [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md): end-to-end tenancy preparation,
  local setup, application workflow, Resource Principal prerequisites, and
  cleanup.
- [UI_CONTEXT.md](UI_CONTEXT.md): UI, navigation, state, and security design.
- [AGENT_TEAM_RUNBOOK.md](AGENT_TEAM_RUNBOOK.md): SQLcl walkthrough and
  database-agent runtime troubleshooting.
- [SQL_VALIDATION.md](SQL_VALIDATION.md): SQL checks for profiles, packages,
  teams, tools, and task history.
- [OAUTH_VALIDATION.md](OAUTH_VALIDATION.md): terminal OAuth flow and A2A
  discovery validation.

