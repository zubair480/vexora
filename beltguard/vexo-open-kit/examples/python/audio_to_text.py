# Listen to the band's microphone and turn the speech into text.
#   python3 audio_to_text.py 42                    listen to band 42 for 10 seconds
#   python3 audio_to_text.py 42 --seconds 30       listen longer
#   python3 audio_to_text.py 42 --text no          only save the sound; nothing leaves the computer
#   python3 audio_to_text.py --sim                 a pretend band: it has no microphone and sends silence
#   python3 audio_to_text.py --sim --replay ../../dashboard/speech/sample-speech.opus
#                                                  the pretend band plays that file (a person reading aloud)
#
# The sound is also saved as recording.ogg in the folder you are in; any player can open it.
#
# The text comes from Deepgram, an online speech-to-text service. While this runs, the
# sound of the band's microphone (everyone near the band) goes over the internet to that
# company: tell the people around you. It needs your team's key in the environment variable
# DEEPGRAM_API_KEY, or in a file called .env in the folder you are in (one line:
# DEEPGRAM_API_KEY=...). Never in a file that is committed. Without a key the sound is
# still saved and the program says what is missing. Nothing to install.
# speech.py holds the engine and the interface another speech service plugs into;
# docs/deepgram.md explains the key, the rules and the measurements.
import kit
import speech
from ogg_opus import OggOpusWriter, read_packets
from vexo_open.sync import Band

args = kit.args(seconds=10, more=[
    ("--out", "recording.ogg", "where to save the sound (default: recording.ogg in the folder you are in)"),
    ("--text", "yes", "yes = turn the speech into text with Deepgram (online, needs your team's key); "
                      "no = only save the sound"),
    ("--replay", None, "with --sim: an Ogg Opus file the pretend band plays as its sound"),
])
ears = None
if args.text != "no":
    try:
        ears = speech.create()                                  # your own engine: its name here
        ears.start(lambda text, final: print("Heard:", text) if final else None)
        print("Speech is turned into text by %s." % ears.label)
    except (speech.SpeechError, kit.VexoError) as problem:
        ears = None
        print("No text this time. %s" % problem)
        print("The sound is still saved; any speech-to-text tool that reads Ogg Opus can use it.")

replay = {"audio_packets": read_packets(args.replay)} if args.replay else {}
band = Band.connect(args.number, args.transport, **replay)
ogg = OggOpusWriter(args.out)
# The band sends 50 packets a second: never keep more than that. A replayed file is heard once, not again from its start.
most = min(int(args.seconds * 50) + 50, len(replay["audio_packets"]) if replay else 10 ** 9)


def on_audio(a):                                        # 50 small packets a second
    if ogg.frames < most:
        ogg.write(a.seq, a.opus)                        # into the file
        if ears:
            ears.feed(a.opus, a.seq)                    # and to the speech engine


band.on("audio", on_audio)
if args.transport == "sim" and not args.replay:
    print("The pretend band has no microphone: it sends silence. To hear it work, add:   --replay %s" % kit.sample_speech())
print("Listening to %s for %g seconds.%s" % (band.name, args.seconds, "" if args.transport == "sim" else " Speak now."))
print("The band's light is solid blue while the microphone is on.")
print("Until the microphone is off again, the band refuses LED commands.")
band.configure(audio=True)
band.wait(args.seconds)
band.configure(audio=False)                             # mic off, blue light off
band.disconnect()
ogg.close()
print("Saved %.1f seconds of sound to %s (%d packets lost on the way)."
      % (ogg.seconds, args.out, ogg.lost))
if ears:
    print("Text:", ears.stop() or "(no speech heard)")
