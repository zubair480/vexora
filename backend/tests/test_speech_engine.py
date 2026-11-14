"""speech_engine with the kit's `speech.listen` faked. No band, no Deepgram, no network."""
import pytest

from backend import config, speech_engine


class FakeBand:
    """Not a band and never connected: just an object to hand through to the fake `listen`."""


class FakeSpeech:
    def __init__(self, text="", error=None, during=None):
        self.text, self.error, self.during, self.calls = text, error, during, []

    def listen(self, band, seconds, on_text=None, **options):
        self.calls.append((band, seconds, on_text, options))
        if self.during:
            self.during()
        if on_text:
            on_text("station", False)
            on_text(self.text, True)
        if self.error:
            raise self.error
        return self.text


@pytest.fixture()
def fake_speech(monkeypatch):
    def install(**kwargs):
        fake = FakeSpeech(**kwargs)
        monkeypatch.setattr(speech_engine, "_kit_speech", lambda: fake)
        return fake
    monkeypatch.setattr(config, "load_env", lambda: None)
    return install


def test_typed_path_needs_no_band_and_no_key():
    assert speech_engine.transcribe_typed("  Station 3,\n return idler 14  ") == "Station 3, return idler 14"
    assert speech_engine.transcribe_typed(None) == ""


def test_capture_wraps_kit_listen_with_the_given_band(fake_speech):
    fake = fake_speech(text=" Station 3, return idler 14, bearing seized. ")
    band, heard = FakeBand(), []
    text = speech_engine.capture(band, 5, on_text=lambda t, final: heard.append((t, final)))
    assert text == "Station 3, return idler 14, bearing seized."
    assert fake.calls[0][0] is band and fake.calls[0][1] == 5.0 and fake.calls[0][3] == {}
    assert heard[0] == ("station", False) and heard[-1][1] is True
    assert speech_engine.is_listening() is False


def test_capture_publishes_nothing(fake_speech, test_bus):
    seen = []
    test_bus.subscribe(seen.append)
    fake_speech(text="hello")
    speech_engine.capture(FakeBand(), 2)
    test_bus.drain()
    assert seen == []


def test_led_updates_are_queued_while_the_microphone_is_on(fake_speech):
    done = []

    def during():
        assert speech_engine.is_listening() is True
        assert speech_engine.queue_led_update(lambda: done.append("green")) is True
        assert speech_engine.queue_led_update(lambda: done.append("red")) is True
        assert done == []                       # LED is locked blue: nothing sent yet

    fake_speech(text="ok", during=during)
    speech_engine.capture(FakeBand(), 2)
    assert done == ["green", "red"]             # sent in order once audio is off
    assert speech_engine.queue_led_update(lambda: done.append("now")) is False and done[-1] == "now"


def test_no_band_and_bad_seconds_are_clear_errors(fake_speech):
    fake = fake_speech(text="x")
    with pytest.raises(speech_engine.SpeechCaptureError) as no_band:
        speech_engine.capture(None, 5)
    assert no_band.value.code == "no-band"
    for seconds in (0, -1, 31, "abc"):
        with pytest.raises(speech_engine.SpeechCaptureError) as bad:
            speech_engine.capture(FakeBand(), seconds)
        assert bad.value.code == "bad-seconds"
    assert fake.calls == []


def test_kit_errors_become_capture_errors_and_release_the_led_queue(fake_speech):
    class SpeechError(Exception):
        code, message = "speech", "Transcription needs a Deepgram key: see docs/deepgram.md."

    done = []
    fake_speech(error=SpeechError(), during=lambda: speech_engine.queue_led_update(lambda: done.append(1)))
    with pytest.raises(speech_engine.SpeechCaptureError) as failed:
        speech_engine.capture(FakeBand(), 3)
    assert failed.value.code == "speech" and "Deepgram key" in failed.value.message
    assert speech_engine.is_listening() is False and done == [1]


def test_second_capture_while_listening_is_refused(fake_speech):
    def during():
        with pytest.raises(speech_engine.SpeechCaptureError) as busy:
            speech_engine.capture(FakeBand(), 1)
        assert busy.value.code == "busy"

    fake_speech(text="ok", during=during)
    assert speech_engine.capture(FakeBand(), 2) == "ok"


def test_real_kit_speech_helper_has_the_signature_we_wrap():
    """The unmodified kit module: import only (no key read, no band, no network)."""
    import inspect
    speech = speech_engine._kit_speech()
    params = list(inspect.signature(speech.listen).parameters)
    assert params[:3] == ["band", "seconds", "on_text"]
    assert speech.KEY_NAME == "DEEPGRAM_API_KEY"
