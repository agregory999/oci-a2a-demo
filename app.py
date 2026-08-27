"""Local Flask console for validating Oracle Autonomous AI Database A2A agents."""

from __future__ import annotations

import json
import hashlib
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
from base64 import urlsafe_b64decode
from configparser import ConfigParser
from dataclasses import dataclass
from pathlib import Path
import re
from urllib.parse import urlencode, urlparse

import requests
from flask import Flask, flash, redirect, render_template, render_template_string, request, session, url_for


app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(32)),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)
SELECT_AI_DEBUG = "--debug" in sys.argv or os.environ.get("A2A_DEMO_DEBUG") == "1"
MASKED_SECRET = "••••••••"

# Tokens live only for the life of this process. The browser stores an opaque ID,
# never a token or password.
volatile_sessions: dict[str, dict[str, object]] = {}
DB_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9]{0,29}$")
LAST_SETTINGS_FILE = Path(__file__).with_name("last-settings.json")
WALLET_ROOT = Path(__file__).with_name("wallet")
SQL_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]{0,124}$")
PROVISIONING_SETTING_KEYS = (
    "provision_profile", "provision_region", "compartment_id", "db_name", "display_name",
    "secret_id", "secret_version_number", "tier", "ecpu_count", "storage_gbs",
)
SELECT_AI_SETTING_KEYS = (
    "provider", "credential_name", "profile_name", "oci_compartment_id",
    "oci_user_ocid", "oci_tenancy_ocid", "oci_fingerprint", "oci_model", "oci_region",
)
ADMIN_SETTING_KEYS = ("service_name", "admin_username")
PERSISTED_SETTING_KEYS = PROVISIONING_SETTING_KEYS + SELECT_AI_SETTING_KEYS + ADMIN_SETTING_KEYS + ("database_ocid", "known_teams", "target_schema", "oauth_clients")
SAMPLE_SCHEMA_SETTING_KEYS = ("target_schema",)
ORACLE_SAMPLE_BASE_URL = "https://raw.githubusercontent.com/oracle-devrel/oracle-autonomous-database-samples/main/google-gemini-marketplace-agents/oracle_ai_database_agent"
SAMPLES_ROOT = Path(__file__).with_name("samples")
SAMPLE_TABLES = ("DEMO_CUSTOMERS", "DEMO_PRODUCTS", "DEMO_ORDERS")
A2A_FEATURE_TAG = json.dumps({"adb$feature": json.dumps({"name": "a2a_server", "enable": True}, separators=(",", ":"))})


@dataclass(frozen=True)
class Connection:
    region: str
    database_ocid: str
    username: str
    password: str

    @property
    def base_url(self) -> str:
        return f"https://dataaccess.adb.{self.region}.oraclecloudapps.com"

    @property
    def token_url(self) -> str:
        return f"{self.base_url}/adb/auth/v1/databases/{self.database_ocid}/token"

    @property
    def agents_url(self) -> str:
        return f"{self.base_url}/adb/a2a/v1/databases/{self.database_ocid}/agents/"


def volatile_state() -> dict[str, object]:
    session_id = session.get("volatile_session_id")
    if not session_id:
        session_id = secrets.token_urlsafe(32)
        session["volatile_session_id"] = session_id
    return volatile_sessions.setdefault(session_id, {})


def request_secret(state: dict[str, object], form_name: str, state_name: str, label: str) -> str:
    """Reuse a masked process-memory secret without writing it to settings or HTML."""
    submitted = request.form.get(form_name, "")
    if submitted and submitted != MASKED_SECRET:
        state[state_name] = submitted
        return submitted
    cached = str(state.get(state_name) or "")
    if cached:
        return cached
    raise ValueError(f"Enter {label}.")


def set_section_feedback(state: dict[str, object], section: str, message: str, category: str) -> None:
    """Keep the latest result beside the workflow that produced it."""
    state.setdefault("section_feedback", {})[section] = {"message": message, "category": category}


def setup_status_items(
    state: dict[str, object],
    connection_settings: dict[str, object],
    provision_settings: dict[str, object],
    select_ai_settings: dict[str, object],
    admin_settings: dict[str, object],
    wallet_dir: str | None,
) -> list[dict[str, str | bool]]:
    """Summarize setup progress without exposing secrets in the persistent UI."""
    database = state.get("provisioned_database")
    database_name = ""
    if isinstance(database, dict):
        database_name = str(database.get("display-name") or database.get("db-name") or database.get("id") or "")
    database_name = database_name or str(provision_settings.get("display_name") or provision_settings.get("db_name") or "")
    lifecycle = str(state.get("wallet_status") or "")
    profile_name = str(select_ai_settings.get("profile_name") or "")
    listed_profiles = {str(value).upper() for value in state.get("select_ai_profiles", [])}
    profile_verified = bool(state.get("select_ai_checklist", {}).get("AI profile")) or bool(profile_name and profile_name.upper() in listed_profiles)
    target_schema = str(state.get("sample_settings", {}).get("target_schema") or provision_settings.get("target_schema") or "")
    credential_name = str(select_ai_settings.get("credential_name") or "")
    profile_detail = " · ".join(part for part in (profile_name, credential_name) if part) or "No profile selected"
    token_user = str(connection_settings.get("username") or "")
    selected_team = str(state.get("selected_team") or "")
    token_details = state.get("oauth_token_details", {})
    token_detail = selected_team or token_user or "No database user selected"
    if state.get("access_token") and isinstance(token_details, dict):
        expires_at = token_details.get("expires_at")
        expires_in = max(0, int(float(expires_at) - time.time())) if expires_at is not None else token_details.get("expires_in")
        scope = token_details.get("scope")
        parts = [f"Expires in {expires_in}s" if expires_in is not None else "Token acquired", str(scope) if scope else ""]
        token_detail = " · ".join(part for part in parts if part)
        source = str(state.get("oauth_token_source") or "")
        if source:
            token_detail = f"{token_detail} · {source}"
    oauth_clients = state.get("oauth_clients", [])
    oauth_detail = str(oauth_clients[-1].get("client_name") or oauth_clients[-1].get("client_id") or "") if isinstance(oauth_clients, list) and oauth_clients else "No client registered"
    wallet_detail = str(Path(wallet_dir).parent) if wallet_dir else "No local wallet"
    return [
        {"tab": "profile", "label": "OCI profile", "detail": str(connection_settings.get("profile") or provision_settings.get("provision_profile") or "Not selected"), "done": bool(connection_settings.get("profile") or provision_settings.get("provision_profile"))},
        {"tab": "wallet", "label": "Wallet downloaded", "detail": wallet_detail, "title": wallet_dir or "", "done": bool(wallet_dir)},
        {"tab": "wallet", "label": "ADMIN connection", "detail": str(admin_settings.get("service_name") or "Not tested"), "done": bool(state.get("admin_connection_ok"))},
        {"tab": "select-ai", "label": "Select AI profile", "detail": profile_detail, "done": profile_verified},
        {"tab": "sample-data", "label": "Target schema", "detail": target_schema or "No sample schema selected", "done": bool(state.get("agent_readiness_ok") or state.get("sample_data_ready"))},
        {"tab": "oauth-clients", "label": "OAuth client", "detail": oauth_detail, "done": oauth_detail != "No client registered"},
        {"tab": "token", "label": "OAuth token", "detail": token_detail, "done": bool(state.get("access_token"))},
        {"tab": "provision", "label": "Database lifecycle", "detail": lifecycle or "Not checked", "done": lifecycle.upper() == "AVAILABLE"},
    ]


def testing_status_items(state: dict[str, object]) -> list[dict[str, str | bool]]:
    """Keep the external-client dock deliberately free of setup internals."""
    token_details = state.get("oauth_token_details", {})
    token_detail = "No external OAuth token"
    if state.get("access_token") and isinstance(token_details, dict):
        expires_at = token_details.get("expires_at")
        expires_in = max(0, int(float(expires_at) - time.time())) if expires_at is not None else token_details.get("expires_in")
        parts = [f"Expires in {expires_in}s" if expires_in is not None else "Token acquired", str(token_details.get("scope") or "")]
        token_detail = " · ".join(part for part in parts if part)
        if state.get("oauth_token_source"):
            token_detail = f"{token_detail} · {state['oauth_token_source']}"
    selected_team = str(state.get("selected_team") or "")
    team_detail = selected_team or "No team selected"
    if selected_team and state.get("selected_card"):
        team_detail += " · Agent Card loaded"
    return [
        {"tab": "test-token", "label": "OAuth token", "detail": token_detail, "done": bool(state.get("access_token"))},
        {"tab": "test-team", "label": "Team selection", "detail": team_detail, "done": bool(selected_team and state.get("selected_card"))},
    ]


def render_workspace(template_name: str, section: str):
    """Render a normal Flask page with the shared setup state and status dock."""
    state = volatile_state()
    visited_navigation = state.setdefault("visited_navigation", [])
    if section not in visited_navigation:
        visited_navigation.append(section)
    provision_settings = state.setdefault("provision_settings", load_last_settings())
    sample_settings = state.setdefault("sample_settings", {key: provision_settings.get(key, "") for key in SAMPLE_SCHEMA_SETTING_KEYS})
    select_ai_settings = state.setdefault("select_ai_settings", {key: provision_settings.get(key, "") for key in SELECT_AI_SETTING_KEYS})
    admin_settings = state.setdefault("admin_settings", {key: provision_settings.get(key, "") for key in ADMIN_SETTING_KEYS})
    settings = dict(session.get("settings", {}))
    settings.setdefault("profile", provision_settings.get("provision_profile", ""))
    settings.setdefault("region", provision_settings.get("provision_region", ""))
    if not settings.get("database_ocid"):
        settings["database_ocid"] = provision_settings.get("database_ocid", "")
    if not settings.get("database_ocid") and isinstance(state.get("provisioned_database"), dict):
        settings["database_ocid"] = state["provisioned_database"].get("id", "")
    select_ai_settings.setdefault("oci_region", settings.get("region", ""))
    if not state.get("agents") and provision_settings.get("known_teams"):
        try:
            remembered_teams = json.loads(str(provision_settings["known_teams"]))
            if isinstance(remembered_teams, list):
                state["agents"] = remembered_teams
        except ValueError:
            pass
    if "oauth_clients" not in state:
        try:
            saved_clients = json.loads(str(provision_settings.get("oauth_clients", "[]")))
            state["oauth_clients"] = saved_clients if isinstance(saved_clients, list) else []
        except ValueError:
            state["oauth_clients"] = []
    wallet_settings = state.get("wallet_settings", {})
    wallet_database_ocid = str(wallet_settings.get("database_ocid") or settings.get("database_ocid") or "")
    wallet_dir = state.get("wallet_dir") or existing_wallet_directory(wallet_database_ocid) or discovered_wallet_directory()
    wallet_dir = str(wallet_dir) if wallet_dir else None
    return render_template(
        template_name, nav_section=section, page_view=request.args.get("view", ""), settings=settings, profiles=oci_profiles(),
        provisioned_database=state.get("provisioned_database"), delete_request=state.get("delete_request"),
        provision_settings=provision_settings, wallet_settings=wallet_settings, wallet_dir=wallet_dir,
        wallet_database_ocid=wallet_database_ocid, wallet_status=state.get("wallet_status"),
        admin_connection_ok=state.get("admin_connection_ok", False), admin_connection_summary=state.get("admin_connection_summary", ""),
        masked_secret=MASKED_SECRET, admin_password_cached=bool(state.get("admin_password")), wallet_password_cached=bool(state.get("admin_wallet_password")), target_schema_password_cached=any(str(key).startswith("target_schema_password:") for key in state),
        admin_settings=admin_settings, select_ai_settings=select_ai_settings,
        sample_settings=sample_settings, sample_tables=state.get("sample_tables", []), sample_data_ready=state.get("sample_data_ready", False), target_select_ai_ready=state.get("target_select_ai_ready", False), target_select_ai_profiles=state.get("target_select_ai_profiles", []), target_select_ai_credentials=state.get("target_select_ai_credentials", []), target_select_ai_privileges=state.get("target_select_ai_privileges", []),
        select_ai_checklist=state.get("select_ai_checklist", {}), select_ai_profiles=state.get("select_ai_profiles", []),
        select_ai_profile_ready=(bool(state.get("select_ai_checklist", {}).get("AI profile")) or bool(select_ai_settings.get("profile_name") and str(select_ai_settings.get("profile_name")).upper() in {str(value).upper() for value in state.get("select_ai_profiles", [])})),
        select_ai_credentials=state.get("select_ai_credentials", []), select_ai_credential_attributes=state.get("select_ai_credential_attributes", {}),
        select_ai_profile_attributes=state.get("select_ai_profile_attributes", {}), select_ai_debug=state.get("select_ai_debug", []),
        select_ai_debug_enabled=SELECT_AI_DEBUG, agent_install_debug=state.get("agent_install_debug", []), agents=state.get("agents", []), selected_card=state.get("selected_card"),
        section_feedback=state.get("section_feedback", {}), selected_team=state.get("selected_team", ""),
        chat_history=state.get("chat_history", []),
        pending_a2a_task_id=state.get("pending_a2a_task_id", ""),
        a2a_context_id=state.get("a2a_context_id", ""),
        last_a2a_task_state=state.get("last_a2a_task_state", ""),
        last_a2a_task_id=state.get("last_a2a_task_id", ""),
        last_a2a_task_diagnostic=state.get("last_a2a_task_diagnostic", {}),
        last_discovery=state.get("last_discovery", {}),
        access_token=bool(state.get("access_token")), oauth_token_details=state.get("oauth_token_details", {}), oauth_token_source=state.get("oauth_token_source", ""),
        oauth_clients=state.get("oauth_clients", []), oauth_one_time_secret=state.pop("oauth_one_time_secret", None),
        oauth_callback_uri=url_for("oauth_callback_route", _external=True), oauth_listener_enabled=bool(state.get("oauth_callback_state")), oauth_callback_received=bool(state.get("oauth_callback_received")), oauth_metadata=state.get("oauth_metadata"),
        setup_status=setup_status_items(state, settings, provision_settings, select_ai_settings, admin_settings, wallet_dir),
        testing_status=testing_status_items(state),
        dock_mode="testing" if section.startswith("test") else "setup",
        setup_ready=bool(state.get("agent_readiness_ok") and state.get("sample_agent_installed")),
        demo_verification=state.get("demo_verification", []),
        demo_verified=bool(state.get("demo_verified")),
        nav_visited=set(visited_navigation),
        samples=sample_catalog(),
    )


def verify_demo(state: dict[str, object]) -> list[dict[str, str | bool]]:
    """Perform the safe, setup-side checks needed before external A2A testing.

    This deliberately excludes OAuth: an external token belongs to the testing
    half and is short-lived.  ADMIN credentials are memory-only, so a restarted
    console reports that fact rather than implying it can test the database.
    """
    stored = load_last_settings()
    schema = str(state.get("sample_settings", {}).get("target_schema") or stored.get("target_schema") or "").upper()
    profile = str(state.get("select_ai_settings", {}).get("profile_name") or stored.get("profile_name") or "").upper()
    database_ocid = str(session.get("settings", {}).get("database_ocid") or stored.get("database_ocid") or "")
    wallet_dir = existing_wallet_directory(database_ocid) if database_ocid else None
    checks: list[dict[str, str | bool]] = [
        {"label": "OCI context", "detail": f"{stored.get('provision_profile') or 'No profile'} · {stored.get('provision_region') or 'No region'}", "ok": bool(stored.get("provision_profile") and stored.get("provision_region"))},
        {"label": "Database and wallet", "detail": "Local wallet found" if wallet_dir else "Database OCID or local wallet is missing", "ok": bool(database_ocid and wallet_dir)},
        {"label": "ADMIN connection", "detail": "Available in this browser session" if state.get("admin_connection_ok") else "Test ADMIN after each app restart", "ok": bool(state.get("admin_connection_ok"))},
    ]
    if not schema or not profile:
        checks.append({"label": "Target schema profile", "detail": "Choose a target schema and Select AI profile", "ok": False})
    elif not state.get("admin_connection_ok"):
        checks.append({"label": "Target schema profile", "detail": f"Cannot verify {schema}.{profile} without the current ADMIN connection", "ok": False})
    else:
        status = target_select_ai_profile_status(state, schema, profile)
        checks.append({"label": "Target schema profile", "detail": f"{schema}.{profile} is {status or 'not found'}", "ok": status == "ENABLED"})
        with database_connection(state) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM DBA_AI_AGENTS WHERE OWNER = :owner", owner=schema)
                agent_count = int(cursor.fetchone()[0])
        checks.append({"label": "Agent objects", "detail": f"{agent_count} agent object(s) found in {schema}", "ok": agent_count > 0})
    state["demo_verification"] = checks
    state["demo_verified"] = all(bool(check["ok"]) for check in checks)
    return checks


def select_ai_debug(state: dict[str, object], label: str, statement: str = "", **parameters: object) -> None:
    """Emit a useful, redacted SQL trace only when explicitly enabled."""
    if not SELECT_AI_DEBUG:
        return
    redacted = {
        key: "[redacted]" if any(word in key.lower() for word in ("key", "password", "private")) else str(value)
        for key, value in parameters.items()
    }
    entry = f"{label}\nSQL: {statement.strip()}\nBinds: {json.dumps(redacted, sort_keys=True)}"
    print(f"[Select AI debug]\n{entry}", flush=True)
    state.setdefault("select_ai_debug", []).append(entry)


def agent_installer_debug(state: dict[str, object], label: str, statement: str = "") -> None:
    """Show the exact non-secret SQL block executed by the sample installer in debug mode."""
    if not SELECT_AI_DEBUG:
        return
    entry = f"{label}\nSQL:\n{statement.strip()}"
    print(f"[Agent installer debug]\n{entry}", flush=True)
    state.setdefault("agent_install_debug", []).append(entry)


def load_last_settings() -> dict[str, object]:
    """Load non-secret provisioning form values saved by this local app."""
    try:
        payload = json.loads(LAST_SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    settings: dict[str, object] = {}
    for key in PERSISTED_SETTING_KEYS:
        value = payload.get(key)
        if isinstance(value, (str, int, float)):
            settings[key] = str(value)
    return settings


def save_last_settings(settings: dict[str, object]) -> None:
    """Persist form configuration, never passwords or tokens, with owner-only access."""
    payload = load_last_settings()
    payload.update({key: settings[key] for key in PERSISTED_SETTING_KEYS if key in settings})
    temporary = LAST_SETTINGS_FILE.with_name(f".{LAST_SETTINGS_FILE.name}.{secrets.token_hex(8)}.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(LAST_SETTINGS_FILE)


def oracle_headers(token: str) -> dict[str, str]:
    return {"Accept": "application/json", "Content-Type": "application/json", "Authorization": f"Bearer {token}"}


def safe_jwt_claims(token: str) -> dict[str, str]:
    """Read a few non-secret JWT claims for local discovery diagnostics only."""
    try:
        payload = token.split(".")[1]
        decoded = urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        claims = json.loads(decoded)
        if not isinstance(claims, dict):
            return {}
        return {key: str(claims[key]) for key in ("sub", "scope", "cloud_database_name", "exp") if claims.get(key) is not None}
    except (IndexError, ValueError, UnicodeDecodeError):
        return {}


def register_oauth_client(state: dict[str, object], region: str, database_ocid: str, client_name: str, redirect_uris: list[str]) -> dict[str, object]:
    if not state.get("admin_connection_ok") or not isinstance(state.get("admin_connection"), dict):
        raise ValueError("Test the ADMIN wallet connection before registering an OAuth client.")
    if not re.fullmatch(r"[a-z0-9-]+", region) or not database_ocid.startswith("ocid1.autonomousdatabase."):
        raise ValueError("Enter a valid region and Autonomous Database OCID.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _.-]{0,127}", client_name):
        raise ValueError("Client name must be 1–128 letters, numbers, spaces, dots, underscores, or hyphens.")
    if not redirect_uris:
        raise ValueError("Enter at least one HTTPS redirect URI.")
    for uri in redirect_uris:
        parsed = urlparse(uri)
        is_loopback = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        if not parsed.netloc or (parsed.scheme != "https" and not is_loopback):
            raise ValueError("Every redirect URI must be HTTPS, except a local loopback HTTP callback.")
    if len(set(redirect_uris)) != len(redirect_uris):
        raise ValueError("Redirect URIs must be unique.")
    admin = state["admin_connection"]
    endpoint = f"https://dataaccess.adb.{region}.oraclecloudapps.com/adb/auth/v1/connect/databases/{database_ocid}/register"
    response = requests.post(endpoint, auth=(str(admin["username"]), str(admin["password"])), headers={"Accept": "application/json", "Content-Type": "application/json"}, json={"client_name": client_name, "redirect_uris": redirect_uris}, timeout=30)
    if not response.ok:
        raise RuntimeError(response_error(response))
    result = response.json()
    secret = result.pop("client_secret", None)
    if not secret or not result.get("client_id"):
        raise RuntimeError("Oracle's registration response did not include the required client credentials.")
    state["oauth_one_time_secret"] = {"client_id": str(result["client_id"]), "client_secret": str(secret)}
    clients = state.setdefault("oauth_clients", [])
    clients.append(result)
    save_last_settings({"oauth_clients": json.dumps(clients)})
    return result


def external_oauth_endpoints(region: str) -> dict[str, str]:
    if not re.fullmatch(r"[a-z0-9-]+", region):
        raise ValueError("Enter a valid OCI region.")
    base = f"https://dataaccess.adb.{region}.oraclecloudapps.com/adb/auth/v1/connect"
    return {"authorization_endpoint": f"{base}/authorize", "token_endpoint": f"{base}/token"}


def oauth_database_context(state: dict[str, object]) -> dict[str, str]:
    """Return the saved non-secret region and database OCID for external A2A.

    Setup values survive an app/browser session; external OAuth state does not.
    OAuth preparation and discovery therefore must not rely solely on the
    transient Flask session after the page has already shown the saved region.
    """
    settings = {key: str(value) for key, value in dict(session.get("settings", {})).items() if value is not None}
    persisted = state.get("provision_settings")
    if not isinstance(persisted, dict):
        persisted = load_last_settings()
    settings["region"] = settings.get("region") or str(persisted.get("provision_region") or "")
    settings["database_ocid"] = settings.get("database_ocid") or str(persisted.get("database_ocid") or "")
    if not settings.get("database_ocid") and isinstance(state.get("provisioned_database"), dict):
        settings["database_ocid"] = str(state["provisioned_database"].get("id") or "")
    session["settings"] = settings
    return settings


def external_oauth_metadata(state: dict[str, object], region: str, database_ocid: str) -> dict[str, object]:
    if not re.fullmatch(r"[a-z0-9-]+", region) or not database_ocid.startswith("ocid1.autonomousdatabase."):
        raise ValueError("Enter a valid region and Autonomous Database OCID.")
    base = f"https://dataaccess.adb.{region}.oraclecloudapps.com"
    candidates = [
        f"{base}/adb/auth/v1/databases/{database_ocid}/.well-known/oauth-authorization-server",
        f"{base}/.well-known/oauth-authorization-server/adb/auth/v1/databases/{database_ocid}",
        f"{base}/.well-known/openid-configuration",
    ]
    for endpoint in candidates:
        response = requests.get(endpoint, headers={"Accept": "application/json"}, timeout=15)
        if response.ok:
            metadata = response.json()
            if isinstance(metadata, dict) and metadata.get("authorization_endpoint") and metadata.get("token_endpoint"):
                state["oauth_metadata"] = metadata
                return metadata
    raise RuntimeError("Oracle did not expose OAuth authorization metadata at the standard discovery locations. Use the external client's documented configuration instead.")


def exchange_external_oauth_code(state: dict[str, object], region: str, client_id: str, client_secret: str, redirect_uri: str) -> None:
    code = state.get("oauth_authorization_code")
    if not code:
        raise ValueError("Complete the local callback before exchanging a code.")
    if not client_id or not client_secret:
        raise ValueError("Enter the OAuth client ID and client secret.")
    response = requests.post(external_oauth_endpoints(region)["token_endpoint"], headers={"Accept": "application/json"}, data={"grant_type": "authorization_code", "code": str(code), "client_id": client_id, "client_secret": client_secret, "redirect_uri": redirect_uri}, timeout=30)
    if not response.ok:
        raise RuntimeError(response_error(response))
    token_response = response.json()
    token = token_response.get("access_token")
    if not token:
        raise RuntimeError("The authorization-code exchange returned no access token.")
    state["access_token"] = token
    state["oauth_token_source"] = "External OAuth"
    state["oauth_token_details"] = {key: token_response.get(key) for key in ("expires_in", "scope", "token_type") if token_response.get(key) is not None}
    if token_response.get("expires_in") is not None:
        state["oauth_token_details"]["expires_at"] = time.time() + float(token_response["expires_in"])
    state.pop("oauth_authorization_code", None)


def obtain_password_grant_token(state: dict[str, object], region: str, database_ocid: str, username: str, password: str) -> list[object]:
    if not all((region, database_ocid, username, password)):
        raise ValueError("Region, database OCID, database username, and password are required.")
    conn = Connection(region, database_ocid, username, password)
    response = requests.post(conn.token_url, headers={"Accept": "application/json", "Content-Type": "application/json"}, json={"grant_type": "password", "username": username, "password": password}, timeout=30)
    if not response.ok:
        raise RuntimeError(token_error(response))
    token_response = response.json(); token = token_response.get("access_token")
    if not token:
        raise RuntimeError("Oracle returned no access_token.")
    # This diagnostic must not replace an external OAuth token used for A2A chat.
    state["admin_diagnostic_token"] = token
    settings = {"region": region, "database_ocid": database_ocid, "username": username}
    session["settings"] = settings
    state.pop("selected_card", None)
    return refresh_agents(state, settings, token=token, token_source="ADMIN diagnostic")


def refresh_agents(
    state: dict[str, object], settings: dict[str, object], token: str | None = None, token_source: str | None = None
) -> list[object]:
    token = token or state.get("access_token")
    region = str(settings.get("region", ""))
    database_ocid = str(settings.get("database_ocid", ""))
    if not token or not region or not database_ocid:
        raise ValueError("Get a bearer token on the Setup page before refreshing published teams.")
    endpoint = Connection(region, database_ocid, "", "").agents_url
    claims = safe_jwt_claims(str(token))
    diagnostic: dict[str, object] = {
        "endpoint": endpoint,
        "region": region,
        "database_ocid": database_ocid,
        "token_source": token_source or str(state.get("oauth_token_source") or "Unknown"),
        "token_subject": claims.get("sub", "Unavailable"),
        "token_scope": claims.get("scope", "Unavailable"),
        "token_database_ocid": claims.get("cloud_database_name", "Unavailable"),
    }
    token_database = claims.get("cloud_database_name", "")
    if token_database:
        diagnostic["token_database_matches_request"] = token_database.upper() == database_ocid.upper()
    response = requests.get(endpoint, headers=oracle_headers(str(token)), timeout=30)
    diagnostic["http_status"] = response.status_code
    if not response.ok:
        diagnostic["result"] = "Failed"
        state["last_discovery"] = diagnostic
        raise RuntimeError(f"A2A discovery GET {endpoint} failed. {response_error(response)}")
    agents = response.json()
    if not isinstance(agents, list):
        diagnostic["result"] = "Unexpected response"
        state["last_discovery"] = diagnostic
        raise RuntimeError("Oracle returned an unexpected agents response.")
    diagnostic["result"] = "Success"
    diagnostic["published_team_count"] = len(agents)
    state["last_discovery"] = diagnostic
    state["agents"] = agents
    # Discovery is safe to remember: it contains team metadata, never tokens.
    save_last_settings({"known_teams": json.dumps(agents)})
    return agents


def safe_wallet_directory(database_ocid: str) -> Path:
    """Keep generated wallets in this project's ignored wallet directory."""
    suffix = database_ocid.rsplit(".", 1)[-1]
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,128}", suffix):
        raise ValueError("Enter a valid Autonomous Database OCID.")
    return WALLET_ROOT / suffix


def existing_wallet_directory(database_ocid: str) -> Path | None:
    """Return a usable locally generated wallet without persisting its password."""
    try:
        wallet_dir = safe_wallet_directory(database_ocid)
    except ValueError:
        return None
    return wallet_dir if (wallet_dir / "tnsnames.ora").is_file() else None


def discovered_wallet_directory() -> Path | None:
    """Use a single existing local wallet even before its OCID is saved in session."""
    if not WALLET_ROOT.is_dir():
        return None
    wallets = [path.parent for path in WALLET_ROOT.glob("*/tnsnames.ora") if path.parent.is_dir()]
    return wallets[0] if len(wallets) == 1 else None


def database_lifecycle(profile: str, region: str, database_ocid: str) -> str:
    """Read lifecycle state before starting slower wallet or connection work."""
    if profile not in {name for name, _ in oci_profiles()}:
        raise ValueError("Select an OCI profile configured on this computer.")
    if not re.fullmatch(r"[a-z0-9-]+", region):
        raise ValueError("Enter a valid OCI region identifier.")
    safe_wallet_directory(database_ocid)
    executable = shutil.which("oci")
    if not executable:
        raise RuntimeError("The OCI CLI is not installed or is not on this app's PATH.")
    command = [
        executable, "db", "autonomous-database", "get", "--profile", profile,
        "--region", region, "--autonomous-database-id", database_ocid,
    ]
    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=30, check=False)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Timed out while checking database status. OCI did not respond within 30 seconds.") from exc
    if result.returncode:
        raise RuntimeError(f"Could not check database status. {provisioning_error(result, '')}")
    try:
        payload = json.loads(result.stdout)
        return str(payload["data"]["lifecycle-state"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("OCI returned an unexpected database-status response.") from exc


def download_wallet(profile: str, region: str, database_ocid: str, wallet_password: str) -> Path:
    if profile not in {name for name, _ in oci_profiles()}:
        raise ValueError("Select an OCI profile configured on this computer.")
    if not wallet_password:
        raise ValueError("Enter a new wallet password.")
    executable = shutil.which("oci")
    if not executable:
        raise RuntimeError("The OCI CLI is not installed or is not on this app's PATH.")
    wallet_dir = safe_wallet_directory(database_ocid)
    WALLET_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    staging_dir = Path(tempfile.mkdtemp(prefix=".wallet-download-", dir=WALLET_ROOT))
    staging_dir.chmod(0o700)
    wallet_zip = staging_dir / "wallet.zip"
    command = [
        executable, "db", "autonomous-database", "generate-wallet",
        "--profile", profile, "--region", region,
        "--autonomous-database-id", database_ocid,
        "--password", wallet_password,
        "--file", str(wallet_zip), "--generate-type", "SINGLE",
    ]
    try:
        try:
            result = subprocess.run(command, text=True, capture_output=True, timeout=45, check=False)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Wallet generation did not finish within 45 seconds. Check database status and retry after it is AVAILABLE.") from exc
        if result.returncode:
            raise RuntimeError(provisioning_error(result, wallet_password))
        with zipfile.ZipFile(wallet_zip) as archive:
            for member in archive.infolist():
                member_path = (staging_dir / member.filename).resolve()
                if not member_path.is_relative_to(staging_dir.resolve()):
                    raise RuntimeError("Wallet ZIP contained an unsafe path.")
            archive.extractall(staging_dir)
    except (OSError, zipfile.BadZipFile) as exc:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise RuntimeError("OCI generated a wallet ZIP that could not be extracted.") from exc
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise
    for path in staging_dir.rglob("*"):
        if path.is_file():
            path.chmod(0o600)
    # Replace only after a complete new wallet has been generated and extracted.
    if wallet_dir.exists():
        shutil.rmtree(wallet_dir)
    staging_dir.replace(wallet_dir)
    return wallet_dir


def database_connection(state: dict[str, object]):
    details = state.get("admin_connection")
    if not isinstance(details, dict):
        raise ValueError("Download a wallet and test the ADMIN connection first.")
    try:
        import oracledb
    except ImportError as exc:
        raise RuntimeError("The python-oracledb dependency is not installed in .venv yet.") from exc
    return oracledb.connect(
        user=details["username"], password=details["password"], dsn=details["service_name"],
        config_dir=details["wallet_dir"], wallet_location=details["wallet_dir"],
        wallet_password=details["wallet_password"],
    )


def target_schema_connection(state: dict[str, object], username: str, password: str):
    """Connect as the target schema using the already-tested wallet configuration."""
    details = state.get("admin_connection")
    if not isinstance(details, dict):
        raise ValueError("Download a wallet and test the ADMIN connection first.")
    username = checked_identifier(username, "Target schema")
    if not password:
        raise ValueError("Enter the target-schema password for this one-time setup.")
    try:
        import oracledb
    except ImportError as exc:
        raise RuntimeError("The python-oracledb dependency is not installed in .venv yet.") from exc
    return oracledb.connect(
        user=username, password=password, dsn=details["service_name"],
        config_dir=details["wallet_dir"], wallet_location=details["wallet_dir"],
        wallet_password=details["wallet_password"],
    )


def checked_identifier(value: str, label: str) -> str:
    if not SQL_IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError(f"{label} must be an unquoted Oracle identifier (letters, numbers, _, $, #; starts with a letter).")
    return value.upper()


def sample_catalog() -> list[dict[str, object]]:
    """Load local sample manifests; code never accepts an arbitrary script path."""
    samples: list[dict[str, object]] = []
    for manifest_path in sorted(SAMPLES_ROOT.glob("*/sample.json")):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Invalid sample manifest {manifest_path}: {exc}") from exc
        name = str(manifest.get("name", ""))
        if not re.fullmatch(r"[a-z0-9-]+", name) or manifest_path.parent.name != name:
            raise RuntimeError(f"Sample manifest {manifest_path} has an invalid name.")
        manifest["directory"] = manifest_path.parent
        samples.append(manifest)
    return samples


def selected_sample(sample_name: str) -> dict[str, object]:
    for sample in sample_catalog():
        if sample["name"] == sample_name:
            return sample
    raise ValueError("Choose a supported sample package.")


def sample_parameter_values(sample: dict[str, object], form: dict[str, str], schema_name: str, profile_name: str) -> dict[str, str]:
    values = {"TARGET_SCHEMA": checked_identifier(schema_name, "Target schema"), "PROFILE_NAME": checked_identifier(profile_name, "Select AI profile")}
    for parameter in sample.get("parameters", []):
        if not isinstance(parameter, dict):
            raise RuntimeError("Sample manifest has an invalid parameter.")
        name = str(parameter.get("name", ""))
        value = str(form.get(f"sample_parameter_{name}", "")).strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", name) or not value:
            raise ValueError(f"Enter {parameter.get('label', name)}.")
        if name.endswith("_REGION"):
            if not re.fullmatch(r"[a-z0-9-]+", value):
                raise ValueError(f"{parameter.get('label', name)} must be an OCI region.")
        elif name.endswith("_OCID"):
            if not re.fullmatch(r"ocid1\.[A-Za-z0-9._-]+", value):
                raise ValueError(f"{parameter.get('label', name)} must be an OCI OCID.")
        values[name] = value
    return values


def local_sql_blocks(script: str) -> list[str]:
    """Read SQLcl-style blocks delimited by a slash on its own line.

    A slash is a SQLcl separator and is not sent through python-oracledb.
    Unlike ordinary SQL, anonymous PL/SQL and stored-program DDL require their
    final semicolon to remain in the statement sent to the driver.
    """
    def driver_statement(block: str) -> str:
        statement = block.strip()
        first_sql_line = next(
            (line.strip().upper() for line in statement.splitlines()
             if line.strip() and not line.lstrip().startswith("--")),
            "",
        )
        is_plsql = first_sql_line.startswith(("BEGIN", "DECLARE")) or bool(re.match(
            r"CREATE\s+(OR\s+REPLACE\s+)?(?:FUNCTION|PROCEDURE|PACKAGE|TYPE|TRIGGER)\b",
            first_sql_line,
        ))
        return statement if is_plsql else statement.rstrip(";")

    blocks: list[str] = []
    current: list[str] = []
    for line in script.splitlines():
        if line.strip() == "/":
            if statement := "\n".join(current).strip():
                blocks.append(driver_statement(statement))
            current = []
        else:
            current.append(line)
    if statement := "\n".join(current).strip():
        blocks.append(driver_statement(statement))
    return blocks


def run_local_sample(
    state: dict[str, object], sample: dict[str, object], values: dict[str, str], target_schema_password: str
) -> None:
    directory = sample["directory"]
    if not isinstance(directory, Path):
        raise RuntimeError("Sample directory is unavailable.")
    secret_values = {
        values[str(parameter.get("name"))]
        for parameter in sample.get("parameters", [])
        if isinstance(parameter, dict) and parameter.get("secret") and str(parameter.get("name")) in values
    }
    for step in sample.get("steps", []):
        if not isinstance(step, dict) or step.get("connection") not in {"admin", "target"}:
            raise RuntimeError("Sample step must declare an ADMIN or target-schema connection.")
        connection_kind = str(step["connection"])
        if connection_kind == "target" and not target_schema_password:
            raise ValueError("Enter the target-schema password to create this package's agent, tools, task, and team as that user.")
        script_name = str(step.get("script", ""))
        script_path = directory / script_name
        if script_path.parent != directory or not script_path.is_file():
            raise RuntimeError(f"Sample step {script_name} is missing.")
        script = script_path.read_text(encoding="utf-8")
        for key, value in values.items():
            script = script.replace("{{" + key + "}}", value)
        if "{{" in script:
            raise RuntimeError(f"Sample step {script_name} has an unresolved placeholder.")
        connection_factory = database_connection if connection_kind == "admin" else lambda current_state: target_schema_connection(current_state, values["TARGET_SCHEMA"], target_schema_password)
        with connection_factory(state) as connection:
            with connection.cursor() as cursor:
                for number, statement in enumerate(local_sql_blocks(script), start=1):
                    debug_statement = statement
                    for secret_value in secret_values:
                        debug_statement = debug_statement.replace(secret_value, "[redacted]")
                    agent_installer_debug(state, f"{sample['name']} / {script_name} ({connection_kind}) block {number}", debug_statement)
                    cursor.execute(statement)
            connection.commit()


def enable_oci_resource_principal(state: dict[str, object], username: str = "") -> None:
    """Enable the database-managed OCI principal for ADMIN or one target schema."""
    username = checked_identifier(username, "Target schema") if username else "ADMIN"
    statement = "BEGIN DBMS_CLOUD_ADMIN.ENABLE_PRINCIPAL_AUTH(provider => 'OCI', username => :username); END;"
    with database_connection(state) as admin_connection:
        with admin_connection.cursor() as cursor:
            select_ai_debug(state, "Enable OCI Resource Principal", statement, username=username)
            cursor.execute(statement, username=username)
        admin_connection.commit()


def run_select_ai_setup(
    state: dict[str, object], form: dict[str, str], connection=None,
    checklist: dict[str, str] | None = None, resource_principal_username: str = "",
) -> None:
    state.pop("select_ai_debug", None)
    selected_provider = str(form.get("provider") or "oci").lower()
    provider = "oci" if selected_provider == "oci_resource_principal" else selected_provider
    credential_name = "OCI$RESOURCE_PRINCIPAL" if selected_provider == "oci_resource_principal" else checked_identifier(form["credential_name"], "Credential name")
    profile_name = checked_identifier(form["profile_name"], "Profile name")
    checklist = checklist if checklist is not None else state.setdefault("select_ai_checklist", {})
    attributes: dict[str, str] = {"provider": provider, "credential_name": credential_name}
    db_connection = connection or database_connection(state)
    with db_connection as connection:
        with connection.cursor() as cursor:
            if provider == "openai":
                api_key = form["openai_api_key"]
                if not api_key:
                    raise ValueError("Enter the OpenAI API key.")
                statement = "BEGIN DBMS_CLOUD.CREATE_CREDENTIAL(:name, :username, :password); END;"
                select_ai_debug(state, "Create OpenAI credential", statement, name=credential_name, username="OPENAI", api_key=api_key)
                cursor.execute(
                    statement,
                    name=credential_name, username="OPENAI", password=api_key,
                )
                checklist["provider credential"] = "OpenAI credential created"
            elif selected_provider == "oci":
                for key in ("oci_user_ocid", "oci_tenancy_ocid", "oci_private_key", "oci_fingerprint", "oci_compartment_id"):
                    if not form[key]:
                        raise ValueError("Complete all OCI Generative AI credential fields.")
                statement = """BEGIN DBMS_CLOUD.CREATE_CREDENTIAL(
                        credential_name => :name, user_ocid => :user_ocid, tenancy_ocid => :tenancy_ocid,
                        private_key => :private_key, fingerprint => :fingerprint); END;"""
                select_ai_debug(state, "Create OCI signing credential", statement, name=credential_name,
                                user_ocid=form["oci_user_ocid"], tenancy_ocid=form["oci_tenancy_ocid"],
                                fingerprint=form["oci_fingerprint"], private_key=form["oci_private_key"])
                cursor.execute(
                    statement,
                    name=credential_name, user_ocid=form["oci_user_ocid"], tenancy_ocid=form["oci_tenancy_ocid"],
                    private_key=form["oci_private_key"], fingerprint=form["oci_fingerprint"],
                )
                attributes["oci_compartment_id"] = form["oci_compartment_id"]
                if form.get("oci_region"):
                    attributes["region"] = form["oci_region"]
                if form.get("oci_model"):
                    attributes["model"] = form["oci_model"]
                checklist["provider credential"] = "OCI Generative AI signing credential created"
            elif selected_provider == "oci_resource_principal":
                if not form.get("oci_compartment_id"):
                    raise ValueError("Enter the OCI Generative AI compartment OCID.")
                enable_oci_resource_principal(state, resource_principal_username)
                attributes["oci_compartment_id"] = form["oci_compartment_id"]
                if form.get("oci_region"):
                    attributes["region"] = form["oci_region"]
                if form.get("oci_model"):
                    attributes["model"] = form["oci_model"]
                checklist["provider credential"] = "Database-managed OCI$RESOURCE_PRINCIPAL enabled; no API-key credential was created"
            else:
                raise ValueError("Choose OCI API key, OCI Resource Principal, or OpenAI.")
            statement = "BEGIN DBMS_CLOUD_AI.CREATE_PROFILE(:profile, :attributes, 'enabled'); END;"
            select_ai_debug(state, "Create Select AI profile", statement, profile=profile_name, attributes=json.dumps(attributes))
            cursor.execute(
                statement,
                profile=profile_name, attributes=json.dumps(attributes),
            )
            statement = "BEGIN DBMS_CLOUD_AI.SET_PROFILE(:profile); END;"
            select_ai_debug(state, "Set Select AI profile", statement, profile=profile_name)
            cursor.execute(statement, profile=profile_name)
            connection.commit()
            checklist["AI profile"] = f"{profile_name} created and enabled"
            checklist["profile session test"] = f"{profile_name} was accepted by DBMS_CLOUD_AI.SET_PROFILE"
            checklist["database packages"] = "DBMS_CLOUD and DBMS_CLOUD_AI executed successfully and committed"


def read_oci_private_key() -> str:
    """Read an uploaded, unencrypted OCI API-signing PEM key without writing it to disk."""
    uploaded = request.files.get("oci_private_key_file")
    if not uploaded or not uploaded.filename:
        raise ValueError("Choose the OCI API-signing private-key PEM file.")
    raw = uploaded.read(65_537)
    if len(raw) > 65_536:
        raise ValueError("The private-key file is unexpectedly large.")
    try:
        private_key = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("The private-key file must be a UTF-8 PEM file.") from exc
    if "-----BEGIN" not in private_key or "PRIVATE KEY-----" not in private_key:
        raise ValueError("Choose an unencrypted OCI API-signing private-key PEM file.")
    return private_key


def test_select_ai_profile(state: dict[str, object], profile_name: str) -> str:
    state.pop("select_ai_debug", None)
    profile_name = checked_identifier(profile_name, "Profile name")
    with database_connection(state) as connection:
        with connection.cursor() as cursor:
            statement = "SELECT DBMS_CLOUD_AI.GENERATE(:prompt, :profile, 'chat') FROM dual"
            select_ai_debug(state, "Run Select AI live test", statement, prompt="Reply exactly: Select AI is ready.", profile=profile_name)
            cursor.execute(statement, prompt="Reply exactly: Select AI is ready.", profile=profile_name)
            row = cursor.fetchone()
        connection.commit()
    result = str(row[0]) if row and row[0] is not None else "No text returned"
    state.setdefault("select_ai_checklist", {})["live provider test"] = result[:300]
    return result


def inspect_select_ai_profile(state: dict[str, object], profile_name: str) -> dict[str, str]:
    """Read safe profile metadata; credentials and private keys are not query results."""
    profile_name = checked_identifier(profile_name, "Profile name")
    statement = "SELECT ATTRIBUTE_NAME, ATTRIBUTE_VALUE FROM USER_CLOUD_AI_PROFILE_ATTRIBUTES WHERE PROFILE_NAME = :profile ORDER BY ATTRIBUTE_NAME"
    with database_connection(state) as connection:
        with connection.cursor() as cursor:
            select_ai_debug(state, "Inspect stored Select AI profile", statement, profile=profile_name)
            cursor.execute(statement, profile=profile_name)
            attributes = {}
            for name, value in cursor:
                attributes[str(name)] = value.read() if hasattr(value, "read") else str(value)
    state["select_ai_profile_attributes"] = attributes
    select_ai_debug(state, "Stored profile attributes", attributes=json.dumps(attributes, sort_keys=True))
    return attributes


def list_select_ai_profiles(state: dict[str, object]) -> list[str]:
    with database_connection(state) as connection:
        with connection.cursor() as cursor:
            statement = "SELECT PROFILE_NAME FROM USER_CLOUD_AI_PROFILES ORDER BY PROFILE_NAME"
            select_ai_debug(state, "List Select AI profiles", statement)
            cursor.execute(statement)
            profiles = [str(row[0]) for row in cursor]
    state["select_ai_profiles"] = profiles
    selected_profile = str(state.get("select_ai_settings", {}).get("profile_name", "")).upper()
    if selected_profile and selected_profile in {profile.upper() for profile in profiles}:
        state.setdefault("select_ai_checklist", {})["AI profile"] = f"{selected_profile} verified in the connected ADMIN schema"
    return profiles


def target_select_ai_profile_status(state: dict[str, object], schema_name: str, profile_name: str) -> str:
    """Check the profile where the sample agent actually runs, not ADMIN's schema."""
    schema_name = checked_identifier(schema_name, "Target schema")
    profile_name = checked_identifier(profile_name, "Select AI profile")
    with database_connection(state) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT STATUS FROM DBA_CLOUD_AI_PROFILES WHERE OWNER = :owner AND PROFILE_NAME = :profile",
                owner=schema_name, profile=profile_name,
            )
            row = cursor.fetchone()
    return str(row[0]).upper() if row else ""


def grant_target_select_ai_privileges(state: dict[str, object], schema_name: str) -> None:
    """Use the tested ADMIN connection to grant the packages the target user needs."""
    schema_name = checked_identifier(schema_name, "Target schema")
    if schema_name.upper() == "ADMIN":
        # Return without doing it
        return
    with database_connection(state) as connection:
        with connection.cursor() as cursor:
            for package_name in ("DBMS_CLOUD", "DBMS_CLOUD_AI", "DBMS_CLOUD_AI_AGENT"):
                cursor.execute(f"GRANT EXECUTE ON {package_name} TO {schema_name}")
        connection.commit()


def target_select_ai_privilege_status(state: dict[str, object], schema_name: str) -> list[str]:
    """Return the explicit package grants held by the target schema."""
    schema_name = checked_identifier(schema_name, "Target schema")
    required = ("DBMS_CLOUD", "DBMS_CLOUD_AI", "DBMS_CLOUD_AI_AGENT")
    with database_connection(state) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT TABLE_NAME FROM DBA_TAB_PRIVS "
                "WHERE GRANTEE = :schema AND PRIVILEGE = 'EXECUTE' "
                "AND (TABLE_NAME IN ('DBMS_CLOUD', 'DBMS_CLOUD_AI', 'DBMS_CLOUD_AI_AGENT') "
                "OR TABLE_NAME LIKE 'DBMS_CLOUD$PDBCS%')",
                schema=schema_name,
            )
            granted = {str(row[0]).upper() for row in cursor}
    effective = set(granted)
    if any(name.startswith("DBMS_CLOUD$PDBCS") for name in granted):
        effective.add("DBMS_CLOUD")
    return [package for package in required if package in effective]


def list_target_select_ai_resources(state: dict[str, object], schema_name: str, password: str) -> tuple[list[dict[str, str]], list[str]]:
    schema_name = checked_identifier(schema_name, "Target schema")
    with target_schema_connection(state, schema_name, password) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT PROFILE_NAME, STATUS FROM USER_CLOUD_AI_PROFILES ORDER BY PROFILE_NAME")
            profiles = [{"name": str(name), "status": str(status)} for name, status in cursor]
            cursor.execute("SELECT CREDENTIAL_NAME FROM USER_CREDENTIALS ORDER BY CREDENTIAL_NAME")
            credentials = [str(row[0]) for row in cursor]
    state["target_select_ai_profiles"] = profiles
    state["target_select_ai_credentials"] = credentials
    return profiles, credentials


def drop_target_select_ai_profile(state: dict[str, object], schema_name: str, password: str, profile_name: str) -> None:
    schema_name = checked_identifier(schema_name, "Target schema")
    profile_name = checked_identifier(profile_name, "Profile name")
    with target_schema_connection(state, schema_name, password) as connection:
        with connection.cursor() as cursor:
            cursor.execute("BEGIN DBMS_CLOUD_AI.DROP_PROFILE(:profile); END;", profile=profile_name)
        connection.commit()


def drop_target_select_ai_credential(state: dict[str, object], schema_name: str, password: str, credential_name: str) -> None:
    schema_name = checked_identifier(schema_name, "Target schema")
    credential_name = checked_identifier(credential_name, "Credential name")
    if credential_name.upper() == "OCI$RESOURCE_PRINCIPAL":
        raise ValueError("OCI$RESOURCE_PRINCIPAL is database-managed and cannot be deleted from this console.")
    with target_schema_connection(state, schema_name, password) as connection:
        with connection.cursor() as cursor:
            cursor.execute("BEGIN DBMS_CLOUD.DROP_CREDENTIAL(:credential); END;", credential=credential_name)
        connection.commit()


def test_target_select_ai_profile(state: dict[str, object], schema_name: str, password: str, profile_name: str) -> str:
    """Run the same stateless Select AI call that the agent tools will use."""
    schema_name = checked_identifier(schema_name, "Target schema")
    profile_name = checked_identifier(profile_name, "Profile name")
    connection = target_schema_connection(state, schema_name, password)
    try:
        cursor = connection.cursor()
        try:
            cursor.execute(
                "SELECT DBMS_CLOUD_AI.GENERATE(:prompt, :profile, 'chat') FROM dual",
                prompt="Reply exactly: Target schema Select AI is ready.", profile=profile_name,
            )
            row = cursor.fetchone()
        finally:
            cursor.close()
    finally:
        connection.close()
    return str(row[0]) if row and row[0] is not None else "No text returned"


def delete_select_ai_profile(state: dict[str, object], profile_name: str) -> None:
    profile_name = checked_identifier(profile_name, "Profile name")
    with database_connection(state) as connection:
        with connection.cursor() as cursor:
            statement = "BEGIN DBMS_CLOUD_AI.DROP_PROFILE(:profile); END;"
            select_ai_debug(state, "Drop Select AI profile", statement, profile=profile_name)
            cursor.execute(statement, profile=profile_name)
        connection.commit()
    state.pop("select_ai_profile_attributes", None)


def list_select_ai_credentials(state: dict[str, object]) -> list[str]:
    """List only credential names; provider secrets are never read back."""
    with database_connection(state) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT CREDENTIAL_NAME FROM USER_CREDENTIALS ORDER BY CREDENTIAL_NAME")
            credentials = [str(row[0]) for row in cursor]
    state["select_ai_credentials"] = credentials
    return credentials


def inspect_select_ai_credential(state: dict[str, object], credential_name: str) -> dict[str, str]:
    credential_name = checked_identifier(credential_name, "Credential name")
    # USER_CREDENTIALS deliberately exposes metadata only, not key material.
    with database_connection(state) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT CREDENTIAL_NAME, USERNAME, COMMENTS FROM USER_CREDENTIALS WHERE CREDENTIAL_NAME = :name", name=credential_name)
            row = cursor.fetchone()
    if not row:
        raise ValueError(f"Credential {credential_name} was not found in the connected schema.")
    details = {"credential_name": str(row[0]), "username": str(row[1] or ""), "comments": str(row[2] or "")}
    state["select_ai_credential_attributes"] = details
    return details


def delete_select_ai_credential(state: dict[str, object], credential_name: str) -> None:
    credential_name = checked_identifier(credential_name, "Credential name")
    if credential_name.upper() == "OCI$RESOURCE_PRINCIPAL":
        raise ValueError("OCI$RESOURCE_PRINCIPAL is database-managed and cannot be deleted from this console.")
    with database_connection(state) as connection:
        with connection.cursor() as cursor:
            cursor.execute("BEGIN DBMS_CLOUD.DROP_CREDENTIAL(:name); END;", name=credential_name)
        connection.commit()
    state.pop("select_ai_credential_attributes", None)


def selected_agent(state: dict[str, object], team_name: str) -> dict[str, object]:
    for agent in state.get("agents", []):
        if isinstance(agent, dict) and agent.get("name") == team_name:
            return agent
    raise ValueError("Choose a currently discovered agent team.")


def load_team_card(state: dict[str, object], team_name: str) -> None:
    token = state.get("access_token")
    if not token:
        raise ValueError("Obtain an OAuth token before choosing a team.")
    agent = selected_agent(state, team_name)
    card_url = str(agent.get("agent_card_url", ""))
    parsed = urlparse(card_url)
    if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith("oraclecloudapps.com"):
        raise ValueError("The selected Agent Card URL must be an Oracle HTTPS endpoint.")
    response = requests.get(card_url, headers=oracle_headers(str(token)), timeout=30)
    if not response.ok:
        raise RuntimeError(response_error(response))
    state["selected_team"] = team_name
    state["selected_card"] = response.json()


def a2a_context_id(value: object) -> str:
    """Return the server-generated A2A context ID from a task or message."""
    if isinstance(value, dict):
        if isinstance(value.get("contextId"), str):
            return str(value["contextId"])
        # JSON-RPC responses normally place the task/message under result.
        result = value.get("result")
        if isinstance(result, (dict, list)):
            return a2a_context_id(result)
        for child in value.values():
            if context_id := a2a_context_id(child):
                return context_id
    elif isinstance(value, list):
        for child in value:
            if context_id := a2a_context_id(child):
                return context_id
    return ""


def remember_a2a_context(state: dict[str, object], reply: object) -> None:
    """Keep the opaque server context ID for later turns in this browser session."""
    if context_id := a2a_context_id(reply):
        state["a2a_context_id"] = context_id


def send_a2a_message(state: dict[str, object], prompt: str) -> object:
    if not prompt.strip():
        raise ValueError("Enter a chat message.")
    token = state.get("access_token")
    team_name = str(state.get("selected_team", ""))
    if not token or not team_name:
        raise ValueError("Obtain a token and select a discovered team before sending chat.")
    card = state.get("selected_card")
    if not isinstance(card, dict):
        load_team_card(state, team_name)
        card = state["selected_card"]
    endpoint = str(card.get("url") or selected_agent(state, team_name).get("agent_card_url", ""))
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith("oraclecloudapps.com"):
        raise ValueError("The Agent Card does not provide a valid Oracle A2A endpoint.")
    pending_task_id = str(state.get("pending_a2a_task_id", ""))
    pending_task_state = normalized_a2a_task_state(state.get("last_a2a_task_state", ""))
    if pending_task_id and pending_task_state not in {"input-required", "auth-required"}:
        raise ValueError(
            f"Task {pending_task_id} is still {pending_task_state or 'pending'}. "
            "Check its status before sending another message."
        )

    message: dict[str, object] = {
        "messageId": str(uuid.uuid4()),
        "role": "user",
        "parts": [{"kind": "text", "text": prompt.strip()}],
    }
    # A2A v0.3: use contextId for conversational continuity. For a task that
    # explicitly requests human input, include both IDs to resume that task.
    if context_id := str(state.get("a2a_context_id", "")):
        message["contextId"] = context_id
    if pending_task_id:
        message["taskId"] = pending_task_id
    # A2A v0.3 places continuation IDs on Message. Oracle's JSON-RPC examples
    # also accept them at params level, so preserve them in both locations.
    params: dict[str, object] = {"message": message}
    if context_id := str(state.get("a2a_context_id", "")):
        params["contextId"] = context_id
    if pending_task_id:
        params["taskId"] = pending_task_id
    payload = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "message/send", "params": params}
    response = requests.post(endpoint, headers=oracle_headers(str(token)), json=payload, timeout=60)
    if not response.ok:
        raise RuntimeError(response_error(response))
    reply = response.json()
    remember_a2a_context(state, reply)
    task_id = a2a_task_id(reply)
    state.setdefault("chat_history", []).append({"role": "user", "text": prompt.strip()})
    if task_id:
        state["pending_a2a_task_id"] = task_id
        state["last_a2a_task_id"] = task_id
        state["last_a2a_task_state"] = a2a_task_state(reply)
        reply = wait_for_a2a_task(state, wait_seconds=15)
        if state.get("pending_a2a_task_id"):
            rendered_reply = f"Task submitted and is still running after 15 seconds. Check task status to retrieve the completed response.\n\nTask ID: {task_id}"
            state["chat_history"].append({"role": "assistant", "text": rendered_reply})
    else:
        rendered_reply = a2a_text(reply) or json.dumps(reply, indent=2)
        state["chat_history"].append({"role": "assistant", "text": rendered_reply})
    state["chat_history"] = state["chat_history"][-12:]
    return reply


def a2a_text(value: object) -> str:
    """Extract text parts from an A2A JSON-RPC response without assuming its outer shape."""
    if isinstance(value, dict):
        if value.get("kind") == "text" and isinstance(value.get("text"), str):
            return value["text"]
        return "\n\n".join(part for child in value.values() if (part := a2a_text(child)))
    if isinstance(value, list):
        return "\n\n".join(part for child in value if (part := a2a_text(child)))
    return ""


def a2a_task_id(value: object) -> str:
    """Find an A2A task object (v0.3 task results expose id plus status)."""
    if isinstance(value, dict):
        if (value.get("kind") == "task" or isinstance(value.get("status"), dict)) and isinstance(value.get("id"), str):
            return value["id"]
        for child in value.values():
            if task_id := a2a_task_id(child):
                return task_id
    elif isinstance(value, list):
        for child in value:
            if task_id := a2a_task_id(child):
                return task_id
    return ""


def a2a_task_state(reply: object) -> str:
    if isinstance(reply, dict):
        result = reply.get("result")
        if isinstance(result, dict) and isinstance(result.get("status"), dict):
            return str(result["status"].get("state", ""))
    return ""


def normalized_a2a_task_state(value: object) -> str:
    """Normalize equivalent A2A state spellings across server versions."""
    return str(value or "").strip().lower().replace("_", "-")


def poll_a2a_task(state: dict[str, object], add_to_history: bool = True) -> object:
    token = state.get("access_token")
    task_id = str(state.get("pending_a2a_task_id", ""))
    team_name = str(state.get("selected_team", ""))
    if not token or not task_id or not team_name:
        raise ValueError("Send a message that returns a task before checking task status.")
    card = state.get("selected_card")
    if not isinstance(card, dict):
        load_team_card(state, team_name)
        card = state["selected_card"]
    endpoint = str(card.get("url") or selected_agent(state, team_name).get("agent_card_url", ""))
    payload = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "tasks/get", "params": {"id": task_id}}
    response = requests.post(endpoint, headers=oracle_headers(str(token)), json=payload, timeout=60)
    if not response.ok:
        raise RuntimeError(response_error(response))
    reply = response.json()
    remember_a2a_context(state, reply)
    state_value = a2a_task_state(reply)
    state["last_a2a_task_id"] = task_id
    state["last_a2a_task_state"] = state_value
    state["last_a2a_task_diagnostic"] = reply
    rendered_reply = a2a_text(reply) or json.dumps(reply, indent=2)
    if add_to_history:
        state.setdefault("chat_history", []).append({"role": "assistant", "text": f"Task {task_id} status: {state_value or 'received'}\n\n{rendered_reply}"})
        state["chat_history"] = state["chat_history"][-12:]
    if normalized_a2a_task_state(state_value) in {"completed", "failed", "canceled", "cancelled", "rejected"}:
        state.pop("pending_a2a_task_id", None)
    return reply


def wait_for_a2a_task(state: dict[str, object], wait_seconds: int = 15) -> object:
    """Offer synchronous chat UX for A2A tasks without abandoning slower work."""
    reply: object = {}
    for attempt in range(wait_seconds):
        reply = poll_a2a_task(state, add_to_history=False)
        state_value = normalized_a2a_task_state(a2a_task_state(reply))
        if state_value in {"input-required", "auth-required"}:
            task_id = str(state.get("last_a2a_task_id", ""))
            rendered_reply = a2a_text(reply) or json.dumps(reply, indent=2)
            state.setdefault("chat_history", []).append({"role": "assistant", "text": f"Task {task_id} requires input. Reply in this chat to continue.\n\n{rendered_reply}"})
            return reply
        if not state.get("pending_a2a_task_id"):
            task_id = str(state.get("last_a2a_task_id", ""))
            state_value = a2a_task_state(reply) or "received"
            rendered_reply = a2a_text(reply) or json.dumps(reply, indent=2)
            state.setdefault("chat_history", []).append({"role": "assistant", "text": f"Task {task_id} status: {state_value}\n\n{rendered_reply}"})
            return reply
        if attempt < wait_seconds - 1:
            time.sleep(1)
    return reply


def update_oci_profile_routing(state: dict[str, object], profile_name: str, region: str, compartment_id: str, model: str) -> None:
    profile_name = checked_identifier(profile_name, "Profile name")
    if not re.fullmatch(r"[a-z0-9-]+", region):
        raise ValueError("Enter a valid OCI Generative AI region, for example us-chicago-1.")
    if not compartment_id.startswith("ocid1.compartment."):
        raise ValueError("Enter the OCI Generative AI compartment OCID.")
    with database_connection(state) as connection:
        with connection.cursor() as cursor:
            cursor.execute("BEGIN DBMS_CLOUD_AI.SET_ATTRIBUTE(:profile, 'region', :region); END;", profile=profile_name, region=region)
            cursor.execute("BEGIN DBMS_CLOUD_AI.SET_ATTRIBUTE(:profile, 'oci_compartment_id', :compartment); END;", profile=profile_name, compartment=compartment_id)
            if model:
                cursor.execute("BEGIN DBMS_CLOUD_AI.SET_ATTRIBUTE(:profile, 'model', :model); END;", profile=profile_name, model=model)
        connection.commit()


def run_team_action(state: dict[str, object], action: str, team_name: str, form: dict[str, str]) -> None:
    team_name = checked_identifier(team_name, "Team name")
    with database_connection(state) as connection:
        with connection.cursor() as cursor:
            if action == "create":
                agent_name = checked_identifier(form["agent_name"], "Agent name")
                task_name = checked_identifier(form["task_name"], "Task name")
                attributes = json.dumps({"agents": [{"name": agent_name, "task": task_name}], "process": "sequential"})
                cursor.execute("BEGIN DBMS_CLOUD_AI_AGENT.CREATE_TEAM(:team, :attributes, 'enabled', :description); END;", team=team_name, attributes=attributes, description=form.get("description", ""))
            elif action == "enable":
                cursor.execute("BEGIN DBMS_CLOUD_AI_AGENT.ENABLE_TEAM(:team); END;", team=team_name)
            elif action == "disable":
                cursor.execute("BEGIN DBMS_CLOUD_AI_AGENT.DISABLE_TEAM(:team); END;", team=team_name)
            elif action == "delete":
                if form.get("confirmation") != "DELETE":
                    raise ValueError("Type DELETE to permanently remove this agent team.")
                cursor.execute("BEGIN DBMS_CLOUD_AI_AGENT.DROP_TEAM(:team); END;", team=team_name)
            else:
                raise ValueError("Unknown team action.")
        connection.commit()


def sample_schema_readiness(state: dict[str, object], schema_name: str) -> list[dict[str, object]]:
    schema_name = checked_identifier(schema_name, "Target schema")
    with database_connection(state) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT TABLE_NAME, NUM_ROWS FROM ALL_TABLES WHERE OWNER = :owner AND TABLE_NAME IN ('DEMO_CUSTOMERS','DEMO_PRODUCTS','DEMO_ORDERS') ORDER BY TABLE_NAME", owner=schema_name)
            tables = [{"name": str(name), "rows": int(rows) if rows is not None else None} for name, rows in cursor]
            # NUM_ROWS may be stale; use an exact count for the known small data set.
            for table in tables:
                cursor.execute(f"SELECT COUNT(*) FROM {schema_name}.{table['name']}")
                table["rows"] = int(cursor.fetchone()[0])
    state["sample_tables"] = tables
    state["sample_data_ready"] = {row["name"] for row in tables} == set(SAMPLE_TABLES) and all(row["rows"] for row in tables)
    return tables


def create_sample_data(state: dict[str, object], schema_name: str) -> list[dict[str, object]]:
    schema_name = checked_identifier(schema_name, "Target schema")
    with database_connection(state) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM ALL_TABLES WHERE OWNER = :owner AND TABLE_NAME IN ('DEMO_CUSTOMERS','DEMO_PRODUCTS','DEMO_ORDERS')", owner=schema_name)
            if cursor.fetchone()[0]:
                raise ValueError("Demo tables already exist in this schema. The console will not overwrite them.")
            cursor.execute(f"CREATE TABLE {schema_name}.DEMO_CUSTOMERS (CUSTOMER_ID NUMBER PRIMARY KEY, CUSTOMER_NAME VARCHAR2(100), REGION VARCHAR2(30), SEGMENT VARCHAR2(30))")
            cursor.execute(f"CREATE TABLE {schema_name}.DEMO_PRODUCTS (PRODUCT_ID NUMBER PRIMARY KEY, PRODUCT_NAME VARCHAR2(100), CATEGORY VARCHAR2(40), UNIT_PRICE NUMBER(10,2))")
            cursor.execute(f"CREATE TABLE {schema_name}.DEMO_ORDERS (ORDER_ID NUMBER PRIMARY KEY, CUSTOMER_ID NUMBER REFERENCES {schema_name}.DEMO_CUSTOMERS, PRODUCT_ID NUMBER REFERENCES {schema_name}.DEMO_PRODUCTS, ORDER_DATE DATE, QUANTITY NUMBER, SALES_AMOUNT NUMBER(12,2), STATUS VARCHAR2(20))")
            cursor.executemany(f"INSERT INTO {schema_name}.DEMO_CUSTOMERS VALUES (:1,:2,:3,:4)", [(1,"Acme Health","East","Enterprise"),(2,"Beacon Retail","West","Mid-Market"),(3,"Cedar Labs","South","Enterprise"),(4,"Delta Foods","East","Small Business")])
            cursor.executemany(f"INSERT INTO {schema_name}.DEMO_PRODUCTS VALUES (:1,:2,:3,:4)", [(1,"Analytics Suite","Software",1200),(2,"Insight Service","Services",450),(3,"Data Sensor","Hardware",850)])
            cursor.executemany(f"INSERT INTO {schema_name}.DEMO_ORDERS VALUES (:1,:2,:3,DATE '2026-01-01'+:4,:5,:6,:7)", [(101,1,1,4,3,3600,"CLOSED"),(102,2,2,31,8,3600,"CLOSED"),(103,3,3,60,2,1700,"OPEN"),(104,1,2,91,6,2700,"CLOSED"),(105,4,1,121,1,1200,"OPEN"),(106,2,3,151,4,3400,"CLOSED")])
        connection.commit()
    return sample_schema_readiness(state, schema_name)


def create_target_schema(state: dict[str, object], schema_name: str, password: str) -> None:
    schema_name = checked_identifier(schema_name, "Target schema")
    if not re.fullmatch(r"[A-Za-z0-9!@#$%^&*_-]{12,128}", password):
        raise ValueError("Use a 12+ character schema password containing only letters, numbers, or ! @ # $ % ^ & * _ -.")
    with database_connection(state) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM DBA_USERS WHERE USERNAME = :name", name=schema_name)
            if cursor.fetchone()[0]:
                raise ValueError(f"Schema {schema_name} already exists; select it instead.")
            cursor.execute(f'CREATE USER {schema_name} IDENTIFIED BY "{password}"')
            cursor.execute(f"GRANT CONNECT, RESOURCE TO {schema_name}")
            cursor.execute(f"ALTER USER {schema_name} QUOTA UNLIMITED ON DATA")
        connection.commit()


def run_oracle_sample_installer(state: dict[str, object], schema_name: str, profile_name: str, script_name: str, source_url: str | None = None, expected_sha256: str | None = None) -> None:
    """Execute Oracle's current sample SQL*Plus script with safe, validated inputs."""
    schema_name = checked_identifier(schema_name, "Target schema")
    profile_name = checked_identifier(profile_name, "Select AI profile")
    if script_name not in {"oracle_ai_database_agent_tool.sql", "oracle_ai_database_agent.sql"}:
        raise ValueError("Unknown Oracle sample installer.")
    response = requests.get(source_url or f"{ORACLE_SAMPLE_BASE_URL}/{script_name}", timeout=30)
    if not response.ok:
        raise RuntimeError(f"Could not download Oracle's sample installer: {response_error(response)}")
    script_fingerprint = hashlib.sha256(response.content).hexdigest()
    if expected_sha256 and script_fingerprint.lower() != expected_sha256.lower():
        raise RuntimeError(
            f"{script_name} did not match the sample manifest SHA-256. Expected {expected_sha256}, got {script_fingerprint}."
        )
    blocks: list[str] = []
    current: list[str] = []
    for line in response.text.splitlines():
        stripped = line.strip()
        if stripped == "/":
            if current:
                blocks.append("\n".join(current)); current = []
        elif not stripped or stripped.upper() in {"REM", "PROMPT", "SET", "VAR"} or stripped.upper().startswith(("REM ", "PROMPT ", "SET ", "VAR ", "EXEC :V_")):
            continue
        else:
            current.append(line.replace(":v_schema", f"'{schema_name}'").replace(":v_ai_profile_name", f"'{profile_name}'"))
    if current:
        blocks.append("\n".join(current))
    agent_installer_debug(
        state,
        f"Downloaded {script_name}; SHA-256 {script_fingerprint}; parsed {len(blocks)} executable block(s).",
    )
    with database_connection(state) as connection:
        with connection.cursor() as cursor:
            for block_number, block in enumerate(blocks, start=1):
                statement = block.strip()
                first_sql_line = next(
                    (line.strip() for line in statement.splitlines() if line.strip() and not line.lstrip().startswith("--")),
                    "",
                )
                if not first_sql_line.upper().startswith(("BEGIN", "DECLARE", "CREATE OR REPLACE")):
                    statement = statement.rstrip(";")
                agent_installer_debug(state, f"{script_name} block {block_number}", statement)
                try:
                    cursor.execute(statement)
                except Exception as exc:
                    agent_installer_debug(state, f"{script_name} block {block_number} failed: {exc}", statement)
                    raise RuntimeError(f"{script_name} block {block_number} failed: {exc}") from exc
        connection.commit()


def deploy_sample(state: dict[str, object], sample_name: str, schema_name: str, profile_name: str, form: dict[str, str]) -> str:
    """Install one declared package; never combine unrelated sample artifacts."""
    sample = selected_sample(sample_name)
    schema_name = checked_identifier(schema_name, "Target schema")
    profile_name = checked_identifier(profile_name, "Select AI profile")
    if target_select_ai_profile_status(state, schema_name, profile_name) != "ENABLED":
        raise ValueError(f"Select AI profile {profile_name} is not enabled in target schema {schema_name}.")
    if sample.get("requires_data"):
        sample_schema_readiness(state, schema_name)
        if not state.get("sample_data_ready"):
            raise ValueError(f"{sample['title']} requires the controlled sales data in {schema_name}.")
    if upstream := sample.get("upstream"):
        if not isinstance(upstream, dict):
            raise RuntimeError("Sample manifest has an invalid upstream declaration.")
        for item in upstream.get("scripts", []):
            if not isinstance(item, dict):
                raise RuntimeError("Sample manifest has an invalid upstream script declaration.")
            run_oracle_sample_installer(
                state, schema_name, profile_name, str(item["name"]), str(item["url"]), str(item["sha256"])
            )
            if item["name"] == "oracle_ai_database_agent_tool.sql":
                ensure_oracle_sample_tools_valid(state, schema_name)
    else:
        run_local_sample(
            state, sample, sample_parameter_values(sample, form, schema_name, profile_name),
            form.get("target_schema_password", ""),
        )
    return str(sample["team_name"])


def ensure_oracle_sample_tools_valid(state: dict[str, object], schema_name: str) -> None:
    """Fail the UI install when Oracle stored an invalid sample package body.

    CREATE PACKAGE BODY can succeed at the DDL level while leaving a stored
    INVALID body and diagnostics in DBA_ERRORS.  Continuing to create a team
    in that state makes later A2A chat failures needlessly opaque.
    """
    schema_name = checked_identifier(schema_name, "Target schema")
    with database_connection(state) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT OBJECT_TYPE, STATUS FROM DBA_OBJECTS "
                "WHERE OWNER = :owner AND OBJECT_NAME = 'ORACLE_AI_DATA_RETRIEVAL_FUNCTIONS' "
                "AND OBJECT_TYPE IN ('PACKAGE', 'PACKAGE BODY')",
                owner=schema_name,
            )
            statuses = {str(object_type): str(status) for object_type, status in cursor}
            cursor.execute(
                "SELECT LINE, POSITION, TEXT FROM DBA_ERRORS "
                "WHERE OWNER = :owner AND NAME = 'ORACLE_AI_DATA_RETRIEVAL_FUNCTIONS' "
                "ORDER BY SEQUENCE",
                owner=schema_name,
            )
            errors = [f"line {line}, column {position}: {str(text).strip()}" for line, position, text in cursor]
    invalid = [kind for kind in ("PACKAGE", "PACKAGE BODY") if statuses.get(kind) != "VALID"]
    if invalid or errors:
        detail = "; ".join(errors[:3]) or f"invalid or missing: {', '.join(invalid)}"
        agent_installer_debug(state, "Oracle sample tool package validation failed", detail)
        raise RuntimeError(
            "Oracle stored an invalid ORACLE_AI_DATA_RETRIEVAL_FUNCTIONS package; the team was not installed. "
            f"{detail}. Run Oracle's approved SQLcl tool script directly, or review the installer debug SHA-256."
        )


def response_error(response: requests.Response) -> str:
    try:
        body = json.dumps(response.json(), indent=2)
    except ValueError:
        body = response.text[:1_500]
    return f"Oracle returned HTTP {response.status_code}: {body}"


def token_error(response: requests.Response) -> str:
    detail = response_error(response)
    if response.status_code == 500 and "AccessToken.getGuid" in response.text:
        return (
            "Oracle's token service returned an internal error while initializing the database access token. "
            "Confirm that the database lifecycle state is Available, the A2A tag is adb$feature={\"name\":\"a2a_server\",\"enable\":true}, "
            "and the database has finished applying the tag; then retry. This is a database-service error, not a password-format error. "
            + detail
        )
    return detail


def oci_profiles() -> list[tuple[str, str]]:
    """Return locally configured OCI profiles without exposing credentials."""
    config = ConfigParser(interpolation=None)
    config.read(Path.home() / ".oci" / "config")
    return [(section, config.get(section, "region", fallback="")) for section in config.sections()]


def provisioning_error(result: subprocess.CompletedProcess[str], secret: str) -> str:
    output = (result.stderr or result.stdout or "OCI CLI returned no diagnostic output.").strip()
    if secret:
        output = output.replace(secret, "[redacted]")
    return f"OCI CLI exited with {result.returncode}: {output[:1_500]}"


def validate_vault_secret(executable: str, profile: str, region: str, secret_id: str, version_number: str) -> None:
    """Fail before provisioning if the selected profile cannot resolve the secret."""
    command = [
        executable, "secrets", "secret-bundle", "get",
        "--profile", profile,
        "--region", region,
        "--secret-id", secret_id,
    ]
    if version_number:
        try:
            version = int(version_number)
        except ValueError as exc:
            raise ValueError("Secret version must be a positive whole number.") from exc
        if version < 1:
            raise ValueError("Secret version must be a positive whole number.")
        command.extend(["--version-number", str(version)])
    else:
        command.extend(["--stage", "CURRENT"])
    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=30, check=False)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Timed out while validating the Vault secret; check the selected region and try again.") from exc
    if result.returncode:
        detail = provisioning_error(result, secret_id)
        raise RuntimeError(
            "Vault-secret preflight failed. The secret must be in the selected region and have a readable CURRENT "
            "(or selected) version. Confirm the secret OCID, version, and IAM permission. " + detail
        )


def create_database(
    profile: str,
    region: str,
    compartment_id: str,
    db_name: str,
    display_name: str,
    secret_id: str,
    tier: str,
    ecpu_count: str,
    storage_gbs: str,
    secret_version_number: str,
) -> dict[str, object]:
    if not DB_NAME_PATTERN.fullmatch(db_name):
        raise ValueError("Database name must start with a letter and contain at most 30 letters or numbers.")
    if not compartment_id.startswith("ocid1.compartment."):
        raise ValueError("Enter a compartment OCID.")
    if not secret_id.startswith("ocid1.vaultsecret."):
        raise ValueError("Enter an OCI Vault secret OCID for the ADMIN password.")
    if not re.fullmatch(r"[a-z0-9-]+", region):
        raise ValueError("Enter an OCI region identifier, for example us-chicago-1.")
    if profile not in {name for name, _ in oci_profiles()}:
        raise ValueError("Select an OCI profile configured on this computer.")
    if tier not in {"free", "developer", "paid"}:
        raise ValueError("Choose ATP Free, ATP Dev, or ATP Paid.")
    executable = shutil.which("oci")
    if not executable:
        raise RuntimeError("The OCI CLI is not installed or is not on this app's PATH.")

    command = [
        executable, "db", "autonomous-database", "create",
        "--profile", profile,
        "--region", region,
        "--compartment-id", compartment_id,
        "--db-name", db_name,
        "--display-name", display_name or db_name,
        "--db-workload", "OLTP",
        "--secret-id", secret_id,
        "--freeform-tags", A2A_FEATURE_TAG,
    ]
    validate_vault_secret(executable, profile, region, secret_id, secret_version_number)
    if secret_version_number:
        command.extend(["--secret-version-number", secret_version_number])
    if tier == "free":
        command.extend(["--is-free-tier", "true"])
    elif tier == "developer":
        command.extend([
            "--is-dev-tier", "true",
            "--compute-model", "ECPU",
            "--compute-count", "4",
            "--data-storage-size-in-gbs", "20",
            "--db-version", "26ai",
        ])
    else:
        try:
            ecpu = float(ecpu_count)
            storage = int(storage_gbs)
        except ValueError as exc:
            raise ValueError("For ATP Paid, enter ECPUs and whole-number storage in GB.") from exc
        if ecpu < 2 or storage < 20:
            raise ValueError("For ATP Paid, enter at least 2 ECPUs and 20 GB of storage.")
        command.extend([
            "--compute-model", "ECPU",
            "--compute-count", str(ecpu),
            "--data-storage-size-in-gbs", str(storage),
            "--db-version", "26ai",
        ])
    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=60, check=False)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("The OCI create request did not return within 60 seconds; check the OCI console before retrying.") from exc
    if result.returncode:
        raise RuntimeError(provisioning_error(result, secret_id))
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("OCI accepted the command but returned an unexpected response; check the OCI console.") from exc
    return payload.get("data", payload)


def delete_database(profile: str, region: str, database_ocid: str) -> dict[str, object]:
    if profile not in {name for name, _ in oci_profiles()}:
        raise ValueError("Select an OCI profile configured on this computer.")
    if not re.fullmatch(r"[a-z0-9-]+", region):
        raise ValueError("Enter an OCI region identifier, for example us-chicago-1.")
    if not database_ocid.startswith("ocid1.autonomousdatabase."):
        raise ValueError("Enter an Autonomous AI Database OCID.")
    executable = shutil.which("oci")
    if not executable:
        raise RuntimeError("The OCI CLI is not installed or is not on this app's PATH.")
    command = [
        executable, "db", "autonomous-database", "delete",
        "--profile", profile,
        "--region", region,
        "--autonomous-database-id", database_ocid,
        "--force",
    ]
    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=60, check=False)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("The OCI delete request did not return within 60 seconds; check the OCI console before retrying.") from exc
    if result.returncode:
        raise RuntimeError(provisioning_error(result, database_ocid))
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("OCI accepted the delete command but returned an unexpected response; check the OCI console.") from exc


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "GET":
        return render_workspace("start.html", "start")
    state = volatile_state()
    workspace = request.values.get("workspace", "infra")
    if workspace not in {"infra", "select-ai", "inferencing"}:
        workspace = "infra"
    if request.method == "POST":
        try:
            region = request.form["region"].strip()
            database_ocid = request.form["database_ocid"].strip()
            username = request.form["username"].strip()
            password = request.form["password"]
            if not all((region, database_ocid, username, password)):
                raise ValueError("Region, database OCID, database username, and password are required.")
            conn = Connection(region, database_ocid, username, password)
            token_response = requests.post(
                conn.token_url,
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                json={"grant_type": "password", "username": username, "password": password},
                timeout=30,
            )
            if not token_response.ok:
                raise RuntimeError(token_error(token_response))
            token = token_response.json().get("access_token")
            if not token:
                raise RuntimeError("Oracle returned no access_token.")
            agents_response = requests.get(conn.agents_url, headers=oracle_headers(token), timeout=30)
            if not agents_response.ok:
                raise RuntimeError(response_error(agents_response))
            agents = agents_response.json()
            if not isinstance(agents, list):
                raise RuntimeError("Oracle returned an unexpected agents response.")
            session["settings"] = {"profile": request.form.get("profile", "DEFAULT").strip() or "DEFAULT", "region": region, "database_ocid": database_ocid, "username": username}
            state.update(access_token=token, agents=agents)
            state.pop("selected_card", None)
            set_section_feedback(state, "token", f"Connection test passed. Found {len(agents)} published agent team(s).", "success")
        except (ValueError, RuntimeError, requests.RequestException) as exc:
            set_section_feedback(state, "token", str(exc), "error")
        return redirect(url_for("inferencing_page", _anchor="selection"))
    provision_settings = state.setdefault("provision_settings", load_last_settings())
    select_ai_settings = state.setdefault(
        "select_ai_settings",
        {key: provision_settings.get(key, "") for key in SELECT_AI_SETTING_KEYS},
    )
    admin_settings = state.setdefault(
        "admin_settings",
        {key: provision_settings.get(key, "") for key in ADMIN_SETTING_KEYS},
    )
    connection_settings = dict(session.get("settings", {}))
    connection_settings.setdefault("profile", provision_settings.get("provision_profile", ""))
    connection_settings.setdefault("region", provision_settings.get("provision_region", ""))
    if not connection_settings.get("database_ocid") and isinstance(state.get("provisioned_database"), dict):
        connection_settings["database_ocid"] = state["provisioned_database"].get("id", "")
    select_ai_settings.setdefault("oci_region", connection_settings.get("region", ""))
    wallet_settings = state.get("wallet_settings", {})
    wallet_database_ocid = str(wallet_settings.get("database_ocid") or connection_settings.get("database_ocid") or "")
    wallet_dir = state.get("wallet_dir")
    if not wallet_dir:
        existing_wallet = existing_wallet_directory(wallet_database_ocid)
        wallet_dir = str(existing_wallet) if existing_wallet else None
    setup_status = setup_status_items(
        state, connection_settings, provision_settings, select_ai_settings, admin_settings, wallet_dir
    )
    return render_template_string(
        PAGE,
        settings=connection_settings,
        agents=state.get("agents", []),
        selected_card=state.get("selected_card"),
        profiles=oci_profiles(),
        provisioned_database=state.get("provisioned_database"),
        delete_request=state.get("delete_request"),
        provision_settings=provision_settings,
        wallet_settings=wallet_settings,
        wallet_dir=wallet_dir,
        wallet_database_ocid=wallet_database_ocid,
        wallet_status=state.get("wallet_status"),
        admin_connection_ok=state.get("admin_connection_ok", False),
        admin_connection_summary=state.get("admin_connection_summary", ""),
        select_ai_checklist=state.get("select_ai_checklist", {}),
        select_ai_settings=select_ai_settings,
        select_ai_debug=state.get("select_ai_debug", []),
        select_ai_debug_enabled=SELECT_AI_DEBUG,
        select_ai_profile_attributes=state.get("select_ai_profile_attributes", {}),
        select_ai_profiles=state.get("select_ai_profiles", []),
        admin_settings=admin_settings,
        section_feedback=state.get("section_feedback", {}),
        setup_status=setup_status,
        workspace=workspace,
    )


@app.get("/setup")
def setup_page():
    return render_workspace("setup_overview.html", "setup")


@app.post("/setup/verify-demo")
def verify_demo_route():
    state = volatile_state()
    try:
        checks = verify_demo(state)
        passed = sum(bool(check["ok"]) for check in checks)
        category = "success" if state.get("demo_verified") else "error"
        set_section_feedback(state, "setup", f"Verify demo: {passed}/{len(checks)} checks passed. Review any item marked incomplete below.", category)
    except Exception as exc:
        state["demo_verified"] = False
        set_section_feedback(state, "setup", f"Verify demo failed: {str(exc)[:1_500]}", "error")
    return redirect(url_for("setup_page", _anchor="verify"))


@app.get("/setup/infra")
def setup_infra_page():
    return render_workspace("setup_infra.html", "setup-infra")


@app.get("/setup/infra/provision")
def infra_operations_page():
    return render_workspace("infra.html", f"setup-infra-{request.args.get('view', 'context')}")


@app.get("/setup/user")
def setup_user_page():
    return render_workspace("setup_user.html", "setup-user")


@app.get("/setup/readiness")
def setup_readiness_page():
    return render_workspace("setup_readiness.html", "setup-readiness")


@app.get("/test")
def test_page():
    return render_workspace("test_overview.html", "test")


@app.get("/test/token")
def test_token_page():
    return render_workspace("oauth_token.html", "test-token")


@app.get("/test/listener")
def test_listener_page():
    return render_workspace("oauth_listener.html", "test-listener")


@app.get("/test/team")
def test_team_page():
    return render_workspace("test_team.html", "test-team")


@app.get("/test/chat")
def test_chat_page():
    return render_workspace("test_chat.html", "test-chat")


@app.get("/select-ai")
def select_ai_page():
    return render_workspace("select_ai.html", "select-ai")


@app.get("/connect")
def connect_page():
    return render_workspace("connect.html", f"setup-connect-{request.args.get('view', 'status')}")


@app.get("/sample-data")
def sample_data_page():
    return render_workspace("sample_data.html", f"setup-user-{request.args.get('view', 'schema')}")


@app.get("/oauth-clients")
def oauth_clients_page():
    return render_workspace("oauth_clients.html", f"setup-oauth-{request.args.get('view', 'register')}")


@app.get("/oauth-token")
def oauth_token_page():
    return render_workspace("oauth_token.html", "test-token")


@app.post("/oauth-clients/register")
def oauth_client_register_route():
    state = volatile_state()
    try:
        redirect_uris = [value.strip() for value in request.form.get("redirect_uris", "").splitlines() if value.strip()]
        result = register_oauth_client(state, request.form.get("region", "").strip(), request.form.get("database_ocid", "").strip(), request.form.get("client_name", "").strip(), redirect_uris)
        settings = dict(session.get("settings", {})); settings.update({"region": request.form.get("region", "").strip(), "database_ocid": request.form.get("database_ocid", "").strip()}); session["settings"] = settings
        set_section_feedback(state, "oauth-clients", f"Registered OAuth client {result.get('client_name', '')}. Copy the one-time secret now.", "success")
    except (ValueError, RuntimeError, requests.RequestException) as exc:
        set_section_feedback(state, "oauth-clients", f"OAuth client registration failed: {str(exc)[:1_500]}", "error")
    return redirect(url_for("oauth_clients_page", view="register"))


@app.post("/oauth-clients/listener")
def oauth_listener_route():
    state = volatile_state()
    state["oauth_callback_state"] = secrets.token_urlsafe(32)
    state["oauth_callback_expires_at"] = time.time() + 600
    state.pop("oauth_authorization_code", None)
    state.pop("oauth_callback_received", None)
    if request.form.get("return_workspace") == "oauth-token":
        set_section_feedback(state, "oauth-token", "Local callback listener enabled for 10 minutes. Register the displayed loopback URI, then start authorization.", "success")
        return redirect(url_for("test_listener_page"))
    set_section_feedback(state, "oauth-clients", "Local callback listener enabled for 10 minutes. Register the displayed loopback URI, then start authorization from the external client.", "success")
    return redirect(url_for("oauth_clients_page", _anchor="listener"))


@app.post("/oauth-clients/password-grant")
def oauth_password_grant_route():
    state = volatile_state()
    try:
        agents = obtain_password_grant_token(state, request.form.get("region", "").strip(), request.form.get("database_ocid", "").strip(), request.form.get("username", "").strip(), request.form.get("password", ""))
        set_section_feedback(state, "oauth-clients", f"Password grant passed. Found {len(agents)} published team(s).", "success")
    except (ValueError, RuntimeError, requests.RequestException) as exc:
        set_section_feedback(state, "oauth-clients", f"Password grant failed: {str(exc)[:1_500]}", "error")
    return redirect(url_for("oauth_clients_page", _anchor="diagnostic"))


@app.post("/inferencing/oauth/discover")
def inferencing_oauth_discover_route():
    state = volatile_state()
    try:
        settings = oauth_database_context(state)
        region = str(settings.get("region", "")).strip()
        if not region:
            raise ValueError("Select and save an OCI region in Setup · Infra before preparing OAuth endpoints.")
        endpoints = external_oauth_endpoints(region)
        state["oauth_metadata"] = endpoints
        state["oauth_region"] = region
        set_section_feedback(state, "oauth-token", f"Prepared OAuth endpoints for {region}.", "success")
    except (ValueError, RuntimeError, requests.RequestException) as exc:
        set_section_feedback(state, "oauth-token", f"OAuth endpoint preparation failed: {str(exc)[:1_500]}", "error")
    return redirect(url_for("oauth_token_page", _anchor="token"))


@app.post("/inferencing/oauth/authorize")
def inferencing_oauth_authorize_route():
    state = volatile_state()
    try:
        endpoints = state.get("oauth_metadata")
        client_id = request.form.get("client_id", "").strip()
        redirect_uri = request.form.get("redirect_uri", "").strip()
        if not isinstance(endpoints, dict) or not client_id or redirect_uri != url_for("oauth_callback_route", _external=True):
            raise ValueError("Prepare OAuth endpoints, enter the client ID, and use the displayed local callback URI first.")
        state["oauth_callback_state"] = secrets.token_urlsafe(32)
        state["oauth_callback_expires_at"] = time.time() + 600
        state.pop("oauth_authorization_code", None)
        params = {"response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri, "scope": "openid", "state": state["oauth_callback_state"]}
        return redirect(f"{endpoints['authorization_endpoint']}?{urlencode(params)}")
    except ValueError as exc:
        set_section_feedback(state, "oauth-token", str(exc), "error")
        return redirect(url_for("oauth_token_page", _anchor="token"))


@app.post("/inferencing/oauth/exchange")
def inferencing_oauth_exchange_route():
    state = volatile_state()
    try:
        settings = oauth_database_context(state)
        region = str(state.get("oauth_region") or settings.get("region", "")).strip()
        exchange_external_oauth_code(state, region, request.form.get("client_id", "").strip(), request.form.get("client_secret", ""), request.form.get("redirect_uri", "").strip())
    except (ValueError, RuntimeError, requests.RequestException) as exc:
        set_section_feedback(state, "oauth-token", f"OAuth code exchange failed: {str(exc)[:1_500]}", "error")
        return redirect(url_for("oauth_token_page", _anchor="token"))
    try:
        agents = refresh_agents(state, settings)
        set_section_feedback(state, "oauth-token", f"External OAuth token acquired. Found {len(agents)} published team(s).", "success")
    except (ValueError, RuntimeError, requests.RequestException) as exc:
        set_section_feedback(state, "oauth-token", f"OAuth token acquired, but A2A team discovery failed: {str(exc)[:1_500]}", "error")
    return redirect(url_for("oauth_token_page", _anchor="token"))


@app.post("/oauth-token/print")
def oauth_token_print_route():
    """Print a token only to the local server console when explicit debug is on."""
    state = volatile_state()
    if not SELECT_AI_DEBUG:
        set_section_feedback(state, "oauth-token", "Token printing is available only when the app starts with A2A_DEMO_DEBUG=1 or --debug.", "error")
    elif not state.get("access_token"):
        set_section_feedback(state, "oauth-token", "No in-memory OAuth token is available to print.", "error")
    else:
        print(f"[OAuth debug token]\n{state['access_token']}", flush=True)
        set_section_feedback(state, "oauth-token", "Token printed to the local server console. Treat console output as a secret and clear it when finished.", "success")
    return redirect(url_for("oauth_token_page", _anchor="token"))


@app.post("/oauth-token/discover")
def oauth_token_discover_route():
    """Retry discovery without reusing or exchanging an authorization code."""
    state = volatile_state()
    try:
        agents = refresh_agents(state, oauth_database_context(state))
        set_section_feedback(state, "oauth-token", f"A2A discovery succeeded. Found {len(agents)} published team(s).", "success")
    except (ValueError, RuntimeError, requests.RequestException) as exc:
        set_section_feedback(state, "oauth-token", f"A2A team discovery failed: {str(exc)[:1_500]}", "error")
    return redirect(url_for("oauth_token_page", _anchor="token"))


@app.get("/oauth/callback")
def oauth_callback_route():
    state = volatile_state()
    expected_state = state.get("oauth_callback_state")
    valid = expected_state and request.args.get("state") == expected_state and time.time() <= float(state.get("oauth_callback_expires_at", 0))
    if not valid:
        set_section_feedback(state, "oauth-token", "Ignored an OAuth callback with missing, invalid, or expired state.", "error")
    elif request.args.get("error"):
        set_section_feedback(state, "oauth-token", f"OAuth authorization was declined: {request.args.get('error')}", "error")
    elif request.args.get("code"):
        state["oauth_authorization_code"] = request.args["code"]
        state["oauth_callback_received"] = True
        set_section_feedback(state, "oauth-token", "Authorization callback received. Exchange it for a token before it expires; the code remains in memory only.", "success")
    else:
        set_section_feedback(state, "oauth-token", "OAuth callback did not contain an authorization code.", "error")
    return redirect(url_for("oauth_token_page", _anchor="token"))


@app.post("/sample-data/select")
def sample_data_select_route():
    state = volatile_state()
    try:
        schema = checked_identifier(request.form.get("target_schema", ""), "Target schema")
        settings = load_last_settings(); settings["target_schema"] = schema; save_last_settings(settings)
        state["sample_settings"] = {"target_schema": schema}
        tables = sample_schema_readiness(state, schema)
        set_section_feedback(state, "sample-data", f"Selected {schema}; found {len(tables)} controlled demo table(s).", "success")
    except Exception as exc:
        set_section_feedback(state, "sample-data", f"Schema selection failed: {str(exc)[:1_500]}", "error")
    return redirect(url_for("sample_data_page", view="schema"))


@app.post("/sample-data/schema/create")
def sample_schema_create_route():
    state = volatile_state()
    try:
        if request.form.get("confirmation", "").strip() != "CREATE SCHEMA":
            raise ValueError("Type CREATE SCHEMA to create the target schema.")
        schema = request.form.get("target_schema", "")
        create_target_schema(state, schema, request.form.get("schema_password", ""))
        schema = checked_identifier(schema, "Target schema")
        settings = load_last_settings(); settings["target_schema"] = schema; save_last_settings(settings)
        state["sample_settings"] = {"target_schema": schema}
        set_section_feedback(state, "sample-data", f"Created target schema {schema}. Create the controlled sample data next.", "success")
    except Exception as exc:
        set_section_feedback(state, "sample-data", f"Schema creation failed: {str(exc)[:1_500]}", "error")
    return redirect(url_for("sample_data_page", view="schema"))


@app.post("/sample-data/privileges")
def target_select_ai_privileges_route():
    """Make the required target-user grants visible and safe to repeat."""
    state = volatile_state()
    try:
        schema = checked_identifier(request.form.get("target_schema", ""), "Target schema")
        grant_target_select_ai_privileges(state, schema)
        granted = target_select_ai_privilege_status(state, schema)
        state["target_select_ai_privileges"] = granted
        if len(granted) != 3:
            required = ("DBMS_CLOUD", "DBMS_CLOUD_AI", "DBMS_CLOUD_AI_AGENT")
            missing = [package for package in required if package not in granted]
            visible = ", ".join(granted) if granted else "none"
            raise RuntimeError(
                f"ADMIN executed the grants, but validation found direct EXECUTE on: {visible}; "
                f"missing: {', '.join(missing)}. Run the privilege-inspection SQL shown in the page help."
            )
        set_section_feedback(state, "sample-data", f"ADMIN verified idempotent EXECUTE grants for {schema}: " + ", ".join(granted) + ".", "success")
    except Exception as exc:
        set_section_feedback(state, "sample-data", f"Target-schema privilege grant failed: {str(exc)[:1_500]}", "error")
    return redirect(url_for("sample_data_page", view="privileges"))


@app.post("/sample-data/create")
def sample_data_create_route():
    state = volatile_state()
    try:
        if request.form.get("confirmation", "").strip() != "CREATE SAMPLE DATA":
            raise ValueError("Type CREATE SAMPLE DATA to create the controlled demo tables.")
        schema = checked_identifier(request.form.get("target_schema", ""), "Target schema")
        tables = create_sample_data(state, schema)
        settings = load_last_settings(); settings["target_schema"] = schema; save_last_settings(settings)
        state["sample_settings"] = {"target_schema": schema}
        set_section_feedback(state, "sample-data", f"Created controlled DEMO_SALES data in {schema}: " + ", ".join(f"{row['name']} ({row['rows']} rows)" for row in tables), "success")
    except Exception as exc:
        set_section_feedback(state, "sample-data", f"Sample-data creation failed: {str(exc)[:1_500]}", "error")
    return redirect(url_for("sample_data_page", view="data"))


@app.post("/sample-data/select-ai")
def target_select_ai_setup_route():
    """Create and test the profile in the schema that owns the sample agent."""
    state = volatile_state()
    try:
        form = {key: request.form.get(key, "").strip() for key in request.form}
        # Multipart forms should submit the selected provider radio value; OCI
        # is also the visible default when a browser omits that value.
        form["provider"] = str(form.get("provider") or "oci").lower()
        schema = checked_identifier(form.get("target_schema", ""), "Target schema")
        profile = checked_identifier(form.get("profile_name", ""), "Select AI profile")
        target_password = request_secret(state, "target_schema_password", f"target_schema_password:{schema}", "target-schema password")
        if form.get("provider") == "oci":
            form["oci_private_key"] = read_oci_private_key()
        checklist: dict[str, str] = {}
        grant_target_select_ai_privileges(state, schema)
        state["target_select_ai_privileges"] = target_select_ai_privilege_status(state, schema)
        checklist["database privileges"] = "ADMIN granted EXECUTE on DBMS_CLOUD, DBMS_CLOUD_AI, and DBMS_CLOUD_AI_AGENT"
        connection = target_schema_connection(state, schema, target_password)
        run_select_ai_setup(state, form, connection=connection, checklist=checklist, resource_principal_username=schema)
        # Use a separately opened session for the stateless call; setup closes
        # its connection after commit and must not be reused for the test.
        result = test_target_select_ai_profile(state, schema, target_password, profile)
        state["target_select_ai_ready"] = True
        state["target_select_ai_checklist"] = checklist
        state["sample_settings"] = {"target_schema": schema}
        credential_name = "OCI$RESOURCE_PRINCIPAL" if form.get("provider") == "oci_resource_principal" else form.get("credential_name", "")
        saved = load_last_settings(); saved.update({"target_schema": schema, "profile_name": profile, "credential_name": credential_name}); save_last_settings(saved)
        state["select_ai_settings"] = {**state.get("select_ai_settings", {}), **{key: form[key] for key in SELECT_AI_SETTING_KEYS if key in form}}
        set_section_feedback(state, "sample-data", f"Target-schema Select AI is ready in {schema}: profile {profile}; live test: {result[:200]}", "success")
    except Exception as exc:
        state["target_select_ai_ready"] = False
        select_ai_debug(state, "Target-schema Select AI setup error", error=str(exc)[:1_500])
        set_section_feedback(state, "sample-data", f"Target-schema Select AI setup failed: {str(exc)[:1_500]}", "error")
    return redirect(url_for("sample_data_page", view="select-ai"))


@app.post("/sample-data/select-ai/test")
def target_select_ai_test_route():
    state = volatile_state()
    try:
        schema = checked_identifier(request.form.get("target_schema", ""), "Target schema")
        profile = checked_identifier(request.form.get("profile_name", ""), "Profile name")
        password = request_secret(state, "target_schema_password", f"target_schema_password:{schema}", "target-schema password")
        result = test_target_select_ai_profile(state, schema, password, profile)
        state["target_select_ai_ready"] = True
        set_section_feedback(state, "sample-data", f"Target-schema Select AI test passed for {schema}.{profile}: {result[:200]}", "success")
    except Exception as exc:
        state["target_select_ai_ready"] = False
        set_section_feedback(state, "sample-data", f"Target-schema Select AI test failed: {str(exc)[:1_500]}", "error")
    return redirect(url_for("sample_data_page", view="select-ai"))


@app.post("/sample-data/select-ai/resources")
def target_select_ai_resources_route():
    state = volatile_state()
    try:
        schema = checked_identifier(request.form.get("target_schema", ""), "Target schema")
        password = request_secret(state, "target_schema_password", f"target_schema_password:{schema}", "target-schema password")
        profiles, credentials = list_target_select_ai_resources(state, schema, password)
        state["sample_settings"] = {"target_schema": schema}
        set_section_feedback(state, "sample-data", f"Found {len(profiles)} target-schema profile(s) and {len(credentials)} credential(s) in {schema}.", "success")
    except Exception as exc:
        set_section_feedback(state, "sample-data", f"Target-schema resource list failed: {str(exc)[:1_500]}", "error")
    return redirect(url_for("sample_data_page", view="select-ai"))


@app.post("/sample-data/select-ai/profile/delete")
def target_select_ai_profile_delete_route():
    state = volatile_state()
    try:
        if request.form.get("confirmation", "").strip() != "DELETE PROFILE":
            raise ValueError("Type DELETE PROFILE to remove the target-schema Select AI profile.")
        schema = checked_identifier(request.form.get("target_schema", ""), "Target schema")
        profile = checked_identifier(request.form.get("profile_name", ""), "Profile name")
        password = request_secret(state, "target_schema_password", f"target_schema_password:{schema}", "target-schema password")
        drop_target_select_ai_profile(state, schema, password, profile)
        list_target_select_ai_resources(state, schema, password)
        state["target_select_ai_ready"] = False
        set_section_feedback(state, "sample-data", f"Deleted target-schema profile {profile} from {schema}. Its credential was not deleted.", "success")
    except Exception as exc:
        set_section_feedback(state, "sample-data", f"Target-schema profile delete failed: {str(exc)[:1_500]}", "error")
    return redirect(url_for("sample_data_page", view="cleanup"))


@app.post("/sample-data/select-ai/credential/delete")
def target_select_ai_credential_delete_route():
    state = volatile_state()
    try:
        if request.form.get("confirmation", "").strip() != "DELETE CREDENTIAL":
            raise ValueError("Type DELETE CREDENTIAL to remove the target-schema provider credential.")
        schema = checked_identifier(request.form.get("target_schema", ""), "Target schema")
        credential = checked_identifier(request.form.get("credential_name", ""), "Credential name")
        password = request_secret(state, "target_schema_password", f"target_schema_password:{schema}", "target-schema password")
        drop_target_select_ai_credential(state, schema, password, credential)
        list_target_select_ai_resources(state, schema, password)
        state["target_select_ai_ready"] = False
        set_section_feedback(state, "sample-data", f"Deleted target-schema credential {credential} from {schema}.", "success")
    except Exception as exc:
        set_section_feedback(state, "sample-data", f"Target-schema credential delete failed: {str(exc)[:1_500]}", "error")
    return redirect(url_for("sample_data_page", view="cleanup"))


@app.get("/inferencing")
def inferencing_page():
    return redirect(url_for("test_team_page"))


@app.post("/profile/select")
def profile_select_route():
    state = volatile_state()
    try:
        profile = request.form.get("provision_profile", "").strip()
        region = request.form.get("provision_region", "").strip()
        if profile not in {name for name, _ in oci_profiles()}:
            raise ValueError("Select an OCI profile configured on this computer.")
        if not re.fullmatch(r"[a-z0-9-]+", region):
            raise ValueError("Enter a valid OCI region identifier.")
        existing = load_last_settings()
        existing.update({"provision_profile": profile, "provision_region": region})
        save_last_settings(existing)
        state["provision_settings"] = existing
        settings = dict(session.get("settings", {})); settings.update({"profile": profile, "region": region}); session["settings"] = settings
        set_section_feedback(state, "profile", f"Using OCI profile {profile} in {region}.", "success")
    except (ValueError, RuntimeError) as exc:
        set_section_feedback(state, "profile", str(exc), "error")
    return redirect(url_for("infra_operations_page", view="context"))


@app.post("/create-database")
def create_database_route():
    state = volatile_state()
    try:
        settings = {key: request.form.get(key, "").strip() for key in PROVISIONING_SETTING_KEYS}
        selected_context = load_last_settings()
        settings["provision_profile"] = str(selected_context.get("provision_profile", ""))
        settings["provision_region"] = str(selected_context.get("provision_region", ""))
        if not settings["provision_profile"] or not settings["provision_region"]:
            raise ValueError("Select and save the OCI profile and region in Setup · Infra before provisioning.")
        save_last_settings(settings)
        state["provision_settings"] = settings
        if request.form.get("confirmation", "").strip() != "CREATE":
            raise ValueError("Type CREATE to confirm that this action provisions a real database.")
        database = create_database(
            profile=settings["provision_profile"],
            region=settings["provision_region"],
            compartment_id=settings["compartment_id"],
            db_name=settings["db_name"],
            display_name=settings["display_name"],
            secret_id=settings["secret_id"],
            tier=settings["tier"],
            ecpu_count=settings["ecpu_count"],
            storage_gbs=settings["storage_gbs"],
            secret_version_number=settings["secret_version_number"],
        )
        state["provisioned_database"] = database
        database_ocid = database.get("id", "") if isinstance(database, dict) else ""
        if database_ocid:
            settings = session.get("settings", {})
            settings["database_ocid"] = database_ocid
            session["settings"] = settings
            save_last_settings({"database_ocid": database_ocid})
        set_section_feedback(state, "provision", "Provisioning request accepted. OCI is now creating the database; configure A2A only after it becomes Available.", "success")
    except (ValueError, RuntimeError) as exc:
        set_section_feedback(state, "provision", str(exc), "error")
    return redirect(url_for("infra_operations_page", view="provision"))


@app.post("/delete-database")
def delete_database_route():
    state = volatile_state()
    try:
        database_ocid = request.form.get("delete_database_ocid", "").strip()
        if request.form.get("delete_confirmation", "").strip() != "DELETE":
            raise ValueError("Type DELETE to permanently remove this database.")
        if request.form.get("delete_ocid_confirmation", "").strip() != database_ocid:
            raise ValueError("Re-enter the exact database OCID to confirm deletion.")
        selected_context = load_last_settings()
        profile = str(selected_context.get("provision_profile", ""))
        region = str(selected_context.get("provision_region", ""))
        if not profile or not region:
            raise ValueError("Select and save the OCI profile and region in Setup · Infra before deleting a database.")
        state["delete_request"] = delete_database(
            profile=profile,
            region=region,
            database_ocid=database_ocid,
        )
        set_section_feedback(state, "delete", "OCI accepted the delete request. Check the work request and console until deletion completes.", "success")
    except (ValueError, RuntimeError) as exc:
        set_section_feedback(state, "delete", str(exc), "error")
    return redirect(url_for("infra_operations_page", view="delete"))


@app.post("/agent-card")
def agent_card():
    state = volatile_state()
    token = state.get("access_token")
    card_url = request.form.get("card_url", "")
    parsed = urlparse(card_url)
    if not token or not card_url:
        set_section_feedback(state, "inferencing", "Connect and obtain an OAuth token before retrieving an Agent Card.", "error")
        flash("Connect and obtain a token before retrieving an Agent Card.", "error")
    elif parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith("oraclecloudapps.com"):
        set_section_feedback(state, "inferencing", "The Agent Card URL must be an Oracle HTTPS endpoint.", "error")
        flash("The Agent Card URL must be an Oracle HTTPS endpoint.", "error")
    else:
        try:
            response = requests.get(card_url, headers=oracle_headers(str(token)), timeout=30)
            if not response.ok:
                raise RuntimeError(response_error(response))
            state["selected_card"] = response.json()
            set_section_feedback(state, "inferencing", "Agent Card loaded.", "success")
            flash("Agent Card loaded.", "success")
        except (ValueError, RuntimeError, requests.RequestException) as exc:
            set_section_feedback(state, "inferencing", str(exc), "error")
            flash(str(exc), "error")
    return redirect(url_for("test_team_page"))


@app.post("/wallet/download")
def wallet_download_route():
    state = volatile_state()
    try:
        selected_context = load_last_settings()
        profile = str(selected_context.get("provision_profile", ""))
        region = str(selected_context.get("provision_region", ""))
        database_ocid = request.form.get("wallet_database_ocid", "").strip()
        if not profile or not region:
            raise ValueError("Select and save the OCI profile and region in Setup · Infra before downloading a wallet.")
        lifecycle = database_lifecycle(profile, region, database_ocid)
        state["wallet_status"] = lifecycle
        if lifecycle != "AVAILABLE":
            raise ValueError(f"Database status is {lifecycle}. Wait until it is AVAILABLE before generating a wallet; no wallet request was started.")
        wallet_password = request_secret(state, "wallet_password", "admin_wallet_password", "wallet password")
        wallet_dir = download_wallet(
            profile, region, database_ocid,
            wallet_password,
        )
        state["wallet_dir"] = str(wallet_dir)
        state["wallet_settings"] = {
            "profile": profile,
            "region": region,
            "database_ocid": database_ocid,
        }
        save_last_settings({"database_ocid": database_ocid})
        set_section_feedback(state, "wallet", f"Wallet downloaded and extracted locally to {wallet_dir.relative_to(Path.cwd())}.", "success")
    except (ValueError, RuntimeError) as exc:
        set_section_feedback(state, "wallet", str(exc), "error")
    return redirect(url_for("connect_page", view="wallet"))


@app.post("/wallet/status")
def wallet_status_route():
    state = volatile_state()
    try:
        selected_context = load_last_settings()
        profile = str(selected_context.get("provision_profile", ""))
        region = str(selected_context.get("provision_region", ""))
        database_ocid = request.form.get("wallet_database_ocid", "").strip()
        if not profile or not region:
            raise ValueError("Select and save the OCI profile and region in Setup · Infra before checking database status.")
        lifecycle = database_lifecycle(
            profile, region, database_ocid,
        )
        state["wallet_status"] = lifecycle
        save_last_settings({"database_ocid": database_ocid})
        set_section_feedback(state, "wallet", f"Database lifecycle state: {lifecycle}.", "success" if lifecycle == "AVAILABLE" else "error")
    except (ValueError, RuntimeError) as exc:
        set_section_feedback(state, "wallet", str(exc), "error")
    return redirect(url_for("connect_page", view="status"))


@app.post("/admin/test")
def admin_test_route():
    state = volatile_state()
    try:
        wallet_dir_value = state.get("wallet_dir")
        wallet_dir = Path(str(wallet_dir_value)) if wallet_dir_value else None
        if not wallet_dir or not (wallet_dir / "tnsnames.ora").is_file():
            wallet_dir = existing_wallet_directory(request.form.get("admin_database_ocid", "").strip()) or discovered_wallet_directory()
        if not wallet_dir:
            raise ValueError("No extracted wallet was found for this database. Download it first, or select the matching database OCID.")
        service_name = request.form.get("service_name", "").strip()
        if not SQL_IDENTIFIER_PATTERN.fullmatch(service_name):
            raise ValueError("Service name must be a wallet service alias such as MYDB_high.")
        username = request.form.get("admin_username", "ADMIN").strip()
        password = request_secret(state, "admin_password", "admin_password", "ADMIN password")
        wallet_password = request_secret(state, "admin_wallet_password", "admin_wallet_password", "wallet password")
        if not username:
            raise ValueError("Enter the ADMIN username.")
        admin_settings = {key: request.form.get(key, "").strip() for key in ADMIN_SETTING_KEYS}
        save_last_settings(admin_settings)
        state["admin_settings"] = admin_settings
        state["admin_connection"] = {
            "username": username, "password": password, "service_name": service_name,
            "wallet_dir": str(wallet_dir), "wallet_password": wallet_password,
        }
        with database_connection(state) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT SYS_CONTEXT('USERENV', 'CURRENT_USER'), BANNER_FULL FROM V$VERSION FETCH FIRST 1 ROW ONLY")
                row = cursor.fetchone()
        state["admin_connection_ok"] = True
        state["admin_connection_summary"] = str(row[0]) if row else username
        set_section_feedback(state, "wallet", f"ADMIN wallet connection passed as {state['admin_connection_summary']}.", "success")
    except Exception as exc:  # Driver exceptions vary by installed Oracle client.
        state.pop("admin_connection_ok", None)
        set_section_feedback(state, "wallet", f"ADMIN connection failed: {str(exc)[:1_500]}", "error")
    return redirect(url_for("connect_page", view="admin"))


@app.post("/select-ai/setup")
def select_ai_setup_route():
    state = volatile_state()
    try:
        form = {key: request.form.get(key, "").strip() for key in request.form}
        saved_settings = {key: form[key] for key in SELECT_AI_SETTING_KEYS if key in form}
        save_last_settings(saved_settings)
        state["select_ai_settings"] = saved_settings
        if form.get("provider") == "oci":
            form["oci_private_key"] = read_oci_private_key()
        run_select_ai_setup(state, form)
        set_section_feedback(state, "select-ai", "Select AI credential and profile were created. Continue to Agents to create and publish a team.", "success")
    except Exception as exc:  # Preserve Oracle's useful database diagnostic without writing credentials.
        select_ai_debug(state, "Select AI setup error", error=str(exc)[:1_500])
        set_section_feedback(state, "select-ai", f"Select AI setup failed: {str(exc)[:1_500]}", "error")
    return redirect(url_for("select_ai_page", _anchor="credential"))


@app.post("/select-ai/test")
def select_ai_test_route():
    state = volatile_state()
    try:
        result = test_select_ai_profile(state, request.form.get("profile_name", "").strip())
        set_section_feedback(state, "select-ai", f"Live Select AI test passed: {result[:300]}", "success")
    except Exception as exc:
        select_ai_debug(state, "Select AI live-test error", error=str(exc)[:1_500])
        set_section_feedback(state, "select-ai", f"Live Select AI test failed: {str(exc)[:1_500]}", "error")
    target_workspace = request.form.get("return_workspace", "select-ai")
    if target_workspace not in {"select-ai", "inferencing"}:
        target_workspace = "select-ai"
    return redirect(url_for("inferencing_page" if target_workspace == "inferencing" else "select_ai_page", _anchor="chat" if target_workspace == "inferencing" else "profile"))


@app.post("/select-ai/inspect")
def select_ai_inspect_route():
    state = volatile_state()
    try:
        attributes = inspect_select_ai_profile(state, request.form.get("profile_name", "").strip())
        set_section_feedback(state, "select-ai", f"Read {len(attributes)} stored Select AI profile attribute(s).", "success")
    except Exception as exc:
        select_ai_debug(state, "Select AI profile inspection error", error=str(exc)[:1_500])
        set_section_feedback(state, "select-ai", f"Profile inspection failed: {str(exc)[:1_500]}", "error")
    return redirect(url_for("select_ai_page", _anchor="profile"))


@app.post("/select-ai/profiles")
def select_ai_profiles_route():
    state = volatile_state()
    try:
        profiles = list_select_ai_profiles(state)
        set_section_feedback(state, "select-ai", f"Found {len(profiles)} Select AI profile(s) in the connected schema.", "success")
    except Exception as exc:
        set_section_feedback(state, "select-ai", f"Profile listing failed: {str(exc)[:1_500]}", "error")
    return redirect(url_for("select_ai_page", _anchor="delete-profile"))


@app.post("/select-ai/profile/delete")
def select_ai_profile_delete_route():
    state = volatile_state()
    try:
        if request.form.get("confirmation", "").strip() != "DELETE":
            raise ValueError("Type DELETE to remove this Select AI profile.")
        profile_name = request.form.get("profile_name", "").strip()
        delete_select_ai_profile(state, profile_name)
        list_select_ai_profiles(state)
        set_section_feedback(state, "select-ai", f"Deleted Select AI profile {profile_name}. Its DBMS_CLOUD credential was not deleted.", "success")
    except Exception as exc:
        set_section_feedback(state, "select-ai", f"Profile delete failed: {str(exc)[:1_500]}", "error")
    return redirect(url_for("select_ai_page", _anchor="profile"))


@app.post("/select-ai/credentials")
def select_ai_credentials_route():
    state = volatile_state()
    try:
        credentials = list_select_ai_credentials(state)
        set_section_feedback(state, "select-ai", f"Found {len(credentials)} credential(s) in the connected schema.", "success")
    except Exception as exc:
        set_section_feedback(state, "select-ai", f"Credential listing failed: {str(exc)[:1_500]}", "error")
    return redirect(url_for("select_ai_page", _anchor="credentials"))


@app.post("/select-ai/credential/inspect")
def select_ai_credential_inspect_route():
    state = volatile_state()
    try:
        details = inspect_select_ai_credential(state, request.form.get("credential_name", ""))
        set_section_feedback(state, "select-ai", f"Read metadata for credential {details['credential_name']}.", "success")
    except Exception as exc:
        set_section_feedback(state, "select-ai", f"Credential inspection failed: {str(exc)[:1_500]}", "error")
    return redirect(url_for("select_ai_page", _anchor="credentials"))


@app.post("/select-ai/credential/delete")
def select_ai_credential_delete_route():
    state = volatile_state()
    try:
        if request.form.get("confirmation", "").strip() != "DELETE":
            raise ValueError("Type DELETE to remove this DBMS_CLOUD credential.")
        name = request.form.get("credential_name", "")
        delete_select_ai_credential(state, name)
        list_select_ai_credentials(state)
        set_section_feedback(state, "select-ai", f"Deleted credential {name}. Profiles that reference it can no longer run.", "success")
    except Exception as exc:
        set_section_feedback(state, "select-ai", f"Credential delete failed: {str(exc)[:1_500]}", "error")
    return redirect(url_for("select_ai_page", _anchor="credentials"))


@app.post("/select-ai/routing")
def select_ai_routing_route():
    state = volatile_state()
    try:
        form = {key: request.form.get(key, "").strip() for key in request.form}
        saved_settings = {key: form[key] for key in SELECT_AI_SETTING_KEYS if key in form}
        save_last_settings(saved_settings)
        state["select_ai_settings"] = {**state.get("select_ai_settings", {}), **saved_settings}
        update_oci_profile_routing(state, form["profile_name"], form["oci_region"], form["oci_compartment_id"], form.get("oci_model", ""))
        set_section_feedback(state, "select-ai", "Updated the OCI Generative AI region, compartment, and supplied model on the existing profile. Run the live test again.", "success")
    except Exception as exc:
        set_section_feedback(state, "select-ai", f"Profile routing update failed: {str(exc)[:1_500]}", "error")
    return redirect(url_for("select_ai_page", _anchor="profile"))


@app.get("/agents")
def agents_page():
    return render_workspace("agents.html", f"setup-agents-{request.args.get('view', 'readiness')}")


@app.post("/agents/refresh")
def agents_refresh():
    state = volatile_state()
    try:
        agents = refresh_agents(state, session.get("settings", {}))
        set_section_feedback(state, "agents", f"Refreshed published teams. Found {len(agents)}.", "success")
        flash(f"Refreshed published teams. Found {len(agents)}.", "success")
    except (ValueError, RuntimeError, requests.RequestException) as exc:
        set_section_feedback(state, "agents", str(exc), "error")
        flash(str(exc), "error")
    return redirect(url_for("agents_page", view="refresh"))


@app.post("/agents/team")
def team_action_route():
    state = volatile_state()
    try:
        action = request.form.get("action", "")
        run_team_action(state, action, request.form.get("team_name", "").strip(), {key: request.form.get(key, "").strip() for key in request.form})
        set_section_feedback(state, "agents", f"Team {action} completed. Refresh discovery when ready.", "success")
        flash(f"Team {action} completed. Refresh discovery after obtaining a token if you need the A2A view.", "success")
    except Exception as exc:
        set_section_feedback(state, "agents", f"Team action failed: {str(exc)[:1_500]}", "error")
        flash(f"Team action failed: {str(exc)[:1_500]}", "error")
    return redirect(url_for("agents_page", view="delete" if request.form.get("action") == "delete" else "install"))


@app.post("/agents/readiness")
def agents_readiness_route():
    state = volatile_state()
    try:
        sample = selected_sample(request.form.get("sample_name", ""))
        schema = checked_identifier(request.form.get("target_schema", ""), "Target schema")
        profile = checked_identifier(request.form.get("profile_name", ""), "Select AI profile")
        profile_status = target_select_ai_profile_status(state, schema, profile)
        if profile_status != "ENABLED":
            raise ValueError(f"Select AI profile {profile} is not enabled in target schema {schema}. Create it in Data · Sample Schema; an ADMIN-owned profile cannot run this agent.")
        tables = sample_schema_readiness(state, schema) if sample.get("requires_data") else []
        state["sample_settings"] = {"target_schema": schema}
        stored = load_last_settings(); stored.update({"target_schema": schema, "profile_name": profile}); save_last_settings(stored)
        state["select_ai_settings"] = {**state.get("select_ai_settings", {}), "profile_name": profile}
        state["agent_readiness_ok"] = not sample.get("requires_data") or bool(state.get("sample_data_ready"))
        data_detail = (", ".join(f"{row['name']} ({row['rows']} rows)" for row in tables) if tables else "no controlled data dependency")
        set_section_feedback(state, "agents", f"Readiness for {sample['title']}: {schema} has {data_detail}; profile {profile} is enabled in the target schema.", "success" if state.get("agent_readiness_ok") else "error")
    except Exception as exc:
        state["agent_readiness_ok"] = False
        set_section_feedback(state, "agents", f"Readiness check failed: {str(exc)[:1_500]}", "error")
    return redirect(url_for("agents_page", view="readiness"))


@app.post("/agents/install")
def agents_install_route():
    state = volatile_state()
    try:
        if request.form.get("confirmation", "").strip() != "INSTALL SAMPLE":
            raise ValueError("Type INSTALL SAMPLE to deploy or refresh the selected sample package.")
        sample_name = request.form.get("sample_name", "")
        schema = checked_identifier(request.form.get("target_schema", ""), "Target schema")
        profile = checked_identifier(request.form.get("profile_name", ""), "Select AI profile")
        form = {key: request.form.get(key, "").strip() for key in request.form}
        if sample_name == "database-provisioning":
            form["target_schema_password"] = request_secret(state, "target_schema_password", f"target_schema_password:{schema}", "target-schema password")
        team_name = deploy_sample(state, sample_name, schema, profile, form)
        state["sample_agent_installed"] = True
        set_section_feedback(state, "agents", f"Installed {sample_name} in {schema}; published team {team_name}. Refresh A2A discovery next.", "success")
    except Exception as exc:
        set_section_feedback(state, "agents", f"Sample agent installation failed: {str(exc)[:1_500]}", "error")
    return redirect(url_for("agents_page", view="install"))


@app.post("/inferencing/team")
def inferencing_team_route():
    state = volatile_state()
    try:
        load_team_card(state, request.form.get("team_name", ""))
        state["chat_history"] = []
        for key in ("a2a_context_id", "pending_a2a_task_id", "last_a2a_task_id", "last_a2a_task_state", "last_a2a_task_diagnostic"):
            state.pop(key, None)
        set_section_feedback(state, "inferencing", f"Selected {state['selected_team']} and loaded its Agent Card.", "success")
    except (ValueError, RuntimeError, requests.RequestException) as exc:
        set_section_feedback(state, "inferencing", str(exc), "error")
    return redirect(url_for("test_team_page"))


@app.post("/inferencing/chat")
def inferencing_chat_route():
    state = volatile_state()
    try:
        send_a2a_message(state, request.form.get("message", ""))
        task_state = normalized_a2a_task_state(state.get("last_a2a_task_state", ""))
        if task_state == "input-required":
            set_section_feedback(state, "inferencing", "A2A task requires input. Reply YES or NO (or provide corrections) in the same chat to resume it.", "success")
        elif task_state == "auth-required":
            set_section_feedback(state, "inferencing", "A2A task requires additional authorization input.", "success")
        elif state.get("pending_a2a_task_id"):
            set_section_feedback(state, "inferencing", "A2A task is still running. Check task status before sending another message.", "success")
        else:
            set_section_feedback(state, "inferencing", "A2A message completed.", "success")
    except (ValueError, RuntimeError, requests.RequestException) as exc:
        set_section_feedback(state, "inferencing", f"A2A chat failed: {str(exc)[:1_500]}", "error")
    return redirect(url_for("test_chat_page"))


@app.post("/inferencing/task-status")
def inferencing_task_status_route():
    state = volatile_state()
    try:
        poll_a2a_task(state)
        set_section_feedback(state, "inferencing", "A2A task status retrieved.", "success")
    except (ValueError, RuntimeError, requests.RequestException) as exc:
        set_section_feedback(state, "inferencing", f"A2A task-status check failed: {str(exc)[:1_500]}", "error")
    return redirect(url_for("test_chat_page"))


@app.post("/inferencing/chat/reset")
def inferencing_chat_reset_route():
    """Forget local A2A conversation state; it does not cancel a server task."""
    state = volatile_state()
    for key in ("chat_history", "a2a_context_id", "pending_a2a_task_id", "last_a2a_task_id", "last_a2a_task_state", "last_a2a_task_diagnostic"):
        state.pop(key, None)
    set_section_feedback(state, "inferencing", "Started a new local conversation. Any prior server task was not canceled.", "success")
    return redirect(url_for("test_chat_page"))


@app.get("/cards")
def cards():
    return redirect(url_for("inferencing_page", _anchor="chat"))


@app.post("/clear")
def clear():
    session_id = session.get("volatile_session_id")
    if session_id:
        volatile_sessions.pop(session_id, None)
    session.clear()
    flash("Local session cleared.", "success")
    return redirect(url_for("index"))


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Oracle A2A Test Console</title><style>
:root{color-scheme:dark;--bg:#09111f;--panel:#101c30;--line:#253652;--text:#edf3ff;--muted:#a9b9d3;--blue:#73a7ff;--good:#66d19e;--bad:#ff8989;--warn:#ffcf72}*{box-sizing:border-box}body{margin:0;background:linear-gradient(135deg,#07101d,#0d1830);color:var(--text);font:15px/1.5 system-ui,sans-serif}main{max-width:1120px;margin:auto;padding:48px 24px 72px}h1{margin:0;font-size:30px}h2{font-size:18px;margin:0 0 16px}.lede{color:var(--muted);max-width:760px;margin:8px 0 30px}.steps{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 20px}.steps a{color:var(--blue);text-decoration:none;border:1px solid var(--line);border-radius:999px;padding:6px 10px;font-size:13px}.grid{display:grid;grid-template-columns:1.1fr .9fr;gap:20px}.panel{background:#101c30;border:1px solid var(--line);border-radius:14px;padding:22px;box-shadow:0 12px 38px #0003}label{display:block;color:var(--muted);font-size:13px;margin:0 0 5px}input,select{width:100%;padding:10px 11px;background:#0a1427;border:1px solid #324563;border-radius:8px;color:var(--text);font:inherit}.fields{display:grid;grid-template-columns:1fr 1fr;gap:13px}.wide{grid-column:1/-1}.checks{display:flex;gap:18px;flex-wrap:wrap}.checks label{display:flex;align-items:center;gap:7px;color:var(--text);font-size:14px}.checks input{width:auto}#paid_options{display:grid;grid-template-columns:1fr 1fr;gap:13px}button{cursor:pointer;border:0;border-radius:8px;padding:10px 14px;background:var(--blue);color:#081224;font-weight:700;font:inherit}.secondary{background:transparent;color:var(--muted);border:1px solid var(--line);margin-left:8px}.danger{background:#dc7c76}.flash{padding:12px 14px;border-radius:9px;margin:0 0 18px}.success{color:var(--good);background:#123525}.error{color:var(--bad);background:#3a1c25;white-space:pre-wrap;overflow-wrap:anywhere}.notice{border-left:3px solid var(--warn);padding-left:12px;color:var(--muted)}.agent{border-top:1px solid var(--line);padding:14px 0}.agent:first-of-type{border-top:0;padding-top:0}.agent p,.hint{color:var(--muted)}.hint{font-size:12px}.agent p{margin:4px 0 10px}.agent code{display:block;color:#c7d8f7;font-size:12px;overflow-wrap:anywhere;margin:8px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:550px;overflow:auto;background:#07101f;border:1px solid var(--line);padding:16px;border-radius:9px;color:#d9e7ff;font-size:12px}.card{margin-top:20px}@media(max-width:780px){.grid,.fields,#paid_options{grid-template-columns:1fr}.wide{grid-column:auto}}</style></head>
<body><main><h1>Oracle A2A Test Console</h1><p class="lede">Provision a test Autonomous AI Database locally, then validate its Select AI A2A agent teams and generated Agent Cards.</p>
{% with messages=get_flashed_messages(with_categories=true) %}{% for category,message in messages %}<div class="flash {{category}}">{{message}}</div>{% endfor %}{% endwith %}<nav class="steps"><a href="#provision">1. Provision</a><a href="#wallet">2. Wallet &amp; admin connection</a><a href="#select-ai">3. Select AI &amp; team</a><a href="#token">4. Token &amp; Agent Card</a><a href="#delete">5. Delete</a></nav>
<div id="provision" class="grid"><section class="panel"><h2>Create ATP database</h2><p class="notice">This submits a real OCI provisioning request and adds the documented A2A feature tag. It does not create agent teams, database users, or OAuth clients.</p><form method="post" action="{{url_for('create_database_route')}}"><div class="fields"><div><label>OCI CLI profile</label><select id="provision_profile" name="provision_profile" required><option value="">Choose a local profile</option>{% for name,region in profiles %}<option value="{{name}}" data-region="{{region}}"{% if provision_settings.get('provision_profile') == name %} selected{% endif %}>{{name}}{% if region %} ({{region}}){% endif %}</option>{% endfor %}</select></div><div><label>OCI region</label><input id="provision_region" name="provision_region" value="{{provision_settings.get('provision_region','')}}" placeholder="us-chicago-1" required></div><div><label>Compartment OCID</label><input name="compartment_id" value="{{provision_settings.get('compartment_id','')}}" placeholder="ocid1.compartment.oc1..." required></div><div><label>Database name</label><input name="db_name" value="{{provision_settings.get('db_name','')}}" placeholder="a2ademo" maxlength="30" required><p class="hint">Letters and numbers only; starts with a letter.</p></div><div><label>Display name</label><input name="display_name" value="{{provision_settings.get('display_name','')}}" placeholder="A2A demo database"></div><div><label>Vault secret OCID (ADMIN password)</label><input name="secret_id" value="{{provision_settings.get('secret_id','')}}" placeholder="ocid1.vaultsecret.oc1..." required></div><div><label>Secret version (optional)</label><input name="secret_version_number" value="{{provision_settings.get('secret_version_number','')}}" type="number" min="1" step="1" placeholder="CURRENT"><p class="hint">The app verifies this secret in the selected OCI region before creating the database.</p></div><div class="wide"><label>ATP tier</label><div class="checks"><label><input name="tier" type="radio" value="free"{% if provision_settings.get('tier','free') == 'free' %} checked{% endif %}> ATP Free</label><label><input name="tier" type="radio" value="developer"{% if provision_settings.get('tier') == 'developer' %} checked{% endif %}> ATP Dev</label><label><input name="tier" type="radio" value="paid"{% if provision_settings.get('tier') == 'paid' %} checked{% endif %}> ATP Paid</label></div><p class="hint">ATP Dev provisions its fixed shape: 4 ECPUs and 20 GB. ATP Free uses OCI's fixed Free tier settings. A2A is enabled through the <code>adb$feature</code> free-form tag.</p></div><div id="paid_options" class="wide"><div><label>ECPU count</label><input name="ecpu_count" type="number" min="2" step="1" value="{{provision_settings.get('ecpu_count','2')}}"></div><div><label>Storage in GB</label><input name="storage_gbs" type="number" min="20" step="1" value="{{provision_settings.get('storage_gbs','20')}}"></div><p class="hint">Only ATP Paid uses these values. It requests 26ai for the current A2A capability.</p></div><div><label>Type CREATE to confirm</label><input name="confirmation" autocomplete="off" required></div></div><p><button class="danger">Start OCI provisioning</button></p></form>{% if provisioned_database %}<h3>Last submitted database</h3><pre>{{provisioned_database|tojson(indent=2)}}</pre>{% endif %}</section>
<section class="panel"><h2>Readiness checklist</h2><p class="hint">Before creating: use a profile authorized to manage Autonomous Databases in the target compartment, confirm Always Free capacity or billing choice, and create the Vault secret.</p><p class="hint">After OCI reports <strong>Available</strong>: create the database user, enable/configure Select AI and the A2A server, publish an agent team, then use the connection form to retrieve its Agent Card.</p><p class="hint">The OCI profile is used only for provisioning. The test connection uses the database OCID, region, and database credentials below.</p></section></div>
<section id="wallet" class="panel card"><h2>2. Wallet &amp; ADMIN connection</h2><p class="notice">The wallet is generated through your selected OCI profile and extracted under this project's ignored <code>wallet/</code> directory. Its password is used only for this request and is never saved.</p><form method="post" action="{{url_for('wallet_download_route')}}"><div class="fields"><div><label>OCI CLI profile</label><select name="wallet_profile" required><option value="">Choose a local profile</option>{% for name,profile_region in profiles %}<option value="{{name}}"{% if wallet_settings.get('profile',settings.get('profile')) == name %} selected{% endif %}>{{name}}{% if profile_region %} ({{profile_region}}){% endif %}</option>{% endfor %}</select></div><div><label>OCI region</label><input name="wallet_region" value="{{wallet_settings.get('region',settings.get('region',''))}}" required></div><div class="wide"><label>Database OCID</label><input name="wallet_database_ocid" value="{{wallet_settings.get('database_ocid',settings.get('database_ocid',''))}}" required></div><div><label>New wallet password</label><input name="wallet_password" type="password" required autocomplete="new-password"></div></div><p><button>Download wallet locally</button></p></form>{% if wallet_dir %}<p class="hint">Downloaded wallet: <code>{{wallet_dir}}</code></p><form method="post" action="{{url_for('admin_test_route')}}"><div class="fields"><div><label>Wallet service alias</label><input name="service_name" placeholder="mydb_high" required></div><div><label>Database user</label><input name="admin_username" value="ADMIN" required></div><div><label>ADMIN password</label><input name="admin_password" type="password" required autocomplete="current-password"></div><div><label>Wallet password</label><input name="admin_wallet_password" type="password" required autocomplete="current-password"></div></div><p><button>Test ADMIN connection</button></p></form>{% endif %}{% if admin_connection_ok %}<p class="success">ADMIN connection ready: {{admin_connection_summary}}</p>{% endif %}</section>
<section id="select-ai" class="panel card"><h2>3. Select AI setup</h2><p class="hint">Only OCI Generative AI and OpenAI are available in this guided flow. Other Select AI providers are intentionally not supported here. Run this as the connected ADMIN user or a schema with the required DBMS_CLOUD and DBMS_CLOUD_AI privileges.</p><form method="post" action="{{url_for('select_ai_setup_route')}}"><div class="fields"><div class="wide"><label>Provider</label><div class="checks"><label><input type="radio" name="provider" value="oci" checked> OCI Generative AI</label><label><input type="radio" name="provider" value="openai"> OpenAI</label></div></div><div><label>Credential name</label><input name="credential_name" value="GENAI_CRED" required></div><div><label>AI profile name</label><input name="profile_name" value="A2A_PROFILE" required></div><div class="wide provider-oci"><label>OCI compartment OCID</label><input name="oci_compartment_id" placeholder="ocid1.compartment.oc1..."></div><div class="provider-oci"><label>OCI user OCID</label><input name="oci_user_ocid" placeholder="ocid1.user.oc1..."></div><div class="provider-oci"><label>OCI tenancy OCID</label><input name="oci_tenancy_ocid" placeholder="ocid1.tenancy.oc1..."></div><div class="provider-oci"><label>API signing-key fingerprint</label><input name="oci_fingerprint"></div><div class="provider-oci"><label>Model (optional)</label><input name="oci_model" placeholder="cohere.command-r-plus"></div><div class="wide provider-oci"><label>OCI API signing private key</label><input name="oci_private_key" type="password" autocomplete="off"></div><div class="wide provider-openai" hidden><label>OpenAI API key</label><input name="openai_api_key" type="password" autocomplete="off"></div></div><p><button{% if not admin_connection_ok %} disabled title="Test the ADMIN connection first"{% endif %}>Create credential and profile</button></p></form>{% if select_ai_checklist %}<h3>Setup checklist</h3><ul>{% for item,status in select_ai_checklist.items() %}<li><strong>{{item}}:</strong> {{status}}</li>{% endfor %}</ul>{% endif %}<p><a class="steps" href="{{url_for('agents_page')}}">Continue to the Agents page &rarr;</a></p></section>
<div id="token" class="grid card"><section class="panel"><h2>4. Connection setup</h2><form method="post"><div class="fields"><div><label>OCI CLI profile</label><select id="connection_profile" name="profile"><option value="">Choose a local profile</option>{% for name,profile_region in profiles %}<option value="{{name}}" data-region="{{profile_region}}"{% if settings.get('profile') == name %} selected{% endif %}>{{name}}{% if profile_region %} ({{profile_region}}){% endif %}</option>{% endfor %}</select><p class="hint">Used to default the region; token authentication uses database credentials.</p></div><div><label>OCI region identifier</label><input id="connection_region" name="region" value="{{settings.get('region','')}}" placeholder="us-chicago-1" required></div><div class="wide"><label>Autonomous Database OCID</label><input name="database_ocid" value="{{settings.get('database_ocid','')}}" placeholder="ocid1.autonomousdatabase.oc1..." required></div><div><label>Database username</label><input name="username" value="{{settings.get('username','')}}" required></div><div><label>Database password</label><input name="password" type="password" required autocomplete="current-password"></div></div><p class="hint">Passwords are used only to request a token and are never written to disk.</p><p><button>Test connection &amp; obtain token</button><button class="secondary" formaction="{{url_for('clear')}}" formmethod="post">Clear session</button></p></form></section><section class="panel"><h2>Published agents</h2>{% if agents %}{% for agent in agents %}<div class="agent"><strong>{{agent.get('name','Unnamed agent')}}</strong><p>{{agent.get('description','No description')}}</p><code>{{agent.get('agent_card_url','')}}</code><form method="post" action="{{url_for('agent_card')}}"><input type="hidden" name="card_url" value="{{agent.get('agent_card_url','')}}"><button>Load Agent Card</button></form></div>{% endfor %}{% else %}<p class="lede">Run the connection test after publishing an agent team.</p>{% endif %}</section></div>{% if selected_card %}<section class="panel card"><h2>Agent Card</h2><pre>{{selected_card|tojson(indent=2)}}</pre></section>{% endif %}<section id="delete" class="panel card"><h2>5. Delete database</h2><p class="notice">This permanently deletes the selected Autonomous AI Database. This action cannot be undone.</p><form method="post" action="{{url_for('delete_database_route')}}"><div class="fields"><div><label>OCI CLI profile</label><select name="delete_profile" required><option value="">Choose a local profile</option>{% for name,profile_region in profiles %}<option value="{{name}}"{% if settings.get('profile') == name %} selected{% endif %}>{{name}}{% if profile_region %} ({{profile_region}}){% endif %}</option>{% endfor %}</select></div><div><label>OCI region</label><input name="delete_region" value="{{settings.get('region','')}}" placeholder="us-chicago-1" required></div><div class="wide"><label>Database OCID to delete</label><input name="delete_database_ocid" value="{{settings.get('database_ocid','')}}" placeholder="ocid1.autonomousdatabase.oc1..." required></div><div class="wide"><label>Re-enter the exact Database OCID</label><input name="delete_ocid_confirmation" required autocomplete="off"></div><div><label>Type DELETE to confirm</label><input name="delete_confirmation" required autocomplete="off"></div></div><p><button class="danger">Permanently delete database</button></p></form>{% if delete_request %}<h3>Last delete request</h3><pre>{{delete_request|tojson(indent=2)}}</pre>{% endif %}</section></main><script>
const profile=document.getElementById('provision_profile'),region=document.getElementById('provision_region'),paid=document.getElementById('paid_options'),tiers=document.querySelectorAll('input[name="tier"]');
profile.addEventListener('change',()=>{region.value=profile.selectedOptions[0].dataset.region||region.value});
function tier(){const isPaid=document.querySelector('input[name="tier"]:checked').value==="paid";paid.hidden=!isPaid;paid.querySelectorAll('input').forEach(input=>{input.disabled=!isPaid;input.required=isPaid})}tiers.forEach(input=>input.addEventListener('change',tier));tier();
const connectionProfile=document.getElementById('connection_profile'),connectionRegion=document.getElementById('connection_region');
connectionProfile.addEventListener('change',()=>{connectionRegion.value=connectionProfile.selectedOptions[0].dataset.region||connectionRegion.value});
</script></body></html>"""

# Keep the long-lived setup instructions together while putting discovery and card
# inspection on their own routes. The legacy in-page discovery panel is hidden.
PAGE = PAGE.replace('<body><main>', '''<body><main><nav class="steps top-nav"><a{% if workspace == 'infra' %} class="active"{% endif %} href="{{url_for('index', workspace='infra')}}">Setup · Infra</a><a{% if workspace == 'select-ai' %} class="active"{% endif %} href="{{url_for('index', workspace='select-ai')}}">Setup · Select AI</a><a href="{{url_for('agents_page')}}">Agents</a><a{% if workspace == 'inferencing' %} class="active"{% endif %} href="{{url_for('index', workspace='inferencing')}}">Inferencing</a></nav>''')
PAGE = PAGE.replace('<section class="panel"><h2>Published agents</h2>', '<section class="panel" hidden><h2>Published agents</h2>')
PAGE = PAGE.replace(
    '</body>',
    '''<script>
const providerInputs=document.querySelectorAll('input[name="provider"]');
function chooseProvider(){
  const oci=document.querySelector('input[name="provider"]:checked').value==='oci';
  document.querySelectorAll('.provider-oci').forEach(node=>{node.hidden=!oci;node.querySelectorAll('input').forEach(input=>input.required=oci)});
  document.querySelectorAll('.provider-openai').forEach(node=>{node.hidden=oci;node.querySelectorAll('input').forEach(input=>input.required=!oci)});
}
providerInputs.forEach(input=>input.addEventListener('change',chooseProvider));chooseProvider();
</script></body>''',
)
PAGE = PAGE.replace(
    '<p><a class="steps" href="{{url_for(\'agents_page\')}}">Continue to the Agents page &rarr;</a></p>',
    '''<form method="post" action="{{url_for('select_ai_test_route')}}"><label>Profile name to test</label><input name="profile_name" value="A2A_PROFILE" required><p class="hint">This sends one small chat request to the selected provider and can incur provider usage.</p><p><button{% if not admin_connection_ok %} disabled{% endif %}>Run live profile test</button></p></form><p><a class="steps" href="{{url_for('agents_page')}}">Continue to the Agents page &rarr;</a></p>''',
)
PAGE = PAGE.replace(
    '<div id="token" class="grid card">',
    '''<section id="select-ai-profiles" class="panel card"><h2>Saved Select AI profiles</h2><p class="hint">Profiles belong to the currently connected ADMIN schema. Dropping a profile does not delete its DBMS_CLOUD credential.</p><form method="post" action="{{url_for('select_ai_profiles_route')}}"><button{% if not admin_connection_ok %} disabled{% endif %}>Refresh profile list</button></form>{% if select_ai_profiles %}<ul>{% for profile in select_ai_profiles %}<li><code>{{profile}}</code></li>{% endfor %}</ul><form method="post" action="{{url_for('select_ai_profile_delete_route')}}"><div class="fields"><div><label>Profile to delete</label><select name="profile_name" required>{% for profile in select_ai_profiles %}<option value="{{profile}}">{{profile}}</option>{% endfor %}</select></div><div><label>Type DELETE to confirm</label><input name="confirmation" autocomplete="off" required></div></div><p><button class="danger">Delete Select AI profile</button></p></form>{% endif %}</section><div id="token" class="grid card">''',
)
PAGE = PAGE.replace(
    '<p><button>Download wallet locally</button></p>',
    '''<p><button>Download wallet locally</button><button class="secondary" formaction="{{url_for('wallet_status_route')}}" formmethod="post" formnovalidate>Check database status</button></p>{% if wallet_status %}<p class="hint">Last database lifecycle state: <strong>{{wallet_status}}</strong>{% if wallet_status != 'AVAILABLE' %} — wallet download waits for AVAILABLE.{% endif %}</p>{% endif %}''',
)
PAGE = PAGE.replace(
    '<form method="post" action="{{url_for(\'admin_test_route\')}}"><div class="fields">',
    '<form method="post" action="{{url_for(\'admin_test_route\')}}"><input type="hidden" name="admin_database_ocid" value="{{wallet_database_ocid}}"><div class="fields">',
)
PAGE = PAGE.replace(
    '<form method="post" action="{{url_for(\'select_ai_setup_route\')}}"><div class="fields">',
    '<form method="post" action="{{url_for(\'select_ai_setup_route\')}}" enctype="multipart/form-data"><div class="fields">',
)
PAGE = PAGE.replace(
    '<div class="wide provider-oci"><label>OCI API signing private key</label><input name="oci_private_key" type="password" autocomplete="off"></div>',
    '<div class="wide provider-oci"><label>OCI API-signing private key (.pem)</label><input name="oci_private_key_file" type="file" accept=".pem,.key,text/plain"><p class="hint">Choose the unencrypted PEM private key registered to the OCI user above. It is read only to create the database credential and is never saved locally by this app.</p></div>',
)
PAGE = PAGE.replace(
    'Only OCI Generative AI and OpenAI are available in this guided flow. Other Select AI providers are intentionally not supported here.',
    'Only OCI Generative AI and OpenAI are available in this guided flow. Other Select AI providers are intentionally not supported here. For OCI Generative AI, use an OCI user with an API-signing key and IAM permission to use Generative AI in the selected compartment.',
)
PAGE = PAGE.replace('name="provider" value="oci" checked', 'name="provider" value="oci"{% if select_ai_settings.get("provider", "oci") == "oci" %} checked{% endif %}')
PAGE = PAGE.replace('name="provider" value="openai"', 'name="provider" value="openai"{% if select_ai_settings.get("provider") == "openai" %} checked{% endif %}')
PAGE = PAGE.replace('name="credential_name" value="GENAI_CRED"', 'name="credential_name" value="{{select_ai_settings.get("credential_name", "GENAI_CRED")}}"')
PAGE = PAGE.replace('name="profile_name" value="A2A_PROFILE"', 'name="profile_name" value="{{select_ai_settings.get("profile_name", "A2A_PROFILE")}}"')
PAGE = PAGE.replace('name="oci_compartment_id" placeholder="ocid1.compartment.oc1..."', 'name="oci_compartment_id" value="{{select_ai_settings.get("oci_compartment_id", "")}}" placeholder="ocid1.compartment.oc1..."')
PAGE = PAGE.replace('name="oci_user_ocid" placeholder="ocid1.user.oc1..."', 'name="oci_user_ocid" value="{{select_ai_settings.get("oci_user_ocid", "")}}" placeholder="ocid1.user.oc1..."')
PAGE = PAGE.replace('name="oci_tenancy_ocid" placeholder="ocid1.tenancy.oc1..."', 'name="oci_tenancy_ocid" value="{{select_ai_settings.get("oci_tenancy_ocid", "")}}" placeholder="ocid1.tenancy.oc1..."')
PAGE = PAGE.replace('name="oci_fingerprint"', 'name="oci_fingerprint" value="{{select_ai_settings.get("oci_fingerprint", "")}}"')
PAGE = PAGE.replace('name="oci_model" placeholder="cohere.command-r-plus"', 'name="oci_model" value="{{select_ai_settings.get("oci_model", "")}}" placeholder="cohere.command-r-plus"')
PAGE = PAGE.replace('name="service_name" placeholder="mydb_high"', 'name="service_name" value="{{admin_settings.get("service_name", "")}}" placeholder="mydb_high"')
PAGE = PAGE.replace('name="admin_username" value="ADMIN"', 'name="admin_username" value="{{admin_settings.get("admin_username", "ADMIN")}}"')
PAGE = PAGE.replace(
    '<div class="wide provider-oci"><label>OCI compartment OCID</label>',
    '<div class="provider-oci"><label>OCI Generative AI region</label><input name="oci_region" value="{{select_ai_settings.get("oci_region", "")}}" placeholder="us-chicago-1"></div><div class="wide provider-oci"><label>OCI compartment OCID</label>',
)
PAGE = PAGE.replace(
    '<p><button{% if not admin_connection_ok %} disabled title="Test the ADMIN connection first"{% endif %}>Create credential and profile</button></p>',
    '<p><button{% if not admin_connection_ok %} disabled title="Test the ADMIN connection first"{% endif %}>Create credential and profile</button><button class="secondary" formaction="{{url_for(\'select_ai_routing_route\')}}" formmethod="post" formnovalidate{% if not admin_connection_ok %} disabled{% endif %}>Update existing OCI profile routing</button></p>',
)
PAGE = PAGE.replace(
    '<form method="post" action="{{url_for(\'select_ai_test_route\')}}">',
    '''{% if select_ai_debug_enabled %}<h3>Redacted Select AI debug trace</h3><p class="hint">Enabled by starting the app with <code>--debug</code> or <code>A2A_DEMO_DEBUG=1</code>. Secrets are omitted.</p><pre>{% if select_ai_debug %}{{select_ai_debug|join('\n\n')}}{% else %}No Select AI operation has run in this session yet.{% endif %}</pre>{% endif %}<form method="post" action="{{url_for('select_ai_test_route')}}">''',
)
PAGE = PAGE.replace('name="profile_name" value="A2A_PROFILE" required><p class="hint">This sends one small chat request', 'name="profile_name" value="{{select_ai_settings.get("profile_name", "A2A_PROFILE")}}" required><p class="hint">This sends one small chat request')
PAGE = PAGE.replace(
    '<p><button{% if not admin_connection_ok %} disabled{% endif %}>Run live profile test</button></p>',
    '<p><button{% if not admin_connection_ok %} disabled{% endif %}>Run live profile test</button><button class="secondary" formaction="{{url_for(\'select_ai_inspect_route\')}}" formmethod="post"{% if not admin_connection_ok %} disabled{% endif %}>Inspect stored profile</button></p>',
)
PAGE = PAGE.replace(
    '<p><a class="steps" href="{{url_for(\'agents_page\')}}">Continue to the Agents page &rarr;</a></p>',
    '''{% if select_ai_debug_enabled and select_ai_profile_attributes %}<h3>Stored profile attributes</h3><pre>{{select_ai_profile_attributes|tojson(indent=2)}}</pre>{% endif %}<p><a class="steps" href="{{url_for('agents_page')}}">Continue to the Agents page &rarr;</a></p>''',
)
PAGE = PAGE.replace('<div id="provision" class="grid"><section class="panel"><h2>Create ATP database</h2>', '<div id="provision" class="grid"><section class="panel"><h2>Create ATP database</h2>{% set feedback=section_feedback.get("provision") %}{% if feedback %}<div class="flash {{feedback.category}}">{{feedback.message}}</div>{% endif %}')
PAGE = PAGE.replace('<section id="wallet" class="panel card"><h2>2. Wallet &amp; ADMIN connection</h2>', '<section id="wallet" class="panel card"><h2>2. Wallet &amp; ADMIN connection</h2>{% set feedback=section_feedback.get("wallet") %}{% if feedback %}<div class="flash {{feedback.category}}">{{feedback.message}}</div>{% endif %}')
PAGE = PAGE.replace('<section id="select-ai" class="panel card"><h2>3. Select AI setup</h2>', '<section id="select-ai" class="panel card"><h2>3. Select AI setup</h2>{% set feedback=section_feedback.get("select-ai") %}{% if feedback %}<div class="flash {{feedback.category}}">{{feedback.message}}</div>{% endif %}')
PAGE = PAGE.replace('<div id="token" class="grid card"><section class="panel"><h2>4. Connection setup</h2>', '<div id="token" class="grid card"><section class="panel"><h2>4. Connection setup</h2>{% set feedback=section_feedback.get("token") %}{% if feedback %}<div class="flash {{feedback.category}}">{{feedback.message}}</div>{% endif %}')
PAGE = PAGE.replace('<section id="delete" class="panel card"><h2>5. Delete database</h2>', '<section id="delete" class="panel card"><h2>5. Delete database</h2>{% set feedback=section_feedback.get("delete") %}{% if feedback %}<div class="flash {{feedback.category}}">{{feedback.message}}</div>{% endif %}')
PAGE = PAGE.replace('<div id="provision" class="grid"><section class="panel"><h2>Create ATP database</h2>{% set feedback=section_feedback.get("provision") %}{% if feedback %}<div class="flash {{feedback.category}}">{{feedback.message}}</div>{% endif %}', '<div id="provision" class="grid"><section class="panel"><h2>Create ATP database</h2>')
PAGE = PAGE.replace('<section id="wallet" class="panel card"><h2>2. Wallet &amp; ADMIN connection</h2>{% set feedback=section_feedback.get("wallet") %}{% if feedback %}<div class="flash {{feedback.category}}">{{feedback.message}}</div>{% endif %}', '<section id="wallet" class="panel card"><h2>2. Wallet &amp; ADMIN connection</h2>')
PAGE = PAGE.replace('<section id="select-ai" class="panel card"><h2>3. Select AI setup</h2>{% set feedback=section_feedback.get("select-ai") %}{% if feedback %}<div class="flash {{feedback.category}}">{{feedback.message}}</div>{% endif %}', '<section id="select-ai" class="panel card"><h2>1. Create credential and profile</h2>')
PAGE = PAGE.replace('<div id="token" class="grid card"><section class="panel"><h2>4. Connection setup</h2>{% set feedback=section_feedback.get("token") %}{% if feedback %}<div class="flash {{feedback.category}}">{{feedback.message}}</div>{% endif %}', '<div id="token" class="grid card"><section class="panel"><h2>1. Selection and OAuth token</h2>')
PAGE = PAGE.replace('<section id="delete" class="panel card"><h2>5. Delete database</h2>{% set feedback=section_feedback.get("delete") %}{% if feedback %}<div class="flash {{feedback.category}}">{{feedback.message}}</div>{% endif %}', '<section id="delete" class="panel card"><h2>3. Delete database</h2>')

# Setup is deliberately a single route, but each stage behaves like a tab. This
# keeps the operator focused on one action while the dock preserves progress and
# results even when a request redirects back to the page.
PAGE = PAGE.replace(
    '<nav class="steps"><a href="#provision">1. Provision</a><a href="#wallet">2. Wallet &amp; admin connection</a><a href="#select-ai">3. Select AI &amp; team</a><a href="#token">4. Token &amp; Agent Card</a><a href="#delete">5. Delete</a></nav>',
    '''<nav class="setup-tabs" aria-label="Workspace stages">{% if workspace == 'infra' %}<button type="button" data-setup-tab="provision">1. Provision DB</button><button type="button" data-setup-tab="wallet">2. Wallet download</button><button type="button" data-setup-tab="delete">3. Delete DB</button>{% elif workspace == 'select-ai' %}<button type="button" data-setup-tab="credential">1. Create credential</button><button type="button" data-setup-tab="profile">2. Create profile</button><button type="button" data-setup-tab="delete-profile">3. Delete profile</button>{% else %}<button type="button" data-setup-tab="selection">1. Selection &amp; token</button><button type="button" data-setup-tab="chat">2. Chat</button>{% endif %}</nav>''',
)
PAGE = PAGE.replace('<div id="provision" class="grid">', '<div id="provision" class="grid" data-setup-panel="provision">')
PAGE = PAGE.replace('<section id="wallet" class="panel card">', '<section id="wallet" class="panel card" data-setup-panel="wallet">')
PAGE = PAGE.replace('<section id="select-ai" class="panel card">', '<section id="select-ai" class="panel card" data-setup-panel="credential">')
PAGE = PAGE.replace('<section id="select-ai-profiles" class="panel card">', '<section id="select-ai-profiles" class="panel card" data-setup-panels="profile delete-profile">')
PAGE = PAGE.replace('<div id="token" class="grid card">', '<div id="token" class="grid card" data-setup-panel="selection">')
PAGE = PAGE.replace('<section id="delete" class="panel card">', '<section id="delete" class="panel card" data-setup-panel="delete">')
PAGE = PAGE.replace('<form method="post"><div class="fields"><div><label>OCI CLI profile</label><select id="connection_profile"', '<form method="post"><input type="hidden" name="workspace" value="inferencing"><div class="fields"><div><label>OCI CLI profile</label><select id="connection_profile"')
PAGE = PAGE.replace(
    '<div id="token" class="grid card" data-setup-panel="selection">',
    '''<section id="chat" class="panel card" data-setup-panel="chat"><h2>2. Chat</h2><p class="hint">Run a small live request through the configured Select AI profile. The OAuth token is used for A2A discovery and Agent Cards; this chat uses the tested ADMIN wallet connection.</p><form method="post" action="{{url_for('select_ai_test_route')}}"><input type="hidden" name="return_workspace" value="inferencing"><label>Select AI profile</label><input name="profile_name" value="{{select_ai_settings.get('profile_name', 'A2A_PROFILE')}}" required><p><button{% if not admin_connection_ok %} disabled{% endif %}>Run live chat test</button></p></form>{% if selected_card %}<h3>Loaded Agent Card</h3><pre>{{selected_card|tojson(indent=2)}}</pre>{% endif %}</section><div id="token" class="grid card" data-setup-panel="selection">''',
)
PAGE = PAGE.replace('{% if selected_card %}<section class="panel card"><h2>Agent Card</h2><pre>{{selected_card|tojson(indent=2)}}</pre></section>{% endif %}', '')
PAGE = PAGE.replace(
    '</main><script>',
    '''<aside class="setup-dock" aria-label="Setup status" aria-live="polite"><div class="setup-dock-heading"><strong>Setup status</strong><span>Choose a stage above; its latest result stays here.</span></div><div class="setup-status-list">{% for item in setup_status %}<button type="button" class="setup-status-item{% if item.done %} complete{% endif %}" data-status-tab="{{item.tab}}"><span class="status-check" aria-hidden="true">{% if item.done %}✓{% else %}○{% endif %}</span><span><strong>{{item.label}}</strong><small>{{item.detail}}</small></span></button>{% endfor %}</div>{% if section_feedback %}<div class="setup-results"><strong>Latest results</strong>{% for section,feedback in section_feedback.items() %}<div class="dock-result {{feedback.category}}"><span>{{section|replace('-', ' ')}}</span>{{feedback.message}}</div>{% endfor %}</div>{% else %}<p class="setup-empty">No setup actions have run in this browser session.</p>{% endif %}</aside></main><script>''',
    1,
)
PAGE = PAGE.replace(
    '</style>',
    '''.setup-tabs{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 20px}.setup-tabs button{background:transparent;color:var(--blue);border:1px solid var(--line);font-weight:600}.setup-tabs button.active{background:var(--blue);color:#081224}.setup-dock{position:fixed;z-index:10;left:50%;bottom:0;transform:translateX(-50%);width:min(1120px,calc(100% - 32px));max-height:36vh;overflow:auto;background:#0b1729f7;border:1px solid var(--line);border-bottom:0;border-radius:14px 14px 0 0;padding:13px 16px;box-shadow:0 -12px 36px #0007;backdrop-filter:blur(10px)}main{padding-bottom:330px}.setup-dock-heading{display:flex;justify-content:space-between;gap:16px;align-items:baseline;margin-bottom:9px}.setup-dock-heading span,.setup-empty{color:var(--muted);font-size:12px;margin:0}.setup-status-list{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}.setup-status-item{display:flex;gap:9px;align-items:flex-start;text-align:left;border:1px solid var(--line);background:#0a1427;color:var(--text);padding:8px 10px}.setup-status-item:hover{border-color:var(--blue)}.setup-status-item.complete{border-color:#2f7759}.status-check{font-size:18px;line-height:1.25;color:var(--muted)}.complete .status-check{color:var(--good)}.setup-status-item strong,.setup-status-item small{display:block}.setup-status-item strong{font-size:12px}.setup-status-item small{color:var(--muted);font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:250px}.setup-results{display:grid;gap:5px;margin-top:11px;font-size:12px}.dock-result{padding:7px 9px;border-radius:7px;white-space:pre-wrap;overflow-wrap:anywhere}.dock-result span{display:block;text-transform:capitalize;font-weight:700;font-size:11px;margin-bottom:2px}@media(max-width:780px){main{padding-bottom:440px}.setup-dock{width:calc(100% - 16px);max-height:42vh;padding:11px}.setup-dock-heading{display:block}.setup-status-list{grid-template-columns:1fr 1fr}.setup-status-item small{max-width:140px}}</style>''',
    1,
)
PAGE = PAGE.replace('</style>', '.top-nav a.active{background:var(--blue);color:#081224;border-color:var(--blue);font-weight:700}</style>', 1)
PAGE = PAGE.replace(
    '</body>',
    '''<script>
const setupTabs=document.querySelectorAll('[data-setup-tab]');
const setupPanels=document.querySelectorAll('[data-setup-panel],[data-setup-panels]');
const setupNames=new Set(['provision','wallet','delete','credential','profile','delete-profile','selection','chat']);
const stageAliases={'select-ai':'credential',token:'selection'};
function selectSetupStage(stage, updateHash=false){
  const requested=stageAliases[stage]||stage;
  const selected=setupNames.has(requested)?requested:'{{ "credential" if workspace == "select-ai" else "selection" if workspace == "inferencing" else "provision" }}';
  setupPanels.forEach(panel=>{const stages=(panel.dataset.setupPanels||panel.dataset.setupPanel||'').split(' ');panel.hidden=!stages.includes(selected)});
  setupTabs.forEach(tab=>{tab.classList.toggle('active',tab.dataset.setupTab===selected)});
  if(updateHash) history.replaceState(null,'','#'+selected);
}
setupTabs.forEach(tab=>tab.addEventListener('click',()=>selectSetupStage(tab.dataset.setupTab,true)));
document.querySelectorAll('[data-status-tab]').forEach(item=>item.addEventListener('click',()=>selectSetupStage(item.dataset.statusTab,true)));
selectSetupStage(location.hash.slice(1));
</script></body>''',
    1,
)
PAGE = PAGE.replace('>Card testing</a>', '>Inferencing &amp; cards</a>')
PAGE = PAGE.replace('<h1>Card testing</h1>', '<h1>Inferencing &amp; card testing</h1>')


AGENTS_PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Agents · Oracle A2A Test Console</title><style>:root{color-scheme:dark;--bg:#09111f;--panel:#101c30;--line:#253652;--text:#edf3ff;--muted:#a9b9d3;--blue:#73a7ff;--good:#66d19e;--bad:#ff8989}*{box-sizing:border-box}body{margin:0;background:linear-gradient(135deg,#07101d,#0d1830);color:var(--text);font:15px/1.5 system-ui,sans-serif}main{max-width:980px;margin:auto;padding:48px 24px 72px}h1{margin:0;font-size:30px}h2{font-size:18px;margin:0 0 16px}.lede,.hint{color:var(--muted)}.tabs{display:flex;gap:8px;flex-wrap:wrap;margin:20px 0}.tabs a,button{border:1px solid var(--line);border-radius:8px;padding:9px 12px;color:var(--blue);background:transparent;text-decoration:none;font:inherit;cursor:pointer}.tabs a.active,button{background:var(--blue);color:#081224;font-weight:700}.panel{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:22px;margin-top:20px}.agent{border-top:1px solid var(--line);padding:15px 0}.agent:first-of-type{border-top:0;padding-top:0}.agent p{color:var(--muted);margin:4px 0 10px}code{overflow-wrap:anywhere;color:#c7d8f7}.flash{padding:12px 14px;border-radius:9px;margin:0 0 18px}.success{color:var(--good);background:#123525}.error{color:var(--bad);background:#3a1c25;white-space:pre-wrap;overflow-wrap:anywhere}ul{padding-left:20px}</style></head><body><main><h1>Published agent teams</h1><p class="lede">Create and manage the database team objects here, then refresh discovery using the bearer token obtained on Setup.</p><nav class="tabs"><a href="{{url_for('index')}}">Setup</a><a class="active" href="{{url_for('agents_page')}}">Agents</a><a href="{{url_for('cards')}}">Card testing</a></nav>{% with messages=get_flashed_messages(with_categories=true) %}{% for category,message in messages %}<div class="flash {{category}}">{{message}}</div>{% endfor %}{% endwith %}<section class="panel"><h2>Discovery</h2><p class="hint">After creating or publishing a team in this tab (or another SQL session), use Refresh. It calls the A2A discovery endpoint again rather than reusing the prior result.</p><form method="post" action="{{url_for('agents_refresh')}}"><button>Refresh published teams</button></form>{% if not settings.get('database_ocid') %}<p class="hint">First obtain a bearer token on Setup.</p>{% endif %}</section><section class="panel"><h2>Teams currently published to A2A</h2>{% if agents %}{% for agent in agents %}<div class="agent"><strong>{{agent.get('name','Unnamed team')}}</strong><p>{{agent.get('description','No description')}}</p><code>{{agent.get('agent_card_url','')}}</code><form method="post" action="{{url_for('agent_card')}}"><input type="hidden" name="card_url" value="{{agent.get('agent_card_url','')}}"><button>Open Agent Card</button></form></div>{% endfor %}{% else %}<p class="lede">No discovered teams yet. Create and enable a team, then refresh.</p>{% endif %}</section>{% if select_ai_checklist %}<section class="panel"><h2>Setup handoff</h2><ul>{% for item,status in select_ai_checklist.items() %}<li>{{item}} — {{status}}</li>{% endfor %}</ul></section>{% endif %}</main></body></html>"""
AGENTS_PAGE = AGENTS_PAGE.replace('</main>', r'''<section class="panel"><h2>Create or manage a team</h2><p class="hint">Create references an existing Select AI agent and task in the connected ADMIN schema. It creates the team enabled. Do not use Delete unless you mean to permanently remove that database team.</p><form method="post" action="{{url_for('team_action_route')}}"><p><label>Team name</label><input name="team_name" placeholder="SUPPORT_TEAM" required></p><p><label>Existing agent name (create only)</label><input name="agent_name" placeholder="SUPPORT_AGENT"></p><p><label>Existing task name (create only)</label><input name="task_name" placeholder="SUPPORT_TASK"></p><p><label>Description (create only)</label><input name="description"></p><p><label>Type DELETE only for deletion</label><input name="confirmation" autocomplete="off"></p><button name="action" value="create">Create &amp; enable team</button> <button name="action" value="enable">Enable</button> <button name="action" value="disable">Disable</button> <button name="action" value="delete">Delete team</button></form></section></main>''')
AGENTS_PAGE = AGENTS_PAGE.replace('<section class="panel"><h2>Discovery</h2>', '<section class="panel"><h2>Discovery</h2><p class="hint">A2A discovery is scoped to the database user that obtained the bearer token. If ADMIN created the team, test discovery with ADMIN or grant/use the appropriate owning schema.</p>')
AGENTS_PAGE = AGENTS_PAGE.replace('<nav class="tabs"><a href="{{url_for(\'index\')}}">Setup</a><a class="active" href="{{url_for(\'agents_page\')}}">Agents</a><a href="{{url_for(\'cards\')}}">Card testing</a></nav>', '''<nav class="tabs"><a href="{{url_for('index', workspace='infra')}}">Setup · Infra</a><a href="{{url_for('index', workspace='select-ai')}}">Setup · Select AI</a><a class="active" href="{{url_for('agents_page')}}">Agents</a><a href="{{url_for('index', workspace='inferencing')}}">Inferencing</a></nav><nav class="tabs"><a href="#refresh">1. Refresh teams</a><a href="#manage">2. Create team</a><a href="#manage">3. Delete team</a></nav>''')
AGENTS_PAGE = AGENTS_PAGE.replace('<section class="panel"><h2>Discovery</h2>', '<section id="refresh" class="panel"><h2>1. Refresh teams</h2>')
AGENTS_PAGE = AGENTS_PAGE.replace('<section class="panel"><h2>Discovery</h2><p class="hint">A2A discovery is scoped to the database user that obtained the bearer token. If ADMIN created the team, test discovery with ADMIN or grant/use the appropriate owning schema.</p>', '<section id="refresh" class="panel"><h2>1. Refresh teams</h2><p class="hint">A2A discovery is scoped to the database user that obtained the bearer token. If ADMIN created the team, test discovery with ADMIN or grant/use the appropriate owning schema.</p>')
AGENTS_PAGE = AGENTS_PAGE.replace('<section class="panel"><h2>Create or manage a team</h2>', '<section id="manage" class="panel"><h2>2. Create or delete a team</h2>')
AGENTS_PAGE = AGENTS_PAGE.replace('</style>', '.setup-dock{position:fixed;z-index:10;left:50%;bottom:0;transform:translateX(-50%);width:min(980px,calc(100% - 32px));max-height:30vh;overflow:auto;background:#0b1729f7;border:1px solid var(--line);border-bottom:0;border-radius:14px 14px 0 0;padding:12px;box-shadow:0 -10px 30px #0007}.setup-dock h2{font-size:14px;margin:0 0 8px}.setup-status-list{display:grid;grid-template-columns:repeat(3,1fr);gap:7px}.setup-status-item{border:1px solid var(--line);border-radius:7px;padding:6px 8px;color:var(--text);text-decoration:none;font-size:12px}.setup-status-item.complete{border-color:#2f7759;color:var(--good)}.dock-result{font-size:12px;margin-top:8px;padding:7px;border-radius:7px}.setup-dock~*{}main{padding-bottom:290px}@media(max-width:780px){main{padding-bottom:380px}.setup-status-list{grid-template-columns:1fr 1fr}}</style>', 1)
AGENTS_PAGE = AGENTS_PAGE.replace('</main>', '''<aside class="setup-dock" aria-label="Setup status"><h2>Setup status</h2><div class="setup-status-list">{% for item in setup_status %}<a class="setup-status-item{% if item.done %} complete{% endif %}" href="{{url_for('index', workspace='select-ai' if item.tab == 'select-ai' else 'inferencing' if item.tab == 'token' else 'infra')}}#{{item.tab}}">{% if item.done %}✓{% else %}○{% endif %} {{item.label}}<br><small>{{item.detail}}</small></a>{% endfor %}</div>{% for section,feedback in section_feedback.items() %}<div class="dock-result {{feedback.category}}">{{section }}: {{feedback.message}}</div>{% endfor %}</aside></main>''', 1)
AGENTS_PAGE = AGENTS_PAGE.replace('{% with messages=get_flashed_messages(with_categories=true) %}{% for category,message in messages %}<div class="flash {{category}}">{{message}}</div>{% endfor %}{% endwith %}', '')


CARDS_PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Card testing · Oracle A2A Test Console</title><style>:root{color-scheme:dark;--text:#edf3ff;--panel:#101c30;--line:#253652;--blue:#73a7ff;--muted:#a9b9d3;--good:#66d19e;--bad:#ff8989}*{box-sizing:border-box}body{margin:0;background:#09111f;color:var(--text);font:15px/1.5 system-ui,sans-serif}main{max-width:980px;margin:auto;padding:48px 24px}h1{margin:0}.lede{color:var(--muted)}.tabs{display:flex;gap:8px;flex-wrap:wrap;margin:20px 0}.tabs a{border:1px solid var(--line);border-radius:8px;padding:9px 12px;color:var(--blue);text-decoration:none}.tabs a.active{background:var(--blue);color:#081224;font-weight:700}.panel{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:22px;margin-top:20px}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:600px;overflow:auto;background:#07101f;border:1px solid var(--line);padding:16px;border-radius:9px;color:#d9e7ff;font-size:12px}.flash{padding:12px;border-radius:9px;margin:0 0 18px}.success{color:var(--good);background:#123525}.error{color:var(--bad);background:#3a1c25}</style></head><body><main><h1>Card testing</h1><p class="lede">Inspect the Agent Card loaded from a published team using the current bearer token.</p><nav class="tabs"><a href="{{url_for('index')}}">Setup</a><a href="{{url_for('agents_page')}}">Agents</a><a class="active" href="{{url_for('cards')}}">Card testing</a></nav>{% with messages=get_flashed_messages(with_categories=true) %}{% for category,message in messages %}<div class="flash {{category}}">{{message}}</div>{% endfor %}{% endwith %}<section class="panel"><h2>Loaded Agent Card</h2>{% if selected_card %}<pre>{{selected_card|tojson(indent=2)}}</pre>{% else %}<p class="lede">Choose Open Agent Card for a discovered team on the Agents page.</p>{% endif %}</section></main></body></html>"""
AGENTS_PAGE = AGENTS_PAGE.replace('>Card testing</a>', '>Inferencing &amp; cards</a>')
CARDS_PAGE = CARDS_PAGE.replace('<title>Card testing', '<title>Inferencing &amp; card testing')
CARDS_PAGE = CARDS_PAGE.replace('<h1>Card testing</h1>', '<h1>Inferencing &amp; card testing</h1>')
CARDS_PAGE = CARDS_PAGE.replace('>Card testing</a>', '>Inferencing &amp; cards</a>')


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
