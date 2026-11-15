# Meeting notes: a minute of talk becomes a short summary and a list of action items.
#   python3 meeting_notes.py 42                        listen to band 42 for 60 seconds
#   python3 meeting_notes.py 42 --seconds 300          a longer meeting
#   python3 meeting_notes.py --sim                     a pretend band that plays the kit's sample recording
#   python3 meeting_notes.py --sim --say "Ana books the room. Ben sends the slides by Friday."
#
# It needs your team's two keys, DEEPGRAM_API_KEY and OPENAI_API_KEY (with --say only the
# second), in environment variables or in a file called .env in the folder you are in.
# The microphone hears everyone in the room, and their words go to Deepgram and to OpenAI:
# ask them first. The model answers in the shape of NOTES (structured output): add a field
# there (decisions, questions, mood) and it is filled in too.
import ai
import kit
import speech
from vexo_open.sync import Band

NOTES = {"type": "object", "additionalProperties": False, "required": ["summary", "action_items"],
         "properties": {"summary": {"type": "string"},
                        "action_items": {"type": "array", "items": {"type": "string"}}}}

args = kit.args(seconds=60, more=[kit.SAY])
ai.key()                                                 # no OpenAI key: stop here, with one sentence
talk = args.say
if not talk:
    band = Band.connect(args.number, args.transport, **kit.voice(args))
    print("Listening to %s for %g seconds." % (band.name, args.seconds))
    talk = speech.listen(band, args.seconds)             # microphone -> Deepgram -> text
    band.disconnect()
print("Heard:", talk or "(no speech heard)")
if talk:
    notes = ai.extract(talk, NOTES, "Summarise what was said in two sentences. List the action items, each as "
                                    "who does what; an empty list if there are none.")       # text -> OpenAI -> fields
    print("Summary:", notes["summary"])
    for item in notes["action_items"] or ["(none)"]:
        print("To do:", item)
