"""Local Sign in with ChatGPT support for eligible ChatGPT plan usage."""

import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import time
from urllib.parse import urlencode
import urllib.error
import urllib.request
from uuid import uuid4


ISSUER = "https://auth.openai.com"
OAUTH_AUTHORIZE = ISSUER + "/api/accounts/authorize"
OAUTH_TOKEN = ISSUER + "/api/accounts/oauth/token"
RESOURCE = "https://api.openai.com/v1"
PLAN_SCOPE = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"


def _b64url(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def begin_chatgpt_signin(redirect_uri, host_id, client_id=None, id_token_hint=None):
    verifier = _b64url(secrets.token_bytes(48))
    state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    client_id = client_id or "dynamic_agent_client"
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": PLAN_SCOPE,
        "resource": RESOURCE,
        "state": state,
        "nonce": nonce,
        "code_challenge_method": "S256",
        "code_challenge": _b64url(hashlib.sha256(verifier.encode()).digest()),
        "ext_agent_host_id": host_id,
    }
    if client_id == "dynamic_agent_client":
        params["agent_name_hint"] = "Stepfilm"
    if id_token_hint and client_id != "dynamic_agent_client":
        params["id_token_hint"] = id_token_hint
    return OAUTH_AUTHORIZE + "?" + urlencode(params), {
        "state": state, "nonce": nonce, "verifier": verifier,
        "redirect_uri": redirect_uri, "client_id": client_id,
        "expires_at": time.time() + 600,
    }


def _post_form(url, values, timeout=30):
    request = urllib.request.Request(url, data=urlencode(values).encode(), headers={
        "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        try:
            body = json.loads(error.read())
            detail = body.get("error_description") or body.get("error")
        except Exception:
            detail = None
        raise RuntimeError(f"ChatGPT sign-in failed ({error.code})" + (f": {detail}" if detail else ".")) from error
    except urllib.error.URLError as error:
        raise RuntimeError("Could not reach ChatGPT sign-in. Check your internet connection.") from error


def exchange_chatgpt_code(code, issued_client_id, pending, callback_client_id=None):
    if callback_client_id and pending["client_id"] != "dynamic_agent_client" and callback_client_id != pending["client_id"]:
        raise ValueError("ChatGPT returned a different app registration. Start sign-in again.")
    client_id = callback_client_id or (issued_client_id if pending["client_id"] == "dynamic_agent_client" else pending["client_id"])
    if not client_id or client_id == "dynamic_agent_client":
        raise ValueError("ChatGPT did not finish registering Stepfilm. Try signing in again.")
    token = _post_form(OAUTH_TOKEN, {
        "grant_type": "authorization_code", "client_id": client_id,
        "code": code, "code_verifier": pending["verifier"],
        "redirect_uri": pending["redirect_uri"], "resource": RESOURCE,
    })
    scopes = set(str(token.get("scope", "")).split())
    if "chatgpt.tokens.use.direct" not in scopes:
        raise RuntimeError("ChatGPT sign-in did not grant plan usage to Stepfilm. Retry and allow plan usage.")
    id_token = token.get("id_token", "")
    if not id_token:
        raise RuntimeError("ChatGPT sign-in returned no identity token.")
    try:
        import jwt
        jwks = jwt.PyJWKClient(ISSUER + "/.well-known/jwks.json")
        signing_key = jwks.get_signing_key_from_jwt(id_token)
        identity = jwt.decode(id_token, signing_key.key, algorithms=["RS256", "ES256"],
                              audience=client_id, issuer=ISSUER,
                              options={"require": ["exp", "iss", "aud", "sub", "nonce"]})
    except ImportError as error:
        raise RuntimeError("Install Stepfilm's requirements.txt to validate ChatGPT sign-in securely, then retry.") from error
    except Exception as error:
        raise RuntimeError("Could not validate the ChatGPT sign-in response. Start sign-in again.") from error
    if identity.get("nonce") != pending["nonce"]:
        raise RuntimeError("ChatGPT sign-in nonce did not match. Start sign-in again.")
    expires_in = int(token.get("expires_in", 3600))
    return {
        "client_id": client_id, "host_id": pending.get("host_id", ""),
        "id_token": id_token, "access_token": token["access_token"],
        "refresh_token": token.get("refresh_token", ""),
        "token_type": token.get("token_type", "Bearer"),
        "expires_at": time.time() + max(60, expires_in),
        "scopes": sorted(scopes), "subject": identity["sub"],
        "email": identity.get("email", ""), "saved_at": time.time(),
    }


class ChatGPTPlan:
    """Manage one local user grant with owner-only file permissions."""

    def __init__(self, data_dir=None):
        self.data_dir = Path(data_dir or (Path.home() / ".stepfilm"))
        self.credentials_path = self.data_dir / "chatgpt-plan.json"
        self.host_path = self.data_dir / "chatgpt-host-id"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.data_dir.chmod(0o700)
        except OSError:
            pass
        self.host_id = self._load_host_id()
        self.pending = None
        self.credentials = self._load_credentials()
        self._model_cache = None

    def _load_host_id(self):
        try:
            value = self.host_path.read_text().strip()
            if value.startswith("urn:uuid:"):
                return value
        except OSError:
            pass
        value = "urn:uuid:" + str(uuid4())
        self._atomic_write(self.host_path, value.encode())
        return value

    def _load_credentials(self):
        try:
            value = json.loads(self.credentials_path.read_text())
            if value.get("refresh_token") and value.get("client_id") and "chatgpt.tokens.use.direct" in value.get("scopes", []):
                try:
                    self.credentials_path.chmod(0o600)
                except OSError:
                    pass
                return value
        except (OSError, ValueError, TypeError):
            pass
        return None

    def _atomic_write(self, path, payload):
        temp = path.with_name(path.name + ".tmp")
        fd = os.open(str(temp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
            path.chmod(0o600)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

    def start(self, redirect_uri):
        existing = self.credentials or {}
        url, pending = begin_chatgpt_signin(redirect_uri, self.host_id,
                                            existing.get("client_id"), existing.get("id_token"))
        pending["host_id"] = self.host_id
        self.pending = pending
        return url

    def finish(self, code, state, callback_client_id=None):
        pending = self.pending
        self.pending = None
        if not pending or pending.get("expires_at", 0) < time.time() or not secrets.compare_digest(str(state), pending["state"]):
            raise ValueError("ChatGPT sign-in expired or could not be verified. Start again.")
        issued = callback_client_id if pending["client_id"] == "dynamic_agent_client" else pending["client_id"]
        credentials = exchange_chatgpt_code(code, issued, pending, callback_client_id)
        if self.credentials and self.credentials.get("client_id") == credentials["client_id"] and self.credentials.get("subject") != credentials.get("subject"):
            raise RuntimeError("ChatGPT signed in as a different account than this Stepfilm connection. Disconnect and start a new registration if you want to switch accounts.")
        credentials["host_id"] = self.host_id
        self._atomic_write(self.credentials_path, json.dumps(credentials).encode())
        self.credentials = credentials
        return {"connected": True, "email": credentials.get("email", "")}

    def cancel(self, state):
        pending = self.pending
        self.pending = None
        if not pending or pending.get("expires_at", 0) < time.time() or not secrets.compare_digest(str(state), pending["state"]):
            raise ValueError("ChatGPT sign-in expired or could not be verified. Start again.")

    def access_token(self):
        credentials = self.credentials
        if not credentials:
            raise RuntimeError("Continue with ChatGPT to use your eligible plan allowance.")
        if credentials.get("expires_at", 0) > time.time() + 90:
            return credentials["access_token"]
        if not credentials.get("refresh_token"):
            self.credentials = None
            raise RuntimeError("Your ChatGPT session expired. Reconnect ChatGPT to continue.")
        token = _post_form(OAUTH_TOKEN, {
            "grant_type": "refresh_token", "client_id": credentials["client_id"],
            "refresh_token": credentials["refresh_token"], "resource": RESOURCE,
        })
        credentials = {**credentials, **token, "expires_at": time.time() + max(60, int(token.get("expires_in", 3600))), "saved_at": time.time()}
        credentials.pop("scope", None)
        self._atomic_write(self.credentials_path, json.dumps(credentials).encode())
        self.credentials = credentials
        return credentials["access_token"]

    def model_slug(self):
        if self._model_cache:
            return self._model_cache
        request = urllib.request.Request(RESOURCE + "/models", headers={"Authorization": "Bearer " + self.access_token()})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = json.loads(response.read())
        except (urllib.error.URLError, urllib.error.HTTPError) as error:
            raise RuntimeError("Could not load models available to this ChatGPT account.") from error
        models = data.get("models", [])
        slugs = [item.get("slug") for item in models if isinstance(item, dict) and item.get("visibility") == "list" and isinstance(item.get("slug"), str)]
        if not slugs:
            raise RuntimeError("This ChatGPT account did not return any models available to Stepfilm.")
        preferences = ("gpt-6.1-sol", "gpt-6.1-luna", "gpt-5.6-luna", "gpt-6-sol", "gpt-5.5")
        self._model_cache = next((slug for wanted in preferences for slug in slugs if slug == wanted), slugs[0])
        return self._model_cache

    def disconnect(self):
        self.credentials = None
        self.pending = None
        self._model_cache = None
        self.credentials_path.unlink(missing_ok=True)

    def status(self):
        credentials = self.credentials or {}
        return {"connected": bool(credentials), "email": credentials.get("email", "")}
