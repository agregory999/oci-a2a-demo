# External OAuth Validation Companion

Use this guide from a terminal to validate the same external authorization-code
boundary used by **A2A Testing → OAuth · Get Token**. It is intentionally
separate from `SQL_VALIDATION.md`: A2A OAuth client registration and token
exchange are regional REST calls, not SQL statements.

Do not paste a client secret, authorization code, refresh token, or access token
into this repository, a shell history file, or a support ticket. Use a private
terminal and discard the shell session when finished.

## 1. Prerequisites

Register a client with **Setup → Infra & Admin → OAuth · Register** while
connected as `ADMIN`.

- The console's built-in local callback uses the exact URI displayed on
  **OAuth · Get Token**. With the default local Flask server this is normally
  `http://127.0.0.1:5000/oauth/callback`; register the displayed value, not a
  guessed port.
- This command-line guide uses its own standalone listener and therefore
  registers exactly `http://127.0.0.1:8765/callback` as a separate redirect
  URI.
- For Google Enterprise, register the exact callback URI shown by its
  connector configuration instead; do not substitute the loopback URI.
- Keep the one-time client secret in a secure secret store.

The authorization and token endpoints are regional and do not include the
database OCID. The OCID is only required for the A2A agent-discovery call.

The expected external test sequence is: authorize and exchange a token;
discover the team installed for the token subject; load its Agent Card in the
console; then send a chat message. Examples are `ORACLE_AI_DATABASE_AGENT`
for Sales Data Query and `DATABASE_PROVISIONING_TEAM` for Database
Provisioning. Successful OAuth and discovery prove external access but do not
prove that the database-side agent runtime will complete a task. Use
`SQL_VALIDATION.md` if a task remains `RUNNING`.

```bash
export A2A_REGION='us-ashburn-1'
export A2A_DATABASE_OCID='ocid1.autonomousdatabase.oc1...'
export A2A_BASE="https://dataaccess.adb.${A2A_REGION}.oraclecloudapps.com"
export A2A_AUTHORIZE_URL="${A2A_BASE}/adb/auth/v1/connect/authorize"
export A2A_TOKEN_URL="${A2A_BASE}/adb/auth/v1/connect/token"
export A2A_AGENTS_URL="${A2A_BASE}/adb/a2a/v1/databases/${A2A_DATABASE_OCID}/agents/"
export A2A_CALLBACK='http://127.0.0.1:8765/callback'

read -r 'A2A_CLIENT_ID?Client ID: '
read -rs 'A2A_CLIENT_SECRET?Client secret: '
printf '\n'
export A2A_CLIENT_ID A2A_CLIENT_SECRET
```

## 2. Receive the authorization callback locally

Run this small listener in one terminal. It prints the callback URL, then exits
after Oracle redirects the browser. It listens only on loopback and exposes no
token endpoint.

```bash
python3 - <<'PY'
from http.server import BaseHTTPRequestHandler, HTTPServer

class Callback(BaseHTTPRequestHandler):
    def do_GET(self):
        print(self.path, flush=True)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'Authorization received. You can close this tab.')
        self.server.should_stop = True
    def log_message(self, *_args):
        pass

server = HTTPServer(('127.0.0.1', 8765), Callback)
while not getattr(server, 'should_stop', False):
    server.handle_request()
PY
```

In a second terminal, generate a state value and print the URL to open in a
browser. Sign in as the external database user (for example, `DEMO_SALES`).
That login authenticates the resource owner; it is not the client secret.

```bash
export A2A_STATE="$(openssl rand -hex 24)"
python3 - <<'PY'
import os
from urllib.parse import urlencode
print(os.environ['A2A_AUTHORIZE_URL'] + '?' + urlencode({
    'response_type': 'code',
    'client_id': os.environ['A2A_CLIENT_ID'],
    'redirect_uri': os.environ['A2A_CALLBACK'],
    'scope': 'openid',
    'state': os.environ['A2A_STATE'],
}))
PY
```

Open the printed URL. After the listener prints a path such as
`/callback?code=...&state=...`, copy only the `code` value into the current
terminal. Confirm that the returned `state` exactly matches `A2A_STATE` before
continuing.

```bash
read -r 'A2A_AUTHORIZATION_CODE?Authorization code: '
export A2A_AUTHORIZATION_CODE
```

Authorization codes are short-lived and single-use. If the exchange fails,
restart with a newly generated authorization URL and code.

## 3. Exchange the code for an external token

Write the response to a secure temporary file rather than displaying the access
token in the terminal. This command uses the registered callback URI exactly.

```bash
export A2A_TOKEN_RESPONSE="$(mktemp /tmp/a2a-token-response.XXXXXX)"
curl --fail --silent --show-error --request POST "$A2A_TOKEN_URL" \
  --header 'Accept: application/json' \
  --data-urlencode 'grant_type=authorization_code' \
  --data-urlencode "code=${A2A_AUTHORIZATION_CODE}" \
  --data-urlencode "client_id=${A2A_CLIENT_ID}" \
  --data-urlencode "client_secret=${A2A_CLIENT_SECRET}" \
  --data-urlencode "redirect_uri=${A2A_CALLBACK}" \
  --output "$A2A_TOKEN_RESPONSE"

jq '{token_type, expires_in, scope, has_access_token: (.access_token != null)}' \
  "$A2A_TOKEN_RESPONSE"
export A2A_ACCESS_TOKEN="$(jq -r '.access_token // empty' "$A2A_TOKEN_RESPONSE")"
test -n "$A2A_ACCESS_TOKEN"
```

The expected summary reports `has_access_token: true`; the sample flow normally
includes the `a2a openid` scope. Do not run `jq .` on the response because it
prints the token.

## 4. Validate the token against A2A discovery

This call proves that the external token is accepted for the database whose
OCID is in `A2A_AGENTS_URL`.

```bash
curl --fail --silent --show-error \
  --header "Authorization: Bearer ${A2A_ACCESS_TOKEN}" \
  --header 'Accept: application/json' \
  "$A2A_AGENTS_URL" \
  | jq '{published_team_count: length, teams: [.[] | {name, url, protocolVersion}]}'
```

Expect the team owned by the database user who authorized the token in `teams`.
The console's discovery uses its saved Setup region and database OCID after a
browser/app restart; it does not require an OCI profile or ADMIN password.
Its diagnostic performs the same database-OCID match and shows only non-secret
token claims.

## 5. Continue in the console

Return to **A2A Testing → Team Selection** and load the discovered Agent Card.
Then open **Chat** and send a small prompt. The console submits A2A
`message/send`, polls the returned task briefly, and retains a manual status
check plus the latest task diagnostic for slow or failed work. When a task is
`input-required`, reply in that same chat. The console preserves the opaque
context and task IDs; **Start new conversation** intentionally discards them.

If discovery succeeds but the task remains `RUNNING`, run the database-native
agent and history checks in `SQL_VALIDATION.md`. Do not re-register the OAuth
client or reissue a token unless discovery/card retrieval is failing too.

## 6. Clean up the terminal session

```bash
unset A2A_CLIENT_ID A2A_CLIENT_SECRET A2A_AUTHORIZATION_CODE A2A_ACCESS_TOKEN
test -n "${A2A_TOKEN_RESPONSE:-}" && rm -f "$A2A_TOKEN_RESPONSE"
unset A2A_TOKEN_RESPONSE A2A_STATE
```

Removing the temporary response prevents a token from remaining on disk. The
OAuth client remains registered until it is removed through Oracle's supported
administration surface.
