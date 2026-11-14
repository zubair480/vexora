# Listen and Live transcript

An optional example in the dashboard's Microphone panel of what can be done with the
band's sound. Both switches are off at the start. It is a starting example, not a
finished product: `docs/speech.md` shows how to use the sound in your own program.

| Switch | What it does | Where the sound goes |
|---|---|---|
| Listen | Plays the band's microphone through this computer's speakers | Nowhere: it stays in the browser tab |
| Live transcript | Writes down what is said, while people speak | To Deepgram, a speech-to-text company on the internet |

Live transcript has one engine, Deepgram. It needs a key (each team gets its own;
`docs/deepgram.md` says where it comes from and the rules for it). Without a key the page
says so in a sentence, the microphone stays off, and everything else on the page works as
before. There is no speech model inside the browser any more: the two models that used to
run in the tab (about 100 MB of files in this folder) were removed, so the page has no
transcript without a key and without the internet.

## What Live transcript sends, and to whom

While Live transcript is on, **everything the band's microphone hears, from everyone near
the band, goes over the internet to Deepgram.** Tell the people around you first. The
band's solid blue light means the microphone is on. The page says this next to the key
field, and shows one line the whole time sound is going out: "The band's microphone is
being sent to Deepgram now. Switch Live transcript off to stop."

    band.on('audio')  ->  Opus packets, as they are  ->  hidden page  ->  api.deepgram.com  ->  text

- **Sound:** the band's own Opus packets, one packet per message, about 3 kB a second.
  Nothing is decoded or changed. A lost packet is simply not sent.
- **With the sound:** fixed settings in the address: `model=nova-3`, `language=en`,
  `encoding=opus`, `sample_rate=16000`, `channels=1`, `interim_results=true`,
  `smart_format=true`, `endpointing=100`, and `mip_opt_out=true`, which tells Deepgram not
  to use the sound to improve its models.
- **The key:** in the request's protocol field (the way Deepgram reads a key from a
  browser), never in an address.
- **The browser adds:** where the page is served from (`Origin`), the computer's internet
  address, the browser's name. No cookie and no `Referer`.
- **Not sent:** the band number, motion data, anything from the band's storage.

The text comes back to the page and is not stored. Switching off sends "close" to
Deepgram, takes the hidden page away, and no more sound leaves the computer.

## The key

- Typed into a password field. The page takes it out of the field at once and keeps it in
  the memory of the open page only: **reload or close the tab, or press "Forget key", and it
  is gone.** It is never written to the browser's storage, the page's address, the console
  or any text on the page.
- Use your own team's key. Anyone at the computer while the page is open could use it,
  so press "Forget key" when you are done.
- A key never belongs in code, a commit, a chat, a screenshot or the band's storage. If
  one got out, tell an organiser: it is replaced.

## The page's security policy

`dashboard/index.html` keeps `default-src 'self'`: the dashboard page can never contact
Deepgram or anything else, in any state. The code that talks to Deepgram runs in a small
hidden page of its own (an iframe), `deepgram-frame.html`, with its own policy:

    default-src 'none'; script-src 'self'; connect-src [the WebSocket address api.deepgram.com/v1/listen]

(The address is written here without `wss` and colon-slash-slash in front, because a test
keeps every web address out of this folder except in the two files that need it.)

- One address, host and path. Not the whole of Deepgram, and no other company.
- It is off until used: the hidden page is made when Live transcript is switched on with a
  key, and removed when it is switched off or fails.
- The hidden page takes one thing from the dashboard: the key. The address and the
  settings are fixed in `deepgram.js` and again in the policy; no message, page address or
  stored value can change where the key and the sound go.
- Nothing the service says is repeated on the page except the transcript itself. A refusal
  or a failure is shown as one of four fixed sentences.

What the hidden page is not: a wall against a hostile script in the dashboard page itself
(both come from the same place). What such a script could reach is still only that one
address.

| File | What it does |
|---|---|
| `panel.js` | The two switches, the status lines, the key field and the text on the page |
| `speech.js` | Listen: Opus packets to sound (the browser's own WebCodecs decoder) and playing (Web Audio). Live transcript: hands the packets to `online.js`. Holds the key in memory |
| `online.js` | Makes the hidden page, hands it the key and the packets, takes the text back, removes it |
| `deepgram-frame.html`, `deepgram-frame.js` | The hidden page and its few lines |
| `deepgram.js` | The one WebSocket to Deepgram: address, settings, first guesses and finished sentences, keep-alive, close |
| `load.js` | The only place that downloads a file: the pretend band's voice, from this folder |
| `pure.js` | Arithmetic and words only (lost packets, lag, status lines, the warning). Tested by `dashboard/test/speech.test.mjs` and `deepgram.test.mjs` |
| `../../js/src/audio.js` | In the kit's library, for any project: `BandAudio` (packets to samples), lost packets, sample rates, reading an Ogg file |
| `sample-speech.opus` | The pretend band's voice (see below) |

## The sample voice

`sample-speech.opus` is what the pretend band ("Try without a band") says when Listen or
Live transcript is on. It is a recording of a person reading aloud, taken from a
public-domain LibriVox audiobook: the start of the fable "The Crow and the Pitcher". The
words are

> In a spell of dry weather, when the birds could find very little to drink, a thirsty
> crow found a pitcher with a little water in it. But the pitcher was high and had a
> narrow neck, and no matter how he tried, the crow could not reach the water.

followed by 1.2 seconds of silence, 15.92 seconds in all, played in a loop. It is encoded
like the band's own sound (Opus, 16 kHz, mono, 20 ms packets, 24 kbit/s at a constant rate, low-delay
mode) with the libopus encoder and written as an Ogg Opus file with
`examples/python/ogg_opus.py`. Any player opens it.

### Where the recording comes from, and its licence

The addresses below are written without the `https` and colon-slash-slash in front,
because a test keeps every web address out of this folder's own files (the page must
never reach another server). Put it in front to open them.

| | |
|---|---|
| Audiobook | "The Aesop for Children", LibriVox, catalogued 2013: `librivox.org/the-aesop-for-children-by-aesop/` |
| Section | 8, "Fables 29-32: The Rat and the Elephant; The Boys and the Frogs; The Crow and the Pitcher; The Ants and the Grasshopper", read by Jill Engle |
| File used | `archive.org/download/aesopforchildren_1308_librivox/theaesopforchildren_08_aesop_64kb.mp3` (2 083 328 bytes, MP3, 22.05 kHz mono) |
| SHA-256 of that file | `60407b4d135ee0e517a2f97997f9ad35783c514ebc47c0f125b5d09f91c06c04` |
| Part used | from 152.2 s to 166.9 s (14.7 s) |
| Text read | "The Aesop for Children", Rand McNally & Co., Chicago, 1919 (`www.gutenberg.org/ebooks/19994`), in the public domain in the USA |
| Licence | Public domain. No permission, payment or credit is needed; the credit here is given by choice |

The licence, in the source's own words (all read on 2026-11-12):

- The recording itself begins: "This is a LibriVox recording. All LibriVox recordings are
  in the public domain."
- `librivox.org/pages/public-domain/`: "LibriVox records only texts that are in the
  public domain (in the USA [...]), and all our recordings are public domain (definitely in
  the USA, and maybe in your country as well [...]). This means anyone can use all our
  recordings however they wish (even to sell them)."
- The audiobook's page on LibriVox: "LibriVox recordings are Public Domain in the USA. If
  you are not in the USA, please verify the copyright status of these works in your own
  country before downloading".
- The Internet Archive item `archive.org/details/aesopforchildren_1308_librivox`
  is marked with the Creative Commons Public Domain Mark 1.0
  (`creativecommons.org/publicdomain/mark/1.0/`).

What was done to it: the 14.7 seconds were cut out, changed to 16 000 samples a second,
faded in and out over 0.03 s, made louder so the loudest point is 3 dB below full scale,
given 1.2 s of silence at the end, and encoded. Nothing else was changed, and nothing was
added to the voice. The MP3 itself is not in the kit.

## Checks

    node --test dashboard/test/*.test.mjs

runs the quick tests: no key, no internet (a pretend socket and a pretend service on this
computer). The checks below need Chrome. They are for the kit's maintainers and are typed
in the kit folder itself:

    node dashboard/test/deepgram-check.mjs
    python3 -m http.server 8137 --bind 127.0.0.1       (in the kit folder, in another terminal)
    node dashboard/test/listen-check.mjs 8137

`deepgram-check.mjs` drives the real page in headless Chrome against a pretend Deepgram
(no key, no internet): nothing leaves the computer before the switch, no key means a kind
sentence and no microphone, the key is only in the protocol field and in no address,
storage or text, the line about sending shows while it runs, a reload forgets the key, a
wrong key gives a fixed sentence, and the hidden page refuses every other address.

With a real key it measures the real service, with the kit's files exactly as they are.
Every file in the list is sent to Deepgram, so use only sound that holds nobody's private
speech. With no key it prints "Skipped":

    DEEPGRAM_API_KEY=... node dashboard/test/deepgram-check.mjs --real voices/voices.json --json out.json

## Measured

November 2026, Chrome 157 (headless) on a recent Mac laptop, the pretend band playing
recordings from a real band: 8 passages by 7 computer voices (the macOS `say` program from
a laptop speaker into the band's microphone, default gain, USB), 230 words, 100 seconds.
No human voice has been measured.

| | Deepgram (`nova-3`), through this page | For comparison: the removed browser models, measured before they were removed |
|---|---|---|
| Words wrong, of 230 | **56 (24 %)** | 27 % (bigger model), 38 % (smaller model) |
| ... the 6 passages by natural-sounding voices (140 words) | **5 (4 %)** | at most 1 word in 4 wrong in five of the eight passages |
| ... the 2 passages by a robot-like voice and a voice at 200 words a minute (90 words) | 51 (57 %) | bad as well |
| First live word on the page after the speech starts | 0.4 to 0.8 s (typically 0.6 s) | about 0.5 s |
| Finished sentence on the page after its last word (by Deepgram's own word times) | typically 0.5 s; 1.6 to 2.9 s in three passages | 1.0 to 1.3 s |
| Download before the first word | none | 43 or 78 MB |
| Works without internet, without a key | no | yes |

How to read it. Almost all mistakes are in two passages (an old robot-like voice, and a
voice at 200 words a minute through a laptop speaker); the other six came out with 5
words wrong in 140 (a name, a number written another way, one word). The same network
log shows 8 connections, all to the one address above, each answered with "101" and each
closed after switching off; no other address outside the computer was contacted.
With so few words, differences of a few percent mean nothing.
