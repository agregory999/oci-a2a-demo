# Oracle A2A Demo — UI Context

The workflow required to set up Agent-2-Agent (A2A) is somewhat complex.  Assuming we want to create a new Autonomous Database with the correct tags, enable Select AI with a profile, and then enable users for inferencing, there are several steps.

## Product Description

This is a local Flask demo console for preparing an Oracle Autonomous AI
Database for A2A, configuring Select AI, managing published agent teams, and
testing A2A access and Select AI chat.

The app runs locally. It uses local OCI CLI profiles for OCI operations and a
locally downloaded wallet for database ADMIN operations.

## Primary workflow

### Setup, once per environment

1. Provision or select an Autonomous AI Database, download a wallet, and test
   ADMIN.
2. Create/select the controlled target schema and sample data.
3. Use ADMIN to apply/verify the idempotent target-schema package grants.
4. Create and validate the provider credential and Select AI profile owned by
   the target schema.
5. Install and validate the sample tools, agent, and team.
6. Register an external OAuth client with ADMIN when required.

### A2A testing, repeat whenever needed

1. Obtain an external OAuth authorization-code token.
2. Discover/select a published team and load its Agent Card.
3. Send an A2A chat request and inspect task progress or diagnostics.

### Current validation boundary

The guided setup and external OAuth/discovery/card path have been exercised
against a paid ATP instance. A database-side chat can still remain `RUNNING`
after that setup succeeds. The UI must describe this accurately: token,
published-team discovery, and Agent Card retrieval prove the external A2A
boundary; they do not prove database Select AI Agent task completion. The chat
page retains task diagnostics, while `SQL_VALIDATION.md` supplies the
database-native runtime checks.

## Implemented information architecture

The console is divided into two deliberate halves. A user should be able to
enter the A2A testing half directly after an environment has been configured,
without navigating through provisioning, wallet, or agent-installation forms.

### Start page

| Page | Route | Purpose |
| --- | --- | --- |
| Start | `/` | Concise entry point with two large choices: **Setup** and **A2A Testing**. Show the last non-secret readiness summary only. |
| Setup overview | `/setup` | Explain the one-time configuration path and link to Infra & Admin, User-Level Setup, and Setup Readiness. |
| A2A Testing | `/test` | Explain the external-client test path and link to OAuth Token, Team Selection, and Chat. |

The Start page must not attempt discovery, credentials, database connections,
or token acquisition. It is navigation and status only.

### Shared shell and workflow outline

The shared shell uses a persistent left-hand workflow outline on desktop
(roughly the leftmost 10–15% of the window) and a compact responsive outline
above the content on smaller screens. It replaces repeated top-page tabs.

The outline order is intentional:

1. **A2A Testing**: testing overview, OAuth · Get Token, Team Selection, Chat.
2. **Setup**: setup overview, Infra & ADMIN, Target User, Package Readiness,
   OAuth · Register.

The active item is highlighted. Pages visited during the current local browser
session receive a green check/visited treatment. This is navigation history,
not proof that a configuration is valid.

Do not mix setup controls into the testing workflow or testing controls into
setup pages. The outline makes both paths visible without repeating navigation
cards in every page body.

Local package steps declare their execution identity. Environment grants and
cross-schema function DDL can run through ADMIN; Select AI Agent objects must
be created through a real connection as the target schema, because
`ALTER SESSION SET CURRENT_SCHEMA` does not change `SESSION_USER`. The console
asks for that target password only for the deployment request and never saves
it.

### Setup half

Setup contains two categories plus one final readiness page.

| Page | Route | Scope |
| --- | --- | --- |
| Setup · Infra & Admin | `/setup/infra` | Environment-level guide linking to OCI provisioning/context (`/setup/infra/provision`), lifecycle/wallet/ADMIN (`/connect`), and OAuth registration (`/oauth-clients`). |
| Setup · User-Level | `/setup/user` | Target schema/data, target-schema provider credential, target-schema Select AI profile, sample tools, agent, and team. These are owned by the target database user, such as `DEMO_SALES`. |
| Verify Demo | `/setup` (`#verify`) | Read-only preflight that checks saved OCI context, local wallet, current ADMIN session, target-schema enabled profile, and discovered target-schema agent objects. |
| Package Readiness | `/agents` (`#readiness`) | Package-specific data/profile validation before deployment or external testing. |

#### Bottom dock

The bottom dock is not a second navigation system. It is an environment and
action summary: known profile/region, wallet and ADMIN state, target schema and
profile, OAuth client/token metadata, database lifecycle, and the most recent
action result. It never exposes secrets.

`Verify Demo` does not obtain an OAuth token. It requires the current-memory
ADMIN connection to perform live database checks, so after an app restart it
truthfully asks the user to re-test ADMIN. A successful preflight means setup
prerequisites are present; external OAuth, A2A discovery, and chat remain
separate testing checks.

#### Setup ordering

1. **Infra & Admin**
   - Select OCI profile and region once.
   - Provision/select the database, inspect lifecycle, download/replace the
     wallet, and test ADMIN.
2. **User-Level**
   - Select/create target schema and controlled demo data.
   - Immediately run the visible, idempotent ADMIN grant step for the target
     schema: `EXECUTE` on `DBMS_CLOUD`, `DBMS_CLOUD_AI`, and
     `DBMS_CLOUD_AI_AGENT`; show the verified grants.
   - Use the tested wallet/service context while authenticating as the target
     schema to create/list/delete its provider credential and Select AI
     profile.
   - Install or refresh the sample agent tools and team.
3. **Readiness**
   - Run only validation queries and show object/profile status.
   - Do not make users obtain a token, discover teams, or send chat here.

### A2A Testing half

A2A Testing assumes setup already exists. It must contain only the controls an
external user needs to get a token, choose a published team, and chat.

| Page | Route | Scope |
| --- | --- | --- |
| OAuth · Get Token | `/test/token` | Optional local callback listener, authorization-code flow, token exchange, safe token metadata, and A2A discovery. No ADMIN password or OCI profile requirement. |
| Team Selection | `/test/team` | Select a discovered published team and load its Agent Card. |
| Chat | `/test/chat` | A2A JSON-RPC chat and task polling/diagnostics only. |

OAuth client registration remains a setup administration capability because it
requires ADMIN. It is linked from Setup · Infra & Admin and is not part of the
normal external testing flow.

#### A2A Testing dock

The testing dock shows exactly two items:

1. **OAuth token** — present/expired state plus expiry, scope, and source;
   never the token value.
2. **Team selection** — selected published team and Agent Card loaded state.

It intentionally omits OCI profile, wallet, ADMIN connection, target schema,
database lifecycle, and setup checkmarks. These are prerequisites, not an
external test user's workflow.

#### A2A Testing ordering

1. **OAuth Token**
   - Show a simple authorization-code path: prepare endpoints, optional
     same-machine callback listener, authorize, exchange, and discover.
   - Keep client secret, authorization code, refresh token, and access token
     in process memory only.
2. **Team Selection**
   - Use the active external token to show discovered teams and select one.
   - Load the Agent Card before enabling chat.
3. **Chat**
   - Send `message/send`, wait briefly for task completion, then retain manual
     `tasks/get` status checks for slower tasks.
   - Show the most recent non-secret task diagnostic and link to SQL history
     guidance for failures.
   - Clearly distinguish an A2A task that was accepted from a database agent
     task that completed. A `RUNNING` task is a runtime diagnostic, not a
     successful answer.

### Migration rules

- The start, Setup, and A2A Testing shells are implemented. They preserve the
  existing backend operations and persistence rules; this remains a UI
  reorganization, not a new database model.
- Existing operation pages remain available as focused working pages and are
  linked from the appropriate half. `/inferencing` redirects to `/test/chat`.
- The detailed page-level sections below remain the behavioral reference for
  the focused operation pages.


## Shared UI components

### Header navigation

Every page uses the same top-level navigation. The current page is visually
active. Navigation must always use real routes, not hidden client-side panels.

### Status dock

Every page includes a fixed bottom status dock scoped to its half of the
console.

- **Setup pages:** show the combined Infra, User-Level, and Setup-ready
  checklists defined above. The wallet detail shows only its base directory;
  the full per-database path is a mouseover tooltip.
- **A2A Testing pages:** show only OAuth token and Team selection, as defined
  above.
- Both docks show the latest relevant action result, never secrets, and link
  items to the appropriate real page and section.


### Sidebar Help

On each page, a right-side "card" exists which shows a help description for the entire page.  If anything is required before proceeding, it should be called out here.  Keep this looking like a column on the page where 75% of the left side of the page is reserved for real content, and 25% o nthe right for this help card.

## Page details

### Database · Connection

Sections, in order:

1. **Profile Selection**
   - OCI CLI profile and region to use, if overridden
   - Persist this when it changes
   - Profile will be used in other places
   - Show named profile in bottom dock once selected
   - This is the only editable OCI profile/region selector. Provisioning,
     deletion, database lifecycle checks, and wallet download display and use
     this saved context rather than offering conflicting selectors.

2. **Provision DB**
   - OCI CLI profile and region
   - Compartment, database/display name, and Vault secret reference
   - ATP Free, ATP Dev, and ATP Paid selection
   - ECPU and storage in GB for Paid only
   - A2A feature tag is added by the provisioning request

3. **Delete DB**
   - Requires exact OCID re-entry and `DELETE` confirmation
   - Is intentionally destructive and must remain clearly marked

### Setup · Infra

Sections, in order:

1. **Database Status**
   - Database OCID pre-populated from persistent settings
   - Update bottom dock with database name and lifecycle once checked
   - Display the profile and region selected on **Setup · Infra**; do not offer
     a second selector.

2. **Wallet download**
   - Display and use the selected profile and region; do not offer a second
     selector.
   - Require Lifecycle status check before wallet download
   - Wallet files are stored locally under the ignored `wallet/` directory
   - Confirm overwrite if wallet is downloaded again
   - Bottom dock indicates whether wallet is present to avoid re-download

3. **ADMIN connection**
   - Use the downloaded wallet and the selected profile/region context.
   - Lifecycle status check required before connection attempt
   - Wallet should exist before connection test is enabled
   - ADMIN connection test uses wallet service alias, database password, and
     wallet password
   - Bottom dock to show connection test status

### Setup · Select AI

Sections, in order. Should allow full lifecycle of creation, inspection, and
deletion of credentials and profiles.

1. **Create credential**
   - Supported authentication: OCI API-signing key, OCI Resource Principal,
     and OpenAI API key.
   - OCI API-key flow uses a selected local API-signing private-key file; the
     key is read for the request only and is not persisted.
   - OCI Resource Principal uses the database-managed
     `OCI$RESOURCE_PRINCIPAL` credential. The console enables principal
     authentication for ADMIN or the selected target schema and creates a
     profile that references that fixed credential. It does not create, store,
     inspect, or delete a second credential object.
   - The user configures the Resource Principal dynamic group and OCI IAM
     policies outside the console. The UI requires only the OCI Generative AI
     compartment, with optional region and model.
   - Credential names and non-secret OCI settings are persisted locally
   - The selected credential name is shown in the bottom dock. For Resource
     Principal, it is always `OCI$RESOURCE_PRINCIPAL`.

3. **Create Select AI Profile**
   - Supported providers: OCI Generative AI and OpenAI only
   - Credential name required
   - Create/Update button will either create or update named profile
   - Profile name and non-secret OCI settings are remembered
   - Profile name shown on bottom dock

3. **Manage profiles**
   - Refresh list of profiles in the connected ADMIN schema
   - Requires ADMIN connection to exist
   - Inspect a selected profile; show all attributes

4. **Manage credentials**
   - Refresh list of credentials in the connected ADMIN schema
   - Requires ADMIN connection to exist
   - Inspect a selected credential; show all attributes except private key

4. **Delete profile**
   - Requires `DELETE` confirmation
   - Deletes the Select AI profile via SQL, not its DBMS_CLOUD credential

5. **Delete credential**
   - Requires `DELETE` confirmation
   - Current behavior deletes the DBMS_CLOUD credential

### Data · Sample Schema

This page comes before Agents. It creates a controlled, portable demo dataset
for the Oracle AI Database Agent and selects the schema in which the sample
tools and team will operate.

Sections, in order:

1. **Target schema selection**
   - Select an existing target schema or create a dedicated controlled demo
     schema.
   - Persist the non-secret target schema name only.
   - Clearly distinguish the connected ADMIN user from the target schema.
   - The status dock shows the selected target schema once it is created or
     verified.

2. **Controlled sample data**
   - Offer a portable `DEMO_SALES` dataset suitable for NL2SQL, filtering,
     date-range questions, aggregation, and charts.
   - Creation requires the exact confirmation `CREATE SAMPLE DATA`.
   - Show every created table and its row count.
   - Never overwrite existing tables. A reset/cleanup action is separate,
     destructive, and requires `DELETE SAMPLE DATA` confirmation.

3. **Select AI data access**
   - Create the provider credential and selected Select AI profile in the
     target schema itself. The tested ADMIN connection provides the wallet and
     service context, but an ADMIN-owned API-key credential/profile cannot be
     used by agent tools executing as the target schema. Resource Principal is
     database-managed, but it must be enabled for the target schema and the
     profile itself is still target-schema owned.
   - Before the target-schema connection, use ADMIN to grant `EXECUTE` on
     `DBMS_CLOUD`, `DBMS_CLOUD_AI`, and `DBMS_CLOUD_AI_AGENT` to that schema.
   - Collect the target-schema password and provider secret/key only for this
     request; never persist either value. Resource Principal needs no provider
     secret, user OCID, tenancy OCID, fingerprint, or private key.
   - Run a stateless `DBMS_CLOUD_AI.GENERATE` test as the target schema after
     creation. For non-OCI providers, explain that the target schema also
     needs the required network ACL.
   - List the target schema's profile names/statuses and user-owned credential
     names without exposing secrets. Explain that the system-managed
     `OCI$RESOURCE_PRINCIPAL` might not appear in that list. Permit profile deletion only with `DELETE
     PROFILE` confirmation and credential deletion only with `DELETE
     CREDENTIAL`; advise deleting the profile first.
   - Present a safe metadata/readiness result in the status dock.
   - Explain when the profile and target schema must be aligned, or when an
     explicitly supported cross-schema configuration is required.

### Agents / Teams

This page deploys a selected local sample package, while retaining A2A
discovery and team deletion. Each package owns a directory under `samples/`
with a manifest, ordered scripts, prerequisites, and one published team.
A target schema can host multiple packages when their object names do not
overlap.

Sections, in order:

1. **Choose package and check readiness**
   - Offer `sales-data` (Oracle's pinned upstream data-query sample) and
     `database-provisioning` (the local resource-principal sample).
   - Select the target schema and an editable Select AI profile. Remembered
     profiles are suggested, and an existing profile may be entered directly.
   - Data validation is package-specific: Sales Data requires controlled sales
     tables; Database Provisioning has no data dependency.
   - Also inspect whether the expected package, configuration table, tools,
     task, agent, and team already exist.
   - Link the status dock's target-schema item to this readiness section.

2. **Deploy or refresh package**
   - Require a ready target schema, enabled target-schema profile, and the
     exact confirmation `INSTALL SAMPLE`.
   - Sales Data downloads the two named Oracle scripts only when their
     SHA-256 values match the package manifest.
   - Database Provisioning runs local parameterized scripts and requests its
     tenancy, compartment, home-region, and Vault-secret OCIDs only for the
     current deployment. It must be installed in a least-privileged schema.
   - A package replaces only its own task, agent, tools, and team.

3. **Refresh teams**
   - Requires ADMIN connection and current OAuth token.
   - Repeats A2A discovery, persists non-secret team metadata, and provides a
     button to load each Agent Card in **A2A Testing → Team Selection**.

4. **Delete team**
   - Requires exact team name and `DELETE` confirmation.
   - The default action deletes only the selected team.
   - Any cleanup of the sample task, agent, tools, package, configuration
     table, or sample data is a separate destructive action with its own
     confirmation.

### OAuth · Register

This dedicated page registers OAuth clients for external A2A clients, such as
Google Gemini Enterprise. It is separate from the external authorization-code
token flow on **OAuth · Get Token**.

The page requires a successful ADMIN wallet connection. Oracle permits only
the database `ADMIN` user to register these client credentials.

Sections, in order:

1. **Registration prerequisites**
   - Display the selected OCI profile, region, database OCID, and connected
     ADMIN user.
   - Validate that the database OCID and region are available before enabling
     registration.
   - Explain that the client is registered through the A2A registration
     endpoint, not with a database SQL statement.

2. **Register OAuth client**
   - Collect a client name and one or more redirect URIs.
   - For Google Enterprise, instruct the user to copy the exact callback URI
     supplied by the Google Enterprise connector configuration UI. Do not
     invent or hard-code a Google callback URI.
   - For a local authorization-code test, offer the console's currently active
     loopback callback URI, such as `http://127.0.0.1:5000/oauth/callback`.
     Clearly state that this is for a browser on the same machine only and
     must be accepted by the Oracle registration service.
   - Validate the client name, each external HTTPS redirect URI, the loopback
     exception, and duplicate URIs locally before sending the registration
     request.
   - Use the documented registration endpoint:
     `POST /adb/auth/v1/connect/databases/{database-ocid}/register`.
   - Authenticate as the connected ADMIN user and send `client_name` plus
     `redirect_uris` only.
   - Persist non-secret metadata only: client name, client ID, redirect URIs,
     timestamps, grant types, and expiry metadata.
   - Never persist, log, render in the status dock, or return the client
     secret after the one-time result view.
   - Present a prominent one-time warning: copy the returned client ID and
     client secret now. The secret is shown only in this result view; the user
     must configure it in the external A2A client's documented OAuth flow.
   - The status dock indicates that an OAuth client exists, using its client
     name or client ID but never the secret.

3. **List and inspect OAuth clients**
   - Show locally persisted non-secret registration metadata and any live list
     endpoint that Oracle documents for this A2A client-registration API.
   - Before implementation, verify the supported live list endpoint and its
     authorization model; do not invent an undocumented endpoint.
   - Clearly distinguish locally remembered registrations from a live database
     inventory.

4. **Registration inventory and retirement**
   - Show only locally remembered non-secret registration metadata unless
     Oracle documents a supported live list endpoint.
   - Do not present a local delete button as revocation. Client retirement must
     use Oracle's supported administration surface; the app has no live A2A
     client deletion endpoint at this time.

### OAuth · Get Token

This page models the external authorization-code experience used by an A2A
client such as Google Gemini Enterprise. It does not require a local OCI
profile or an ADMIN password. Client credentials, authorization codes, refresh
tokens, and access tokens stay in process memory only.

1. **Local callback listener**
   - Provide a button that enables a one-time local callback listener and
     displays its exact loopback callback URI for registration.
   - This is only for a browser on the same machine. Google Enterprise must
     use the exact callback URI supplied by its connector configuration UI.
   - The listener accepts only expected OAuth callback parameters, keeps the
     authorization code in process memory only, never renders the raw code,
     and clearly shows received or expired state.

2. **Authorize and exchange**
   - Use the saved Setup region to prepare endpoints and the saved Autonomous
     Database OCID for A2A team discovery after token acquisition. Do not ask
     the external user to re-enter either value.
   - Display the known Google-compatible regional endpoints:
     `/adb/auth/v1/connect/authorize` and `/adb/auth/v1/connect/token`.
     These URLs do not contain the database OCID.
   - Collect client ID to start authorization, then client ID and client
     secret to exchange the locally captured code for a token.
   - Treat token exchange and published-team discovery as separate results. If
     the token exchange succeeds but discovery fails, say so explicitly; do
     not label the discovery error as an OAuth exchange failure.
   - Show no secret/token values in the UI and update the dock with token
     expiry, scope, and source where returned. When explicit local debug mode
     is enabled, allow a deliberate action to print the token to the local
     server console only, with a prominent secret-handling warning.

### A2A Testing — Team Selection and Chat

This page is limited to team selection and chat. It requires an in-memory
external OAuth token obtained on **OAuth · Get Token**.

1. **Team or Agent Selection**
   - User selects Agent Team from the list of known teams
   - List can come from persisted settings
   - Refresh is available on **Agents** and uses the active external token; it
     does not require an ADMIN password connection.
   - Currently selected Team is shown in bottom dock.

2. **Chat**
   - Sends JSON-RPC `message/send` with the active external OAuth token.
   - The current sample Agent Card does not advertise streaming. When Oracle
     returns a task, automatically poll JSON-RPC `tasks/get` for up to 15
     seconds so ordinary chat feels synchronous. Show its ID and retain a
     **Check task status** action for slower work.
   - Render returned text parts in the conversation and retain a small
     in-memory history only. Preserve the opaque A2A `contextId`; when a task
     is `input-required`, preserve both its context and task ID so a reply
     resumes that task. **Start new conversation** intentionally discards
     these values.
   - Retain the most recent non-secret task response in a collapsible
     diagnostic panel. This makes returned Oracle failure details visible
     without exposing credentials or tokens.

## Persistence and security rules

- `last-settings.json` stores non-secret form settings only and is ignored by
  Git.
- Wallets are stored under `wallet/` and ignored by Git.
- Passwords, OAuth tokens, OpenAI keys, and OCI private keys must never be
  persisted in `last-settings.json`, templates, logs, or status messages. The
  sole exception is a user-invoked OAuth token print to the local server
  console while explicit debug mode is enabled; it must never be rendered in
  the browser.
- OCI private-key selection must use a file input; do not ask users to paste
  the private key into a normal field.
- Select AI and sample-agent-installer debug output is opt-in via `--debug` or
  `A2A_DEMO_DEBUG=1` and must redact secrets. Installer debug may show each
  executable sample-script SQL block in the page and local command line; it
  must not include passwords, OAuth tokens, or client secrets.
- `SQL_VALIDATION.md` is the user-facing companion for SQL inspection, test,
  and validation queries. `OAUTH_VALIDATION.md` is the separate command-line
  authorization-code validation guide. Neither file may include credentials,
  private keys, or OAuth tokens.

## Active template structure

```text
templates/
├── base.html                 # Shared layout, CSS, and header navigation
├── includes/
│   └── status_dock.html      # Shared persistent status/results dock
├── infra.html
├── connect.html
├── select_ai.html
├── sample_data.html
├── agents.html
├── oauth_clients.html
├── oauth_token.html
└── inferencing.html
```

## How to request UI changes

Add or modify the relevant section above. For a new page, include:

- Page name and route
- Its role in the workflow
- Ordered sections and the action each button performs
- Inputs to collect and which values should persist
- Expected success/error result in the status dock
- Any destructive action, confirmation text, or security consideration

## Change requests

<!-- Add your next UI/product changes below this line. -->
