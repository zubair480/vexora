// Shared by the examples in this folder. It reads the command line, the same way as the
// Python examples do (typed in my-project/):
//   node vexo-open-kit/examples/node/hello.mjs 42                 42 = your band number
//   node vexo-open-kit/examples/node/hello.mjs 42 --seconds 10    stop after 10 seconds
//   node vexo-open-kit/examples/node/hello.mjs                    no number: band 42
// Node has no Web Bluetooth and no USB port for the library, so these examples always run
// with a pretend band. The number only gives the pretend band its name. A wrong argument
// ends the example with one sentence that says what to type.
import { relative } from 'node:path';
import { codec, NAME_PREFIX } from '../../js/src/index.js';

/** "42", "07" or the full name "Vexo-HACK-42" -> 42. Anything else: an Error that says what to type. */
export function bandNumber(text) {
  const typed = text.trim();
  const name = typed.toLowerCase().startsWith(NAME_PREFIX.toLowerCase()) ? NAME_PREFIX + typed.slice(NAME_PREFIX.length) : typed;
  const number = codec.parseBandNumber(typed) ?? codec.bandNumberOf(name);
  if (number === 0 || /^0{1,3}$/.test(name.replace(NAME_PREFIX, ''))) {
    throw new Error('Band numbers are 1 to 999. 0 is what a band has before it gets a number: ask an organiser to number it.');
  }
  if (number === null) {
    throw new Error(`"${typed}" is not a band number. Type just the number on your band or its bag, for example 42.`);
  }
  return number;
}

/** "10" or "0.5" -> the number. Anything else: an Error that says what to type. */
export function secondsOf(text) {
  const seconds = /^\d+(\.\d+)?$/.test(text ?? '') ? Number(text) : NaN;
  if (!(seconds >= 0 && seconds <= 3600)) {
    throw new Error(`--seconds needs a number of seconds from 0 to 3600, for example --seconds 10${text === undefined ? '' : `, not "${text}"`}.`);
  }
  return seconds;
}

/**
 * What was typed after the program name, as { number, seconds }.
 * words: the arguments (process.argv.slice(2)). seconds: how long the example runs if
 * --seconds is not given. Throws an Error with a sentence for the student.
 */
export function read(words, seconds) {
  const found = { number: 42, seconds };
  let numbers = 0;
  for (let i = 0; i < words.length; i++) {
    const word = words[i];
    if (word === '--seconds') found.seconds = secondsOf(words[++i]);
    else if (word.startsWith('--seconds=')) found.seconds = secondsOf(word.slice('--seconds='.length));
    else if (word === '--sim') continue;       // the Python examples need it; here the band is always a pretend one
    else if (word === '--usb') {
      throw new Error('Node cannot use the USB cable (or Bluetooth), so this example only runs with the pretend band. '
        + 'For a real band use the Python examples (examples/python) or the web pages (examples/web).');
    } else if (word.startsWith('--')) {
      throw new Error(`"${word}" is not an option of this example. The only one is --seconds, for example --seconds 10.`);
    } else if (++numbers > 1) {
      throw new Error(`"${word}" is one argument too many. Type the band number first, then --seconds for the time, for example 42 --seconds 10.`);
    } else found.number = bandNumber(word);
  }
  return found;
}

/** The same for an example: on a wrong argument it prints the sentence and how to type the command, and ends. */
export function args({ seconds }) {
  try {
    return read(process.argv.slice(2), seconds);
  } catch (problem) {
    const program = relative(process.cwd(), process.argv[1]) || process.argv[1];     // the path as it was typed
    console.error(`${problem.message}\nType:   node ${program} 42   or   node ${program} 42 --seconds 10`);
    process.exit(2);
  }
}
