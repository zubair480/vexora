// Tests for the arithmetic of the accuracy check (dashboard/test/accuracy-lib.mjs):
// counting wrong words, and making test voices. No browser, no speech model.
import test from 'node:test';
import assert from 'node:assert/strict';
import { words, wordErrors, numberWords, PASSAGES, VOICES, LEVELS, mixAtLevel, readWav, writeWav } from './accuracy-lib.mjs';

test('words: case, punctuation and the way a number is written do not count as mistakes', () => {
  assert.deepEqual(words('The Quick-Brown fox, it\'s here!'), ['the', 'quick', 'brown', 'fox', 'its', 'here']);
  assert.deepEqual(words('We need 42 chairs and 150 cups.'), words('we need forty-two chairs, one hundred and fifty cups'));
  assert.deepEqual(words('room 318'), ['room', 'three', 'hundred', 'eighteen']);
  assert.deepEqual(words('16,000 times'), words('sixteen thousand times'));
  assert.equal(numberWords(0), 'zero');
  assert.equal(numberWords(2026), 'two thousand twenty six');
});

test('wordErrors: wrong, missing and extra words each count once, against the words in the reference', () => {
  assert.deepEqual(wordErrors('the quick brown fox', 'The quick brown fox.'), { errors: 0, words: 4, rate: 0 });
  assert.equal(wordErrors('the quick brown fox', 'the quick round fox').errors, 1);
  assert.equal(wordErrors('the quick brown fox', 'the quick fox').errors, 1);
  assert.equal(wordErrors('the quick brown fox', 'the very quick brown fox').errors, 1);
  assert.deepEqual(wordErrors('the quick brown fox', ''), { errors: 4, words: 4, rate: 1 });
  assert.equal(wordErrors('one two', 'a b c d e').rate, 2.5, 'more extra words than the reference has: over 100%');
});

test('the test passages cover everyday talk, numbers, names, commands and a long text, with several voices', () => {
  assert.ok(PASSAGES.length >= 8 && VOICES.length >= 6);
  assert.equal(new Set(PASSAGES.map((p) => p.name)).size, PASSAGES.length);
  assert.ok(PASSAGES.some((p) => /\d/.test(p.text)) && PASSAGES.some((p) => words(p.text).length > 50));
  assert.ok(new Set(VOICES.map((v) => v.rate)).size >= 5, 'several speaking rates');
});

test('mixAtLevel: the voice and the hiss come out at the levels asked for, the same every time', () => {
  const voice = Int16Array.from({ length: 32000 }, (_, i) => Math.round(8000 * Math.sin(i / 5)));
  const db = (pcm) => {
    let sum = 0;
    for (const v of pcm) sum += (v / 32768) ** 2;
    return 10 * Math.log10(sum / pcm.length);
  };
  for (const level of LEVELS) {
    const out = mixAtLevel(voice, level, 7);
    assert.equal(out.length, 8000 + voice.length + 16000, 'half a second of room before, one second after');
    assert.ok(Math.abs(db(out.subarray(0, 8000)) - level.noiseDb) < 1, `hiss ${db(out.subarray(0, 8000))}`);
    const middle = out.subarray(8000, 8000 + voice.length);
    const expected = 10 * Math.log10(10 ** (level.voiceDb / 10) + 10 ** (level.noiseDb / 10));
    assert.ok(Math.abs(db(middle) - expected) < 1, `voice ${db(middle)}`);
    assert.deepEqual(mixAtLevel(voice, level, 7), out);
  }
  assert.notDeepEqual(mixAtLevel(voice, LEVELS[0], 8), mixAtLevel(voice, LEVELS[0], 7));
});

test('a .wav file written is read back the same; another kind of file is refused with a reason', () => {
  const pcm = Int16Array.of(0, 1, -1, 32767, -32768, 1234);
  assert.deepEqual(readWav(writeWav(pcm)), pcm);
  assert.throws(() => readWav(new Uint8Array(64)), /not a \.wav file/);
  const stereo = writeWav(pcm);
  stereo[22] = 2;
  assert.throws(() => readWav(stereo), /16000 samples per second, 16 bit, mono/);
});
