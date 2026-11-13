import base64
import json
from types import SimpleNamespace

import server


class FakeWebSocket:
    def __init__(self):
        self.sent = []
        self.events = iter([
            {"type": "session.created"},
            {"type": "session.updated"},
            {"type": "response.output_audio.delta", "delta": base64.b64encode(b"\x00\x00\x01\x00").decode()},
            {"type": "response.done", "response": {"status": "completed"}},
        ])

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def recv(self, timeout=None):
        assert timeout == 60
        return json.dumps(next(self.events))

    def send(self, message):
        self.sent.append(json.loads(message))


def main():
    socket = FakeWebSocket()
    connect_calls = []

    def connect(url, **kwargs):
        connect_calls.append((url, kwargs))
        return socket

    ffmpeg_calls = []
    original_run = server.subprocess.run

    def fake_run(command, **kwargs):
        ffmpeg_calls.append((command, kwargs))
        return SimpleNamespace(stdout=b"mock-mp3")

    server.subprocess.run = fake_run
    try:
        mp3 = server.generate_realtime_tts_audio(
            connect,
            "/mock/ffmpeg",
            "test-key",
            "gpt-realtime-2.1-mini",
            {"voice": "fable", "input": "Click Save.", "instructions": "Speak clearly."},
        )
    finally:
        server.subprocess.run = original_run

    assert mp3 == b"mock-mp3"
    url, options = connect_calls[0]
    assert url == "wss://api.openai.com/v1/realtime?model=gpt-realtime-2.1-mini"
    assert options["additional_headers"] == {"Authorization": "Bearer test-key"}
    assert options["open_timeout"] == 10 and options["max_size"] == 1024 * 1024
    assert [event["type"] for event in socket.sent] == ["session.update", "conversation.item.create", "response.create"]
    config = socket.sent[0]["session"]
    assert config["model"] == "gpt-realtime-2.1-mini"
    assert config["audio"]["output"]["format"] == {"type": "audio/pcm", "rate": 24000}
    assert config["audio"]["output"]["voice"] == "alloy"
    assert socket.sent[1]["item"]["content"] == [{"type": "input_text", "text": "Click Save."}]
    assert socket.sent[2]["response"]["output_modalities"] == ["audio"]
    command, kwargs = ffmpeg_calls[0]
    assert command[command.index("-i") + 1] == "pipe:0"
    assert kwargs["input"] == b"\x00\x00\x01\x00"

    print("Realtime TTS protocol and PCM conversion request: PASS")


if __name__ == "__main__":
    main()
