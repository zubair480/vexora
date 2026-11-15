"""A pretend OpenAI for the tests: a tiny web server on this computer only.

It speaks the part of OpenAI's Responses API the kit uses (POST /v1/responses), in the
shape of OpenAI's documentation as read in November 2026: plain text answers, one tool
call, and JSON that follows a schema. Nothing here reaches the internet, no real key is
ever used with it, and it has never been compared with the real service by these tests.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

SERVER_WORDS = "WORDS-FROM-THE-SERVER"      # must never show up in what the kit prints


def message(text, kind="output_text"):
    part = {"type": kind, "text": text, "annotations": []} if kind == "output_text" else {"type": kind, "refusal": text}
    return {"type": "message", "id": "msg_1", "status": "completed", "role": "assistant", "content": [part]}


class MockOpenAI:
    """with MockOpenAI() as server: ... server.url, server.requests (path, headers, body)

    refuse=(429, "insufficient_quota")   answer with that status, that code word and SERVER_WORDS
    redirect="http://..."                answer 307 with that address
    answer={...}                         the whole JSON answer, instead of the rules below
    The rules: with tools, a call of the first tool whose name or allowed words appear in
    the input (else the last tool); with a schema, JSON with every field filled; otherwise
    the text "You said: <input>"."""

    def __init__(self, refuse=None, redirect=None, answer=None):
        self.refuse, self.redirect, self.answer, self.requests = refuse, redirect, answer, []
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                body = json.loads(raw.decode("utf-8"))
                mock.requests.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()},
                                      "body": body})
                if mock.redirect:
                    self.send_response(307)
                    self.send_header("Location", mock.redirect)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if mock.refuse:
                    status, word = mock.refuse
                    self.reply(status, {"error": {"message": SERVER_WORDS, "type": "invalid_request_error", "code": word}})
                    return
                self.reply(200, mock.answer or {"id": "resp_1", "object": "response", "status": "completed",
                                                "model": body.get("model"), "output": mock.output(body)})

            def reply(self, status, data):
                data = json.dumps(data).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d/v1/responses" % self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self._server.shutdown()
        self._server.server_close()

    @staticmethod
    def output(body):
        said = str(body.get("input", "")).lower()
        reasoning = {"type": "reasoning", "id": "rs_1", "summary": []}       # reasoning models put one first
        if body.get("tools"):
            picked = body["tools"][-1]
            for tool in body["tools"]:
                words = [w for field in tool["parameters"]["properties"].values() for w in field.get("enum", [])]
                if tool["name"] in said or any(w in said for w in words):
                    picked = tool
                    break
            details = {name: next((w for w in field.get("enum", []) if w in said), (field.get("enum") or ["x"])[0])
                       for name, field in picked["parameters"]["properties"].items()}
            return [reasoning, {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": picked["name"],
                                "arguments": json.dumps(details), "status": "completed"}]
        schema = body.get("text", {}).get("format", {}).get("schema")
        if schema:
            filled = {name: ["Item one from: " + body["input"][:20]] if field["type"] == "array" else "Short: " + body["input"][:40]
                      for name, field in schema["properties"].items()}
            return [reasoning, message(json.dumps(filled))]
        return [reasoning, message("You said: " + str(body.get("input", "")))]


def launcher(openai_url, examples, deepgram_url=None):
    """Python code that runs a program (sys.argv[1], with its arguments) with ai.py pointed
    at a pretend OpenAI (and speech.py at a pretend Deepgram). Both addresses are fixed in
    the code and are not options, so a test has to change them in the running program."""
    return ("import runpy, sys; sys.path.insert(0, %r); import ai, speech; ai.URL = %r; "
            "speech.DeepgramSpeech.URL = %r or speech.DeepgramSpeech.URL; "
            "sys.argv = sys.argv[1:]; runpy.run_path(sys.argv[0], run_name='__main__')"
            % (examples, openai_url, deepgram_url))
