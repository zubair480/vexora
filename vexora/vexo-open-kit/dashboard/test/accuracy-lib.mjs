// Counting wrong words (the word error rate) for deepgram-check.mjs, the eight test
// passages, and making a clean computer voice sound more like a band's microphone.
// Tested by accuracy.test.mjs.

const ONES = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten', 'eleven', 'twelve', 'thirteen',
  'fourteen', 'fifteen', 'sixteen', 'seventeen', 'eighteen', 'nineteen'];
const TENS = ['', '', 'twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy', 'eighty', 'ninety'];

/** 42 -> "forty two", 2026 -> "two thousand twenty six" (0..999999), so "42" and "forty-two" count as the same words. */
export function numberWords(n) {
  if (n < 20) return ONES[n];
  if (n < 100) return TENS[Math.floor(n / 10)] + (n % 10 ? ` ${ONES[n % 10]}` : '');
  if (n < 1000) return `${ONES[Math.floor(n / 100)]} hundred${n % 100 ? ` ${numberWords(n % 100)}` : ''}`;
  return `${numberWords(Math.floor(n / 1000))} thousand${n % 1000 ? ` ${numberWords(n % 1000)}` : ''}`;
}

/** Text -> the list of words that are compared: lower case, no punctuation, numbers spelled out. */
export function words(text) {
  return text.toLowerCase()
    .replace(/(\d),(\d{3})/g, '$1$2')
    .replace(/\b\d{1,6}\b/g, (n) => numberWords(Number(n)))
    .replace(/%/g, ' percent ')
    .replace(/\band\b/g, ' ')                 // "one hundred and five" = "one hundred five"
    .replace(/[^a-z0-9' ]+/g, ' ')
    .replace(/'/g, '')
    .split(/\s+/).filter(Boolean);
}

/**
 * Word error rate: the fewest words to change, add or remove to turn `heard` into
 * `reference`, divided by the words in `reference`.
 * @returns {{ errors: number, words: number, rate: number }}
 */
export function wordErrors(reference, heard) {
  const r = words(reference);
  const h = words(heard);
  let row = Array.from({ length: h.length + 1 }, (_, j) => j);
  for (let i = 1; i <= r.length; i++) {
    const next = [i];
    for (let j = 1; j <= h.length; j++) next[j] = Math.min(row[j] + 1, next[j - 1] + 1, row[j - 1] + (r[i - 1] === h[j - 1] ? 0 : 1));
    row = next;
  }
  const errors = row[h.length];
  return { errors, words: r.length, rate: errors / Math.max(1, r.length) };
}

// ---- test material made with the macOS `say` program

/** What the computer voices read: everyday talk, numbers, names, a long passage, short commands. */
export const PASSAGES = [
  { name: 'pangram', text: 'The quick brown fox jumps over the lazy dog, and the five boxing wizards jump quickly.' },
  { name: 'band', text: 'Our project uses the wristband to count how many times you raise your hand. When you tap it twice, the light turns blue and the page shows a new question.' },
  { name: 'numbers', text: 'The meeting starts at 9 30 on Thursday. We need 42 chairs, 7 tables and about 150 cups. The room number is 318 on the third floor.' },
  { name: 'names', text: 'Maria and David are working with Sophie from Berlin. Yesterday they called Michael in London and asked him about the train to Paris.' },
  { name: 'commands', text: 'Turn on the light. Make it green. Stop the timer. Start recording. What is the temperature?' },
  { name: 'story', text: 'When the sun came up over the hills, the small town was already awake. The baker opened his shop, the children walked to school, '
    + 'and an old man sat on a bench by the river feeding the birds. Nobody noticed the stranger who arrived on the morning bus with a heavy suitcase and a map.' },
  { name: 'science', text: 'Sound is a wave that moves through the air. A microphone turns that wave into an electrical signal, and a computer measures it '
    + 'sixteen thousand times every second. Speech recognition then guesses which words were most likely spoken.' },
  { name: 'chat', text: 'Okay, so I think we should try it again. Can you hear me now? I am going to walk to the other side of the room and keep talking.' },
];

/** Voices and speaking rates (words per minute). The passages take turns with them. */
export const VOICES = [
  { name: 'Samantha', rate: 165 }, { name: 'Daniel', rate: 180 }, { name: 'Karen', rate: 150 }, { name: 'Rishi', rate: 170 },
  { name: 'Fred', rate: 160 }, { name: 'Kathy', rate: 200 }, { name: 'Ralph', rate: 165 }, { name: 'Samantha', rate: 210 },
];

/**
 * How loud the voice and the background hiss are, in dB below full scale (RMS over the
 * whole voice file, pauses included). "near" is what the bench measured from a real band
 * at its default gain with a voice from a laptop speaker (voice -32, background -50);
 * "far" is the same background with the voice 12 dB quieter, like someone across the table.
 */
export const LEVELS = [
  { name: 'near', voiceDb: -32, noiseDb: -50 },
  { name: 'far', voiceDb: -44, noiseDb: -50 },
];

/** A small repeatable random number generator (the same seed gives the same hiss). */
function random(seed) {
  let s = seed >>> 0;
  return () => {
    s = (s + 0x6d2b79f5) >>> 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/**
 * A clean voice -> a voice at the band's level with background hiss, with 0.5 s of
 * background in front and 1 s after.
 * @param {Int16Array} pcm 16000 samples per second
 * @returns {Int16Array}
 */
export function mixAtLevel(pcm, { voiceDb, noiseDb }, seed = 1) {
  let sum = 0;
  for (const v of pcm) sum += v * v;
  const rms = Math.sqrt(sum / Math.max(1, pcm.length)) / 32768;
  const gain = rms > 0 ? 10 ** (voiceDb / 20) / rms : 0;
  const noise = 10 ** (noiseDb / 20) * 32768;
  const next = random(seed);
  const before = 8000;
  const out = new Int16Array(before + pcm.length + 16000);
  let smooth = 0;
  for (let i = 0; i < out.length; i++) {
    // Two random numbers make a bell-shaped one; a little smoothing makes the hiss less sharp, like a microphone's.
    const bell = Math.sqrt(-2 * Math.log(1 - next())) * Math.cos(2 * Math.PI * next());
    smooth = 0.5 * smooth + bell;
    const voice = i >= before && i < before + pcm.length ? pcm[i - before] * gain : 0;
    out[i] = Math.max(-32768, Math.min(32767, Math.round(voice + smooth * noise * 0.866)));
  }
  return out;
}

/** The samples of a 16 bit mono .wav file. */
export function readWav(bytes) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const tag = (at) => String.fromCharCode(bytes[at], bytes[at + 1], bytes[at + 2], bytes[at + 3]);
  if (tag(0) !== 'RIFF' || tag(8) !== 'WAVE') throw new Error('This is not a .wav file.');
  let at = 12;
  let format = null;
  while (at + 8 <= bytes.length) {
    const size = view.getUint32(at + 4, true);
    if (tag(at) === 'fmt ') format = { channels: view.getUint16(at + 10, true), rate: view.getUint32(at + 12, true), bits: view.getUint16(at + 22, true) };
    if (tag(at) === 'data') {
      if (!format || format.channels !== 1 || format.rate !== 16000 || format.bits !== 16) throw new Error('The .wav file must be 16000 samples per second, 16 bit, mono.');
      const count = Math.min(size, bytes.length - at - 8) >> 1;
      const pcm = new Int16Array(count);
      for (let i = 0; i < count; i++) pcm[i] = view.getInt16(at + 8 + 2 * i, true);
      return pcm;
    }
    at += 8 + size + (size & 1);
  }
  throw new Error('This .wav file has no sound in it.');
}

/** Samples -> a 16 bit mono .wav file at 16000 samples per second. */
export function writeWav(pcm) {
  const bytes = new Uint8Array(44 + pcm.length * 2);
  const view = new DataView(bytes.buffer);
  const put = (at, text) => [...text].forEach((c, i) => view.setUint8(at + i, c.charCodeAt(0)));
  put(0, 'RIFF');
  view.setUint32(4, 36 + pcm.length * 2, true);
  put(8, 'WAVEfmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, 16000, true);
  view.setUint32(28, 32000, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  put(36, 'data');
  view.setUint32(40, pcm.length * 2, true);
  pcm.forEach((v, i) => view.setInt16(44 + 2 * i, v, true));
  return bytes;
}
