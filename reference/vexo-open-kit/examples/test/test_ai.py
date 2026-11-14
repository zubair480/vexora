"""The three "audio to text to AI" examples, ai.py and check_keys.py, against a pretend
Deepgram and a pretend OpenAI on this computer, with made-up keys. Nothing here reaches
the internet. What these tests cannot tell: whether the real OpenAI service accepts the
requests exactly as sent (the shapes follow its documentation, read in November 2026).

    python3 -m unittest discover -s examples/test
"""
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.dirname(os.path.dirname(HERE))
PY = os.path.join(KIT, "examples", "python")
sys.path.insert(0, HERE)
from deepgram_mock import MockDeepgram  # noqa: E402
from openai_mock import SERVER_WORDS, MockOpenAI, launcher  # noqa: E402

KEYS = ("DEEPGRAM_API_KEY", "OPENAI_API_KEY", "OPENAI_MODEL")
NO_KEY = {k: v for k, v in os.environ.items() if k not in KEYS}     # no test uses a real key
AI_KEY, EARS_KEY = "k-ai-test", "k-ears-test"                        # made up


def run(name, *args, openai=None, deepgram=None, env=None, folder=None, timeout=60):
    """One example, started in an empty folder, with the two addresses swapped for pretend ones."""
    dead = "http://127.0.0.1:9/v1/responses"                          # nothing listens there
    with tempfile.TemporaryDirectory() as empty:
        done = subprocess.run([sys.executable, "-c", launcher(openai.url if openai else dead, PY, deepgram and deepgram.url),
                               os.path.join(PY, name)] + list(args), cwd=folder or empty, timeout=timeout,
                              env=dict(NO_KEY, **({"OPENAI_API_KEY": AI_KEY, "DEEPGRAM_API_KEY": EARS_KEY} if env is None else env)),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    return done


class Examples(unittest.TestCase):
    def ok(self, *args, **kw):
        done = run(*args, **kw)
        self.assertEqual((done.returncode, done.stderr), (0, ""), done.stdout)
        self.assertNotIn(AI_KEY, done.stdout)
        self.assertNotIn(EARS_KEY, done.stdout)
        return done.stdout

    def test_ask_the_band_with_typed_words(self):
        with MockOpenAI() as server:
            out = self.ok("ask_the_band.py", "--sim", "--say", "Why is the sky blue?", openai=server,
                          env={"OPENAI_API_KEY": AI_KEY})
        self.assertEqual(out, "You asked: Why is the sky blue?\n"
                              "Note: the text is sent over the internet to OpenAI (openai.com), model gpt-6-luna.\n"
                              "Answer: You said: Why is the sky blue?\n")
        (request,) = server.requests
        self.assertEqual(request["path"], "/v1/responses")
        self.assertEqual(request["headers"]["authorization"], "Bearer " + AI_KEY)
        self.assertEqual(request["headers"]["content-type"], "application/json")
        self.assertEqual(request["body"], {"model": "gpt-6-luna", "store": False, "input": "Why is the sky blue?",
                                           "instructions": "Answer in one or two short sentences."})

    def test_ask_the_band_from_the_microphone_to_the_answer(self):
        # pretend band (the kit's sample recording) -> pretend Deepgram -> pretend OpenAI
        with MockOpenAI() as server, MockDeepgram() as ears:
            out = self.ok("ask_the_band.py", "--sim", "--seconds", "1.3", openai=server, deepgram=ears)
        self.assertIn("Listening to Vexo-HACK-42 for 1.3 seconds. Ask your question.\n", out)
        self.assertLess(out.index("is sent over the internet to Deepgram"), out.index("You asked:"))
        self.assertIn("You asked: Sentence 1. The end.\n", out)
        self.assertIn("Answer: You said: Sentence 1. The end.\n", out)
        self.assertGreaterEqual(len(ears.sound), 55)                  # the sample recording went out as sound
        self.assertEqual(server.requests[0]["body"]["input"], "Sentence 1. The end.")

    def test_a_pretend_band_says_the_sample_once_however_long_you_ask_for(self):
        sys.path.insert(0, PY)
        try:
            import kit
        finally:
            sys.path.remove(PY)
        args = kit.argparse.Namespace(transport="sim", seconds=600, replay=None)
        packets = kit.voice(args)["audio_packets"]
        self.assertGreater(len(packets), 700)
        self.assertEqual(args.seconds, len(packets) / 50)
        self.assertLess(args.seconds, 20)
        real = kit.argparse.Namespace(transport="ble", seconds=600)
        self.assertEqual((kit.voice(real), real.seconds), ({}, 600))

    def test_voice_commands_with_typed_words(self):
        with MockOpenAI() as server:
            out = self.ok("voice_commands.py", "--sim", "--say", "please blink in red", openai=server)
        self.assertIn("Heard: \"please blink in red\"   Action: set_light {'colour': 'red', 'pattern': 'blink'}\n", out)
        body = server.requests[0]["body"]
        self.assertEqual(body["tool_choice"], "required")
        self.assertEqual([t["name"] for t in body["tools"]], ["set_light", "light_off", "nothing"])
        for tool in body["tools"]:                                    # the shape OpenAI's strict mode asks for
            self.assertEqual((tool["type"], tool["strict"]), ("function", True))
            self.assertEqual(tool["parameters"]["additionalProperties"], False)
            self.assertEqual(tool["parameters"]["required"], list(tool["parameters"]["properties"]))
        self.assertEqual(body["tools"][0]["parameters"]["properties"]["pattern"],
                         {"type": "string", "enum": ["solid", "blink", "breathe", "rainbow"]})

    def test_voice_commands_listen_then_set_the_light_round_after_round(self):
        # The band refuses the light while the microphone is on: the example must switch
        # the microphone off before each set_led, also in the second round.
        with MockOpenAI() as server, MockDeepgram() as ears:
            out = self.ok("voice_commands.py", "--sim", "--seconds", "1.2", "--rounds", "2", openai=server, deepgram=ears)
        self.assertEqual(out.count("Listening to Vexo-HACK-42 for 1.2 seconds. Say what the light should do.\n"), 2)
        self.assertEqual(out.count('Heard: "Sentence 1. The end."   Action: nothing \n'), 2)
        self.assertEqual(len(server.requests), 2)
        with MockOpenAI(answer={"status": "completed", "output": [
                {"type": "function_call", "name": "set_light", "arguments": '{"colour": "blue", "pattern": "breathe"}'}]}) as server, \
                MockDeepgram() as ears:
            out = self.ok("voice_commands.py", "--sim", "--seconds", "1.2", "--rounds", "2", openai=server, deepgram=ears)
        self.assertEqual(out.count("Action: set_light {'colour': 'blue', 'pattern': 'breathe'}\n"), 2)
        with MockOpenAI() as server:
            self.assertIn("Action: light_off \n", self.ok("voice_commands.py", "--sim", "--say", "light_off now", openai=server))

    def test_meeting_notes(self):
        talk = "Ana books the room. Ben sends the slides by Friday."
        with MockOpenAI() as server:
            out = self.ok("meeting_notes.py", "--sim", "--say", talk, openai=server)
        self.assertIn("Heard: %s\n" % talk, out)
        self.assertIn("Summary: Short: Ana books the room. Ben sends the slides\n", out)
        self.assertIn("To do: Item one from: Ana books the room. \n", out)
        form = server.requests[0]["body"]["text"]["format"]
        self.assertEqual((form["type"], form["strict"], form["name"]), ("json_schema", True, "answer"))
        self.assertEqual(form["schema"]["required"], ["summary", "action_items"])
        self.assertEqual(form["schema"]["additionalProperties"], False)
        with MockOpenAI() as server, MockDeepgram() as ears:
            out = self.ok("meeting_notes.py", "--sim", "--seconds", "1.2", openai=server, deepgram=ears)
        self.assertIn("Heard: Sentence 1. The end.\nNote: the text is sent", out)
        self.assertIn("Summary: Short: Sentence 1. The end.\n", out)

    def test_another_model_by_environment_variable(self):
        with MockOpenAI() as server:
            out = self.ok("ask_the_band.py", "--sim", "--say", "hi", openai=server,
                          env={"OPENAI_API_KEY": AI_KEY, "OPENAI_MODEL": "some-other-model"})
        self.assertIn("model some-other-model.", out)
        self.assertEqual(server.requests[0]["body"]["model"], "some-other-model")

    def test_without_an_openai_key_it_stops_before_it_listens(self):
        for name in ("ask_the_band.py", "voice_commands.py", "meeting_notes.py"):
            with MockOpenAI() as server, MockDeepgram() as ears:
                done = run(name, "--sim", openai=server, deepgram=ears, env={"DEEPGRAM_API_KEY": EARS_KEY})
            self.assertEqual((done.returncode, done.stdout), (1, ""), name)
            self.assertRegex(done.stderr, r"^Problem \(ai\): This needs an OpenAI key: see \"Audio to text to AI\" in AGENTS\.md\. "
                                          r".*export OPENAI_API_KEY=\.\.\. .*\$env:OPENAI_API_KEY = \"\.\.\.\".*a file called \.env.*\n"
                                          r"  \(line \d+ of %s\)\n$" % re.escape(name))
            self.assertEqual((server.requests, ears.requests), ([], []))

    def test_without_a_deepgram_key_typed_words_still_work_and_listening_says_what_is_missing(self):
        with MockOpenAI() as server, MockDeepgram() as ears:
            self.ok("ask_the_band.py", "--sim", "--say", "hi", openai=server, deepgram=ears, env={"OPENAI_API_KEY": AI_KEY})
            done = run("ask_the_band.py", "--sim", openai=server, deepgram=ears, env={"OPENAI_API_KEY": AI_KEY})
        self.assertEqual(done.returncode, 1)
        self.assertIn("Problem (speech): Transcription needs a Deepgram key: see docs/deepgram.md.", done.stderr)
        self.assertIn('Windows PowerShell: $env:DEEPGRAM_API_KEY = "..."', done.stderr)
        self.assertEqual(ears.requests, [])

    def test_the_keys_from_a_dot_env_file_in_the_folder_you_are_in(self):
        with MockOpenAI() as server, MockDeepgram() as ears, tempfile.TemporaryDirectory() as folder:
            with open(os.path.join(folder, ".env"), "w", encoding="utf-8") as f:
                f.write("DEEPGRAM_API_KEY=%s\nOPENAI_API_KEY='%s'\n" % (EARS_KEY, AI_KEY))
            out = self.ok("ask_the_band.py", "--sim", "--seconds", "1.2", openai=server, deepgram=ears, env={}, folder=folder)
            self.assertIn("Answer: You said: Sentence 1. The end.", out)
            self.assertEqual(server.requests[0]["headers"]["authorization"], "Bearer " + AI_KEY)
            self.assertEqual(ears.requests[0]["headers"]["authorization"], "Token " + EARS_KEY)
            # the kit's env.example, copied as it is: empty values count as "no key"
            with open(os.path.join(KIT, "env.example"), encoding="utf-8") as f:
                example = f.read()
            with open(os.path.join(folder, ".env"), "w", encoding="utf-8") as f:
                f.write(example)
            done = run("ask_the_band.py", "--sim", openai=server, deepgram=ears, env={}, folder=folder)
        self.assertEqual(done.returncode, 1)
        self.assertIn("This needs an OpenAI key", done.stderr)
        self.assertEqual(re.findall(r"^(\w+)=(.*)$", example, re.M), [("DEEPGRAM_API_KEY", ""), ("OPENAI_API_KEY", "")])

    def test_refusals_show_our_sentence_and_the_number_never_the_servers_words_or_the_key(self):
        for refuse, words in (((401, "invalid_api_key"), "OpenAI did not accept the key. Check OPENAI_API_KEY: the whole key, "
                                                         "nothing before or after it. (status 401, invalid_api_key)"),
                              ((429, "insufficient_quota"), "The key's budget is used up. Tell an organiser. (status 429, insufficient_quota)"),
                              ((429, "rate_limit_exceeded"), "Too many requests at once, or the key's budget is used up."),
                              ((404, "model_not_found"), "OpenAI does not know the model gpt-6-luna."),
                              ((400, SERVER_WORDS + " and more words"), "OpenAI did not understand the request. Check the model "
                                                                        "name (gpt-6-luna) and the fields you sent. (status 400)"),
                              ((500, None), "OpenAI could not answer just now. Try again in a moment. (status 500)")):
            with MockOpenAI(refuse=refuse) as server:
                done = run("ask_the_band.py", "--sim", "--say", "hi", openai=server)
            self.assertEqual(done.returncode, 1, refuse)
            self.assertIn("Problem (ai): openai: " + words, done.stderr)
            self.assertNotIn("Traceback", done.stderr)
            for secret in (SERVER_WORDS, AI_KEY):
                self.assertNotIn(secret, done.stdout + done.stderr)

    def test_no_way_to_reach_the_service_is_one_sentence(self):
        done = run("ask_the_band.py", "--sim", "--say", "hi")         # nothing listens at the address
        self.assertEqual(done.returncode, 1)
        self.assertRegex(done.stderr, r"^Problem \(ai\): openai: Could not reach api\.openai\.com\. Check the internet connection; "
                                      r"try another network or a phone's hotspot\.\n  \(line \d+ of ask_the_band\.py\)\n$")

    def test_the_key_is_not_sent_on_to_another_address(self):
        with MockOpenAI() as elsewhere, MockOpenAI(redirect="PLACEHOLDER") as server:
            server.redirect = elsewhere.url
            done = run("ask_the_band.py", "--sim", "--say", "hi", openai=server)
            self.assertEqual(done.returncode, 1)
            self.assertIn("(status 307)", done.stderr)
            self.assertEqual(len(server.requests), 1)
            self.assertEqual(elsewhere.requests, [])

    def test_check_keys(self):
        with MockOpenAI() as server, MockDeepgram() as ears:
            done = run("check_keys.py", openai=server, deepgram=ears)
        self.assertEqual((done.returncode, done.stderr), (0, ""), done.stdout)
        self.assertRegex(done.stdout, r'(?m)^Deepgram key: works\. It heard: "Sentence 1\. .*"$')
        self.assertIn('OpenAI key: works. The model gpt-6-luna said: "You said: Say the two words: all good"\n', done.stdout)
        with MockOpenAI(refuse=(401, "invalid_api_key")) as server, MockDeepgram(refuse=402) as ears:
            done = run("check_keys.py", openai=server, deepgram=ears)
        self.assertEqual(done.returncode, 1)
        self.assertIn("Deepgram key: NOT working. deepgram: The key has no credit left. Tell an organiser. (status 402)\n", done.stdout)
        self.assertIn("OpenAI key: NOT working. openai: OpenAI did not accept the key.", done.stdout)
        done = run("check_keys.py", env={})
        self.assertEqual(done.returncode, 1)
        self.assertIn("Deepgram key: NOT working. Transcription needs a Deepgram key", done.stdout)
        self.assertIn("OpenAI key: NOT working. This needs an OpenAI key", done.stdout)
        for secret in (AI_KEY, EARS_KEY, SERVER_WORDS):
            self.assertNotIn(secret, done.stdout + done.stderr)


class Helper(unittest.TestCase):
    """ai.py itself, in this process, pointed at the pretend service."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, PY)
        import ai
        cls.ai, cls.url, cls.before = ai, ai.URL, dict(os.environ)
        os.environ["OPENAI_API_KEY"] = AI_KEY
        ai._told = True

    @classmethod
    def tearDownClass(cls):
        cls.ai.URL = cls.url
        os.environ.clear()
        os.environ.update(cls.before)
        sys.path.remove(PY)

    def answer(self, output, status="completed"):
        server = MockOpenAI(answer={"status": status, "output": output})
        self.ai.URL = server.url
        return server

    def test_the_address_the_model_and_the_key_are_each_in_one_place(self):
        with open(os.path.join(PY, "ai.py"), encoding="utf-8") as f:
            code = f.read()
        self.assertEqual(re.findall(r"https?://[^\s\"']+", code), ["https://api.openai.com/v1/responses"])
        self.assertEqual(self.url, "https://api.openai.com/v1/responses")
        self.assertEqual(code.count("os.environ"), 2)                 # the key, and the model's name
        self.assertEqual(len(re.findall(r"gpt-[\w.-]+", code)), 1)
        self.assertNotRegex(code, r"print\([^)]*\b(secret|key)\b")
        for name in ("ask_the_band.py", "voice_commands.py", "meeting_notes.py", "check_keys.py"):
            with open(os.path.join(PY, name), encoding="utf-8") as f:
                text = f.read()
            self.assertNotIn("os.environ", text, name)
            self.assertNotRegex(text, r"gpt-|https?://", name)

    def test_text_is_joined_and_a_reasoning_item_is_skipped(self):
        from openai_mock import message
        with self.answer([{"type": "reasoning", "summary": []}, message("One. "), message("Two.")]):
            self.assertEqual(self.ai.ask("x"), "One. Two.")

    def test_a_refusal_a_cut_off_answer_and_a_strange_answer_are_each_one_sentence(self):
        from openai_mock import message
        for answer, words in ((([message("no", "refusal")],), "The model declined to answer this."),
                              (([], "incomplete"), "The answer was cut off"),
                              (("nonsense",), "did not look like the documentation describes")):
            with self.answer(*answer), self.assertRaises(self.ai.AiError) as caught:
                self.ai.ask("x")
            self.assertIn(words, str(caught.exception))
            self.assertEqual(caught.exception.code, "ai")

    def test_a_choice_outside_the_actions_or_unreadable_details_are_refused(self):
        tools = [self.ai.tool("go", "Go.", speed=["slow", "fast"], steps="number"), self.ai.tool("stop", "Stop.")]
        self.assertEqual(tools[0]["parameters"]["properties"], {"speed": {"type": "string", "enum": ["slow", "fast"]},
                                                                "steps": {"type": "number"}})
        with self.answer([{"type": "function_call", "name": "go", "arguments": '{"speed": "fast", "steps": 3}'}]):
            self.assertEqual(self.ai.choose("x", tools), ("go", {"speed": "fast", "steps": 3}))
        for output in ([{"type": "function_call", "name": "delete_everything", "arguments": "{}"}],
                       [{"type": "function_call", "name": "go", "arguments": "not json"}],
                       [{"type": "function_call", "name": "go", "arguments": "[1]"}], []):
            with self.answer(output), self.assertRaises(self.ai.AiError):
                self.ai.choose("x", tools)

    def test_fields_that_are_not_json_are_refused(self):
        from openai_mock import message
        with self.answer([message("not json")]), self.assertRaises(self.ai.AiError):
            self.ai.extract("x", {"type": "object"})
        with self.answer([message('{"a": 1}')]):
            self.assertEqual(self.ai.extract("x", {"type": "object"}), {"a": 1})

    def test_a_key_with_a_space_is_refused_before_anything_is_sent(self):
        with MockOpenAI() as server:
            self.ai.URL = server.url
            os.environ["OPENAI_API_KEY"] = "k one"
            try:
                with self.assertRaises(self.ai.AiError) as caught:
                    self.ai.ask("x")
            finally:
                os.environ["OPENAI_API_KEY"] = AI_KEY
            self.assertIn("has a space or a strange character", str(caught.exception))
            self.assertNotIn("k one", str(caught.exception))
            self.assertEqual(server.requests, [])


if __name__ == "__main__":
    unittest.main()
