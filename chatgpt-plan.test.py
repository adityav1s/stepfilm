import json
import os
import sys
import tempfile
import types
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import chatgpt_plan
import server


def test_oauth_storage_and_refresh():
    with tempfile.TemporaryDirectory() as temp:
        plan = chatgpt_plan.ChatGPTPlan(temp)
        url = plan.start("http://127.0.0.1:8765/auth/callback")
        query = parse_qs(urlsplit(url).query)
        assert query["client_id"] == ["dynamic_agent_client"]
        assert query["agent_name_hint"] == ["Stepfilm"]
        assert query["ext_agent_host_id"] == [plan.host_id]
        assert "chatgpt.tokens.use.direct" in query["scope"][0]
        assert len(plan.pending["verifier"]) >= 43
        assert plan.pending["redirect_uri"] == "http://127.0.0.1:8765/auth/callback"

        class FakeJwks:
            def __init__(self, _url): pass
            def get_signing_key_from_jwt(self, _token): return types.SimpleNamespace(key="verified-key")

        expected_nonce = plan.pending["nonce"]
        fake_jwt = types.SimpleNamespace(
            PyJWKClient=FakeJwks,
            decode=lambda token, key, **kwargs: {
                "sub": "account-1", "email": "creator@example.com",
                "nonce": expected_nonce, "aud": "oaiapp-test", "iss": chatgpt_plan.ISSUER,
            },
        )
        token_response = {
            "access_token": "access-1", "refresh_token": "refresh-1", "id_token": "signed-id-token",
            "token_type": "Bearer", "expires_in": 3600,
            "scope": "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct",
        }
        with patch.dict(sys.modules, {"jwt": fake_jwt}), patch.object(chatgpt_plan, "_post_form", return_value=token_response) as exchange:
            result = plan.finish("auth-code", plan.pending["state"], "oaiapp-test")
            assert result == {"connected": True, "email": "creator@example.com"}
            assert exchange.call_args.args[0] == chatgpt_plan.OAUTH_TOKEN
            assert exchange.call_args.args[1]["code_verifier"]
        assert os.stat(plan.credentials_path).st_mode & 0o777 == 0o600
        assert plan.access_token() == "access-1"
        plan.credentials["expires_at"] = 0
        with patch.object(chatgpt_plan, "_post_form", return_value={
            "access_token": "access-2", "refresh_token": "refresh-2", "expires_in": 3600,
        }) as refresh:
            assert plan.access_token() == "access-2"
            assert refresh.call_args.args[1]["grant_type"] == "refresh_token"
        assert plan.status()["connected"]
        plan.disconnect()
        assert not plan.status()["connected"] and not plan.credentials_path.exists()


def test_response_api_image_conversion():
    class FakePlan:
        def model_slug(self): return "gpt-6.1-sol"
        def access_token(self): return "plan-access-token"

    class FakeStream:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def __iter__(self):
            yield b'data: {"type":"response.output_text.delta","delta":"{\\"ok\\":true}"}\n'
            yield b'data: {"type":"response.completed"}\n'

    opened = []
    def fake_urlopen(request, timeout):
        opened.append((request, timeout))
        return FakeStream()

    messages = [
        {"role": "system", "content": "Return JSON only."},
        {"role": "user", "content": [
            {"type": "text", "text": "Describe this screenshot."},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA", "detail": "high"}},
        ]},
    ]
    with patch.object(server.urllib.request, "urlopen", side_effect=fake_urlopen):
        result = server.chatgpt_responses_text(FakePlan(), messages)
    assert result == '{"ok":true}'
    request, timeout = opened[0]
    payload = json.loads(request.data)
    assert request.full_url == "https://api.openai.com/v1/responses"
    assert request.get_header("Authorization") == "Bearer plan-access-token"
    assert payload["store"] is False and payload["stream"] is True
    assert payload["instructions"] == "Return JSON only."
    assert payload["input"][0]["content"][1] == {
        "type": "input_image", "image_url": "data:image/png;base64,AAAA", "detail": "high",
    }
    assert timeout == 180


test_oauth_storage_and_refresh()
test_response_api_image_conversion()
print("ChatGPT plan connection: PASS (PKCE URL, verified identity gate, private token file, refresh, Responses API image mapping)")
