# Text to AI: send words to an OpenAI model and get an answer, a choice or filled-in fields.
#
#     import ai
#     print(ai.ask("Why is the sky blue?"))                        # words in, words out
#     name, details = ai.choose("make it red", [ai.tool(...)])     # the model picks one action
#     notes = ai.extract(long_text, SCHEMA)                        # the model fills in fields
#
# It needs a key. Each team gets its own OpenAI key at the event. A key is a secret: put it
# in the environment variable OPENAI_API_KEY, or in a file called ".env" in the folder you
# start your program in (one line: OPENAI_API_KEY=...). Never in your code, in git, in a
# chat or in a web page. Without a key every call stops with one sentence that says so.
#
# While you use it, the text you pass in is sent over the internet to OpenAI, a company.
# One line says so before the first request: keep that line in your project.
#
# Nothing to install: this is plain HTTPS with Python's standard library, about 40 lines,
# so you can read exactly what is sent. The official package (python3 -m pip install openai)
# does the same job with more features; use it if you like, the key is read the same way.
#
# The model is one constant, MODEL. To try another one without changing code, set the
# environment variable OPENAI_MODEL. AGENTS.md ("Audio to text to AI") has the whole story.
import json
import os
import ssl
import urllib.error
import urllib.request

import kit  # noqa: F401  (lets Python find the library)
from vexo_open.sync import VexoError, short_errors

short_errors(helper=__file__)        # a problem from this file ends a program with one sentence

KEY_NAME = "OPENAI_API_KEY"
MODEL = os.environ.get("OPENAI_MODEL", "").strip() or "gpt-6-luna"     # small, fast and cheap
URL = "https://api.openai.com/v1/responses"                            # the only place the key and the text go
TIMEOUT = 60                                                           # seconds to wait for an answer


class AiError(VexoError):
    """Something the person at the keyboard can fix; the message says how. It is a
    VexoError (code "ai"), so uncaught it ends a program with one sentence: "Problem (ai): ..."."""

    def __init__(self, message: str) -> None:
        super().__init__("ai", message)


def key() -> str:
    """The key from the environment variable, or from a ".env" file in the folder the
    program was started in. Never write a key into code."""
    found = os.environ.get(KEY_NAME, "").strip()
    if not found:
        try:
            with open(os.path.join(os.getcwd(), ".env"), encoding="utf-8") as f:
                for line in f:
                    name, _, value = line.strip().replace("export ", "", 1).partition("=")
                    if name.strip() == KEY_NAME and value.strip():
                        found = value.strip().strip("'\"")
        except (OSError, ValueError):
            pass
    if not found:
        raise AiError("This needs an OpenAI key: see \"Audio to text to AI\" in AGENTS.md. Put your team's key in the "
                      "environment variable %s (macOS, Linux: export %s=...   Windows PowerShell: $env:%s = \"...\"), "
                      "or in a file called .env in the folder you start the program in, with the line %s=your-key. "
                      "Never put it in your code or in git." % (KEY_NAME, KEY_NAME, KEY_NAME, KEY_NAME))
    if not found.isascii() or not found.isprintable() or " " in found:
        raise AiError("The OpenAI key in %s has a space or a strange character in it. "
                      "Copy it again, the whole key and nothing else." % KEY_NAME)
    return found


# What to do about each refusal. Only the number (and OpenAI's short code word) is shown;
# the sentences are ours, and the key is never part of a message.
REFUSED = {
    400: "OpenAI did not understand the request. Check the model name (%s) and the fields you sent." % MODEL,
    401: "OpenAI did not accept the key. Check %s: the whole key, nothing before or after it." % KEY_NAME,
    403: "The key is not allowed to use the model %s, or OpenAI is not available from this network." % MODEL,
    404: "OpenAI does not know the model %s. Check the OPENAI_MODEL variable, or MODEL in ai.py." % MODEL,
    429: "Too many requests at once, or the key's budget is used up. Wait a few seconds and try again; "
         "if it stays, tell an organiser.",
}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_):          # the key goes to the one address above, never on to another
        return None


def _context():
    context = ssl.create_default_context()
    try:                                      # some Pythons on a Mac ship without certificates
        import certifi
        context.load_verify_locations(certifi.where())
    except ImportError:
        pass
    return context


_told = False


def request(body: dict) -> dict:
    """One request to OpenAI's Responses API. `body` is the JSON it describes (input,
    instructions, tools, ...); the model is filled in. Returns the JSON answer."""
    global _told
    secret = key()
    if not _told:
        _told = True
        print("Note: the text is sent over the internet to OpenAI (openai.com), model %s." % MODEL)
    body = dict({"model": MODEL, "store": False}, **body)       # store False: OpenAI does not keep the answer
    ask_ = urllib.request.Request(URL, data=json.dumps(body).encode("utf-8"), method="POST",
                                  headers={"Authorization": "Bearer " + secret, "Content-Type": "application/json"})
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=_context()))
    try:
        with opener.open(ask_, timeout=TIMEOUT) as answer:
            return json.loads(answer.read(4000000).decode("utf-8"))
    except urllib.error.HTTPError as error:
        try:                                  # OpenAI's short code word, for example insufficient_quota
            word = str(json.loads(error.read(100000).decode("utf-8"))["error"]["code"] or "")
        except (ValueError, KeyError, TypeError, OSError):
            word = ""
        word = word if word.replace("_", "").isalnum() and len(word) < 40 else ""
        if error.code == 429 and word == "insufficient_quota":
            said = "The key's budget is used up. Tell an organiser."
        else:
            said = REFUSED.get(error.code, "OpenAI could not answer just now. Try again in a moment.")
        raise AiError("openai: %s (status %d%s)" % (said, error.code, ", " + word if word else "")) from None
    except ssl.SSLCertVerificationError:
        raise AiError("openai: This Python cannot check the certificate of api.openai.com. Fix it once "
                      "with:  python3 -m pip install certifi") from None
    except (OSError, ValueError):
        raise AiError("openai: Could not reach api.openai.com. Check the internet connection; "
                      "try another network or a phone's hotspot.") from None


def _parts(answer: dict, kind: str) -> list:
    """The items of one kind in the answer's "output" list."""
    if not isinstance(answer, dict) or not isinstance(answer.get("output"), list):
        raise AiError("openai: The answer did not look like the documentation describes.")
    if answer.get("status") == "incomplete":
        raise AiError("openai: The answer was cut off before it was finished. Ask for something shorter.")
    return [item for item in answer["output"] if isinstance(item, dict) and item.get("type") == kind]


def _text(answer: dict) -> str:
    pieces = [piece for item in _parts(answer, "message") for piece in item.get("content") or []]
    if any(piece.get("type") == "refusal" for piece in pieces):
        raise AiError("openai: The model declined to answer this.")
    return "".join(str(piece.get("text") or "") for piece in pieces if piece.get("type") == "output_text").strip()


def ask(text: str, instructions: str = "Answer in one or two short sentences.") -> str:
    """Words in, words out. `instructions` tells the model how to behave."""
    return _text(request({"input": str(text), "instructions": instructions}))


def tool(name: str, description: str, **choices) -> dict:
    """One action the model may pick. Each extra argument is a detail the model fills in:
    a list of allowed words (colour=["red", "green"]) or a type name ("string", "number")."""
    fields = {field: {"type": "string", "enum": list(allowed)} if isinstance(allowed, (list, tuple))
              else {"type": allowed} for field, allowed in choices.items()}
    return {"type": "function", "name": name, "description": description, "strict": True,
            "parameters": {"type": "object", "properties": fields, "required": list(fields),
                           "additionalProperties": False}}


def choose(text: str, tools: list, instructions: str = "Pick the one action that fits what was said."):
    """The model reads `text` and picks exactly one of `tools` (tool calling). Returns
    (name, details), for example ("set_light", {"colour": "red", "pattern": "blink"})."""
    answer = request({"input": str(text), "instructions": instructions, "tools": tools, "tool_choice": "required"})
    calls = _parts(answer, "function_call")
    if not calls or calls[0].get("name") not in [t["name"] for t in tools]:
        raise AiError("openai: The model picked none of the actions.")
    try:
        details = json.loads(calls[0].get("arguments") or "{}")
    except ValueError:
        details = None
    if not isinstance(details, dict):
        raise AiError("openai: The model's choice could not be read.")
    return calls[0]["name"], details


def extract(text: str, schema: dict, instructions: str = "Fill in the fields from the text.") -> dict:
    """The model reads `text` and answers as JSON that follows `schema` (structured output).
    Every field of the schema must be listed in "required", with "additionalProperties": False."""
    answer = request({"input": str(text), "instructions": instructions,
                      "text": {"format": {"type": "json_schema", "name": "answer", "strict": True, "schema": schema}}})
    try:
        found = json.loads(_text(answer))
    except ValueError:
        found = None
    if not isinstance(found, dict):
        raise AiError("openai: The model's answer could not be read.")
    return found
