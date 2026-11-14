# Voice commands by meaning: say what you want in your own words, an AI model picks one of
# a few actions, and the band's light does it. "I feel calm" can become a slow blue light.
#   python3 voice_commands.py 42                       three commands, 5 seconds each, to band 42
#   python3 voice_commands.py --sim                    a pretend band that plays the kit's sample recording
#   python3 voice_commands.py --sim --say "blink red, quickly"     typed words, no microphone
#
# It needs your team's two keys, DEEPGRAM_API_KEY and OPENAI_API_KEY (with --say only the
# second), in environment variables or in a file called .env in the folder you are in.
# The light is solid blue while the microphone is on and takes no commands then, so this
# listens first, switches the microphone off, and then sets the light.
# The model can only answer with one of ACTIONS (tool calling): add your own action there.
import ai
import kit
import speech
from vexo_open.sync import Band

COLOURS = {"red": "#ff0000", "green": "#00ff00", "blue": "#0000ff", "yellow": "#ffcc00",
           "purple": "#aa00ff", "white": "#ffffff"}
ACTIONS = [
    ai.tool("set_light", "Set the colour and the pattern of the band's light.",
            colour=list(COLOURS), pattern=["solid", "blink", "breathe", "rainbow"]),
    ai.tool("light_off", "Switch the band's light off."),
    ai.tool("nothing", "What was said is not about the light."),
]

args = kit.args(seconds=5, more=[kit.SAY, ("--rounds", "3", "how many commands to listen for (default 3)")])
ai.key()                                                 # no OpenAI key: stop here, with one sentence
band = Band.connect(args.number, args.transport, **kit.voice(args))
for _ in range(1 if args.say else int(args.rounds)):
    if not args.say:
        print("Listening to %s for %g seconds. Say what the light should do." % (band.name, args.seconds))
    words = args.say or speech.listen(band, args.seconds)           # microphone -> Deepgram -> text
    action, details = ai.choose(words, ACTIONS) if words else ("nothing", {})       # text -> OpenAI -> one action
    print('Heard: "%s"   Action: %s %s' % (words, action, details or ""))
    if action == "set_light":
        band.set_led(COLOURS[details["colour"]], pattern=details["pattern"], period=1000)
    elif action == "light_off":
        band.led_off()
    band.wait(3)                                         # look at the light
band.disconnect()
