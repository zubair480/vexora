# Check your team's two keys in half a minute. No band needed.
#   python3 check_keys.py
#
# 1. Deepgram: a pretend band plays 6 seconds of the kit's sample recording (a person
#    reading aloud) and Deepgram turns it into text.
# 2. OpenAI: one short question to the model.
# Each line says "works" or what is wrong and what to do. The keys are never printed.
import ai
import kit
import speech
from vexo_open import audio
from vexo_open.sync import Band

good = True
try:
    band = Band.connect(42, "sim", audio_packets=audio.ogg_packets(kit.SAMPLE_SPEECH))      # a pretend band with a voice
    heard = speech.listen(band, 6)
    band.disconnect()
    print('Deepgram key: works. It heard: "%s"' % heard if heard else "Deepgram key: accepted, but no words came back. Run it again.")
except kit.VexoError as problem:
    good = False
    print("Deepgram key: NOT working. %s" % problem)
try:
    print('OpenAI key: works. The model %s said: "%s"' % (ai.MODEL, ai.ask("Say the two words: all good")))
except kit.VexoError as problem:
    good = False
    print("OpenAI key: NOT working. %s" % problem)
raise SystemExit(0 if good else 1)
