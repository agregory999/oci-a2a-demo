# Deployment Guide

This guide takes a new Autonomous AI Database from tenancy preparation through
external A2A chat testing and cleanup. It complements the short [README](README.md).
Use [SQL_VALIDATION.md](SQL_VALIDATION.md) for database-side checks,
[OAUTH_VALIDATION.md](OAUTH_VALIDATION.md) for a command-line OAuth flow, and
[AGENT_TEAM_RUNBOOK.md](AGENT_TEAM_RUNBOOK.md) when an agent task does not finish.

## Tenancy Preparation

Before embarking on a deployment, the tenancy must be configured as follows.  Specific steps are not in scope for this project, but are readily available in docs:
- Tenancy with User that can create Autonomous AI Database instance
- IAM permissions to read compartment hierarchy & perform GenAI tasks
- Sufficient quota to create a small Autonomous Database instance
- If using Resource Principals, a set of statements allowing management or reading of any object this demo will create.

### Decide the scope

Choose the following before opening the app:

- OCI tenancy and home region.
- An OCI compartment for the Autonomous AI Database and, if different, a
  compartment containing the Vault secret used for a newly provisioned
  database's ADMIN password.
- A database type/shape that supports the Select AI Agent features you intend
  to test. Confirm feature availability for the chosen region and service
  before provisioning.
- One target database schema per sample when you want isolated tests. For
  example, use `DEMO_SALES` for Sales Data Query and `ADBPROV` for Database
  Provisioning.

The OCI user behind the local CLI profile needs permissions to manage
Autonomous Databases in the target compartment and to read the Vault secret
specified during provisioning. Apply least privilege and your organization's
normal approval process.

### Create or select the database

You can use an existing compatible Autonomous AI Database or let the app
provision one. For a new database, create the Vault secret containing the
ADMIN password before provisioning, then record only its secret OCID. Do not
put the actual password in a source file, shell history, or `last-settings.json`.

After a database becomes available, obtain its database OCID. The app uses it
for wallet actions, OAuth client registration, A2A discovery, and Agent Card
requests.

### Optional Resource Principal setup

This setup is optional for the app in general, but it is **required** when you
choose either of these options:

1. **Target Select AI → OCI Resource Principal** for an OCI Generative AI
   profile without an API-signing key.
2. **Database Provisioning** sample, whose tools call OCI Database APIs and
   read the Vault secret OCID supplied to the package.

Do these tenancy/database administration steps before deploying either option:

1. Create an IAM dynamic group whose rule selects the Autonomous AI Database
   resource principal. Use the matching rule format for your tenancy and
   explicitly target the database, compartment, or tenancy as appropriate.
2. Add policies for that dynamic group in the policy compartment. Grant only
   the resources the selected feature needs:
   - For a Resource Principal Select AI profile: permission to use OCI
     Generative AI in the model's compartment.
   - For Database Provisioning: permission to manage the required Autonomous
     Database family in the allowed target compartment; read access to the
     Vault secret (and any required Vault/key metadata) containing new
     database ADMIN passwords.
   - Add a narrowly scoped policy for any additional OCI service a custom tool
     calls. Do not use tenancy-wide `manage all-resources` just to make a demo
     work.
3. Connect to the database as `ADMIN` and enable the database resource
   principal once:

   ```sql
   EXEC DBMS_CLOUD_ADMIN.ENABLE_RESOURCE_PRINCIPAL();
   ```

4. Grant a target schema access to the database-managed credential. Repeat
   this for each schema that will use it:

   ```sql
   EXEC DBMS_CLOUD_ADMIN.ENABLE_RESOURCE_PRINCIPAL(username => 'ADBPROV');
   ```

5. Verify it before using the app:

   ```sql
   SELECT owner, credential_name
   FROM dba_credentials
   WHERE credential_name = 'OCI$RESOURCE_PRINCIPAL';

   SELECT grantee, table_name, grantor
   FROM all_tab_privs
   WHERE grantee = 'ADBPROV'
     AND table_name = 'OCI$RESOURCE_PRINCIPAL';
   ```

`OCI$RESOURCE_PRINCIPAL` is created and secured by Autonomous AI Database; it
is not a user-created DBMS_CLOUD credential. The app creates a profile that
uses it, but it does not create IAM dynamic groups or policies. Policy changes
can take time to become effective. Oracle's current resource-principal guide
is the authority for matching-rule and policy syntax: [Use Resource Principal
to Access OCI Resources](https://docs.oracle.com/en-us/iaas/autonomous-database-serverless/doc/resource-principal.html).

## Local Environment

### Prerequisites

- Python 3 and `venv`.
- OCI CLI installed and authenticated locally.
- At least one usable OCI CLI profile in `~/.oci/config`; the app reads its
  profile names and default regions.
- Network access from the workstation to OCI APIs, the database wallet service,
  and the public A2A/OAuth endpoints.
- A wallet password and database ADMIN password available when needed.

For database-side validation or manual sample troubleshooting, install SQLcl
and use the downloaded wallet. The application itself uses the Oracle Python
database driver declared in `requirements.txt`; it does not require SQLcl.

### Install and configure

```bash
git clone https://github.com/agregory999/oci-a2a-demo.git
cd oci-a2a-demo
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Set a distinct, long random Flask signing key for every environment:

```bash
export FLASK_SECRET_KEY='replace-with-a-long-random-value'
```

Do not commit `.venv/`, `wallet/`, `last-settings.json`, private keys, OAuth
codes/tokens, password files, or terminal captures containing them. The
project already ignores the normal local state files.

## Running the Application

Start the server from the repository root:

```bash
FLASK_SECRET_KEY="$FLASK_SECRET_KEY" .venv/bin/python app.py
```

For local diagnostics that redact secrets but show installer SQL and related
details, use:

```bash
FLASK_SECRET_KEY="$FLASK_SECRET_KEY" .venv/bin/python app.py --debug
```

Open <http://127.0.0.1:5000>. This is a local, single-user workflow console;
do not expose it directly to an untrusted network. Restarting the app clears
the in-memory OAuth token and remembered passwords. Non-secret form choices
may remain in ignored `last-settings.json`.

## Using the Application

The left outline is intentional: complete each focused step in order. The
right-side help explains the current step, and the bottom dock summarizes the
known state and recent action results.

### 1. Environment setup

1. **Setup → OCI context**: choose one local OCI CLI profile and region. All
   downstream OCI actions display and reuse this context.
2. **Provision database**: use this only for a new database. Enter the
   compartment, database names, and Vault secret OCID; type `CREATE` to submit
   the real OCI request. Otherwise skip to Database status for an existing DB.
3. **Database status**: enter/select the database OCID and confirm it is
   `AVAILABLE`.
4. **Wallet**: download the matching wallet. Replacing it is explicit and only
   occurs after the new wallet download succeeds.
5. **ADMIN connection**: enter the wallet service alias, ADMIN password, and
   wallet password; run the test. This gates environment-level database work.
6. **OAuth · Register**: register an external client after ADMIN succeeds.
   Enter the external product's real callback URI, or the loopback URI from
   Local listener for a local authorization-code test. Copy the returned client
   secret immediately; the console does not persist it.

### 2. Target-user setup

1. **Target schema**: select an existing schema or create a dedicated one.
   The schema—not ADMIN—owns its profile and agent objects.
2. **Target privileges**: run the idempotent grant step. It grants the target
   user execution rights on `DBMS_CLOUD`, `DBMS_CLOUD_AI`, `DBMS_CLOUD_AI_AGENT`,
   and `DBMS_CLOUD_PIPELINE`.
3. **Demo data**: load controlled data for the Sales Data Query package. Skip
   it for Database Provisioning.
4. **Target Select AI**: create and live-test the profile owned by the target
   schema. Choose one provider:
   - **OCI API-signing key**: provide OCI identity inputs and a local private
     key file.
   - **OCI Resource Principal**: requires the optional tenancy preparation
     above; no API-signing credential is created.
   - **OpenAI**: provide a request-only API key.
5. **Package readiness**: select the package, schema, and target-owned profile.
   Resolve failed profile/data checks before deployment.
6. **Deploy package**: type `INSTALL SAMPLE`. Supply the extra tenancy,
   compartment, secret-OCID, and schema-password inputs for Database
   Provisioning. Deployment is designed to be repeatable for the same package
   and schema.
7. **Published teams**: refresh using an external OAuth token once one is
   available. Load the desired team into A2A Testing.

### 3. External A2A testing

1. **Local listener**: start it only for a local authorization-code test. Copy
   its displayed loopback URI into the OAuth client registration, if accepted.
2. **OAuth · Get Token**: prepare the region-derived endpoints, enter the
   client ID and one-time client secret, authorize as the target database user,
   and exchange the returned code. The database username/password belong to
   Oracle's authorization page; the exchange uses the client credentials and
   captured authorization code.
3. **Team selection**: refresh/load a published Agent Card with the external
   OAuth token. A successful card proves discovery and authentication, not an
   agent task's runtime behavior.
4. **Chat**: submit a simple question first. The UI polls ordinary tasks for a
   short time; use **Check task status** for slower work. For a task awaiting
   human input, answer in the same conversation—do not start a new
   conversation, which intentionally discards local A2A context.

If discovery succeeds but chat stays `RUNNING` or fails, capture the task ID
and follow [AGENT_TEAM_RUNBOOK.md](AGENT_TEAM_RUNBOOK.md) and the task-history
queries in [SQL_VALIDATION.md](SQL_VALIDATION.md). First validate a direct
`DBMS_CLOUD_AI.GENERATE` call as the target schema; it separates a provider or
profile issue from the agent runtime.

## Cleanup

Clean up in reverse dependency order. Leave the destructive left-nav steps
until the testing evidence you need has been captured.

1. Finish or record active A2A task IDs and clear local browser/app state when
   no longer needed.
2. **Delete team** from the console, or use the package's documented SQLcl
   cleanup/reset process. Team deletion does not automatically drop its tools,
   functions, credentials, profile, or data.
3. **Target cleanup**: remove the target Select AI profile first, then its
   user-owned credential if it is no longer shared. Do not attempt to drop
   `OCI$RESOURCE_PRINCIPAL`; remove schema access with
   `DBMS_CLOUD_ADMIN.DISABLE_RESOURCE_PRINCIPAL(username => 'SCHEMA')` only if
   that schema should no longer use it.
4. Drop custom sample functions, data, and target schemas through approved
   database administration practices if the environment is disposable.
5. **Delete database** last, using the exact OCID confirmation. This is
   irreversible and should follow your tenancy change-control process.
6. If no database in the instance needs Resource Principal, an administrator
   may disable it globally with `DBMS_CLOUD_ADMIN.DISABLE_RESOURCE_PRINCIPAL()`.
   Review all dependent schemas first; this removes the database-managed
   credential.
7. Remove the local `wallet/` directory and any copied client secret or token
   material using your organization's approved secure-disposal process.

The console intentionally never treats local remembered registrations or a
successful external token as an OCI inventory or audit record. Use OCI Audit,
database task history, and your organization’s operational logs for that
purpose.
