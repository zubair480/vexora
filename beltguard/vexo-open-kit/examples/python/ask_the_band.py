# Ask the band: speak a question, an AI model answers. Microphone -> Deepgram -> OpenAI.
#   python3 ask_the_band.py 42                         speak to band 42 for 6 seconds
#   python3 ask_the_band.py --sim                      a pretend band that plays the kit's sample recording
#   python3 ask_the_band.py --sim --say "Why is the sky blue?"     typed words, no microphone
#
# It needs your team's two keys, DEEPGRAM_API_KEY (sound to text) and OPENAI_API_KEY (text
# to answer), in environment variables or in a file called .env in the folder you are in.
# With --say only the OpenAI key is needed. While it listens, the band's microphone
# (everyone near the band) goes to Deepgram, and the text goes to OpenAI: tell the people
# around you. speech.py and ai.py are the two helpers; AGENTS.md has the steps.
import ai
import kit
import speech
from vexo_open.sync import Band

args = kit.args(seconds=6, more=[kit.SAY])
ai.key()                                                 # no OpenAI key: stop here, with one sentence
question = args.say
if not question:
    band = Band.connect(args.number, args.transport, **kit.voice(args))
    print("Listening to %s for %g seconds. Ask your question." % (band.name, args.seconds))
    question = speech.listen(band, args.seconds)         # microphone -> Deepgram -> text
    band.disconnect()
print("You asked:", question or "(no speech heard)")
if question:
    print("Answer:", ai.ask(question, "Answer in one or two short sentences."))      # text -> OpenAI -> text
