# Band contract

- [`contract.md`](contract.md): every command you can send to a Vexo Open band and every
  kind of data it sends back, byte by byte. Event bands run firmware 1.1.0, which
  speaks this contract (v1.1).
- [`test-vectors.json`](test-vectors.json): example frames with their meaning. The
  JavaScript and Python libraries are both tested against this one file, so they cannot
  drift apart. If you write a client in another language, test it against the same file.

The only code that knows the byte format is `js/src/codec.js` and
`python/vexo_open/codec.py`.
