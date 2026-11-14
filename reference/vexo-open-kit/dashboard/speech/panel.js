// The Listen and Live transcript part of the Microphone panel: two switches, two status
// lines, the key field and the text. app.js tells it what the band is doing; speech.js
// does the work.
import { Speech, canDecode, samplePackets } from './speech.js';
import { NO_DECODER, EXAMPLE_NOTE, SENDING_WARNING, CONSENT_LINE, transcriptStatus, listenStatus, footerNote, keyStatus } from './pure.js';

const $ = (id) => document.getElementById(id);

/**
 * @param {object} page what the panel needs from the rest of the dashboard
 * @param {() => boolean} page.audioIsOn is the band's audio stream on?
 * @param {(on: boolean) => Promise<void>} page.setAudio switch the band's audio stream (the Stream audio switch)
 * @param {() => object|null} page.pretendBand the pretend band, when that is what is connected
 */
export function speechPanel({ audioIsOn, setAudio, pretendBand }) {
  let supported = null;           // can this browser decode the sound for Listen? null = still checking
  let usable = false;             // the band is connected and answering
  let busy = false;               // a switch is being worked on
  let startedAudio = false;       // we switched Stream audio on ourselves, so we switch it off again
  let problem = '';
  let shownLines = 0;
  let keyRefused = false;         // what was typed into the key field could not be a key
  let keyAsked = false;           // Live transcript was switched on without a key
  let timer = 0;

  const speech = new Speech(draw);
  $('speech-note').textContent = EXAMPLE_NOTE;
  $('speech-warning').textContent = SENDING_WARNING;
  canDecode().then((yes) => {
    supported = yes;
    if (!yes) {
      $('speech-problem').textContent = NO_DECODER;
      $('speech-problem').hidden = false;
    }
    draw();
  });

  function draw() {
    $('listen-switch').disabled = !usable || busy || supported !== true;
    $('transcript-switch').disabled = !usable || busy;       // without a key it answers with a sentence, never with nothing
    if (!busy) {
      $('listen-switch').checked = speech.listening;
      $('transcript-switch').checked = speech.transcribing;
    }
    $('listen-status').textContent = problem && !speech.listening && !speech.transcribing ? problem : listenStatus({
      on: speech.listening, sound: speech.sound, blocked: speech.playBlocked, delay: speech.playDelay, lost: speech.lost,
    });
    $('transcript-status').textContent = transcriptStatus({
      state: speech.state, hasKey: speech.hasKey, pretend: !!pretendBand(), lag: speech.lag.typical, sound: speech.sound, problem: speech.problem,
    });
    $('transcript-status').dataset.state = speech.state;
    // The key, and one line the whole time sound goes to the company.
    $('speech-key-status').textContent = keyStatus({ hasKey: speech.hasKey, refused: keyRefused, asked: keyAsked });
    $('speech-key-forget').disabled = !speech.hasKey;
    $('speech-consent').hidden = !speech.sending;
    $('speech-consent').textContent = speech.sending ? CONSENT_LINE : '';
    $('footer-note').textContent = footerNote(speech.sending);
    drawText();
    // The status lines hold numbers that change (delay, lag), so they are refreshed while something is on.
    if (speech.active && !timer) timer = setInterval(draw, 1000);
    if (!speech.active && timer) {
      clearInterval(timer);
      timer = 0;
    }
  }

  function drawText() {
    const box = $('transcript');
    const done = $('transcript-final');
    box.hidden = !speech.transcribing && speech.lines.length === 0;
    if (speech.lines.length < shownLines) {       // a new start: the old text goes
      done.textContent = '';
      shownLines = 0;
    }
    const atEnd = box.scrollTop + box.clientHeight >= box.scrollHeight - 4;
    for (; shownLines < speech.lines.length; shownLines++) {
      const line = document.createElement('span');
      line.textContent = `${speech.lines[shownLines]} `;
      done.append(line);
    }
    while (done.children.length > 200) done.firstChild.remove();
    $('transcript-live').textContent = speech.live;
    $('transcript-empty').hidden = speech.lines.length > 0 || speech.live !== '';
    if (atEnd) box.scrollTop = box.scrollHeight;
  }

  // Take what is in the key field into the page's memory, and empty the field at once.
  function takeKey() {
    const field = $('speech-key');
    const typed = field.value.trim();
    field.value = '';
    if (typed === '') return;
    keyRefused = !speech.setKey(typed);
    keyAsked = false;
    draw();
  }

  async function flip(which, want) {
    if (which === 'transcript' && want) {
      takeKey();
      if (!speech.hasKey) {       // no key: say so, and do not switch the microphone on for nothing
        keyAsked = !keyRefused;
        $('transcript-switch').checked = false;
        draw();
        $('speech-key').focus();
        return;
      }
    }
    busy = true;
    problem = '';
    draw();
    try {
      if (want) {
        // The pretend band says nothing by itself: give it the sample voice (loaded only now).
        const pretend = pretendBand();
        if (pretend && !pretend.audioPackets) {
          try {
            pretend.audioPackets = await samplePackets();
          } catch (error) {
            problem = `The pretend band's sample voice could not be loaded, so it stays silent. ${error.message}`;
          }
        }
        if (!audioIsOn()) {
          await setAudio(true);       // the same as flipping Stream audio: blue light, LED locked
          if (!audioIsOn()) return;   // the band refused; the Microphone panel says why
          startedAudio = true;
        }
      }
      if (which === 'listen') speech.setListen(want);
      else speech.setTranscript(want);
      if (!speech.active && startedAudio && audioIsOn()) {
        startedAudio = false;
        await setAudio(false);        // leave the band as we found it
      }
    } catch (error) {
      problem = String(error?.message ?? error);
    } finally {
      busy = false;
      draw();
    }
  }
  $('listen-switch').addEventListener('change', (e) => flip('listen', e.target.checked));
  $('transcript-switch').addEventListener('change', (e) => flip('transcript', e.target.checked));
  $('speech-key').addEventListener('change', takeKey);       // Enter, or leaving the field
  $('speech-key-forget').addEventListener('click', () => {
    $('speech-key').value = '';
    keyRefused = false;
    keyAsked = false;
    const wasOn = speech.transcribing;
    speech.forgetKey();                       // if the key is in use, Live transcript goes off
    if (wasOn && !speech.active && startedAudio && audioIsOn()) {
      startedAudio = false;
      setAudio(false).catch(() => {});        // and the microphone with it, if we had switched it on
    }
    draw();
  });
  document.addEventListener('click', () => speech.nudge());

  return {
    /** One `audio` event from the band. */
    frame: (frame) => speech.frame(frame),
    /** The band's audio stream was switched, or came back after a reconnect. */
    audioChanged(on) {
      if (on) {
        speech.streamRestarted();
      } else {
        startedAudio = false;
        speech.stop();                // nothing to hear or write down without sound
      }
      draw();
    },
    /** The band is connected and answering (true), or not (false). */
    setUsable(yes) {
      usable = yes;
      draw();
    },
    /** The link is gone for good: everything off. */
    disconnected() {
      startedAudio = false;
      usable = false;
      speech.stop();
      draw();
    },
  };
}
