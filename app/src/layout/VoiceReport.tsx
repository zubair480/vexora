// "Report defect": speak through the band's microphone, see the words, send them to the BeltGuard
// server (POST /api/inspection/report), which turns them into a defect and a draft work order.
// Speech to text uses the kit dashboard's own Deepgram link with the team's key, typed here and
// kept in memory only. Without a band or a key, type the report instead.
import { Mic, MicOff, Send, X } from 'lucide-react';
import { useRef, useState } from 'react';
import { deepgramLink, type DeepgramLink } from 'vexo-deepgram';
import { API } from '../api/useShift';
import type { VexoBand } from '../band/useVexoBand';

const PROBLEMS: Record<string, string> = {
  'no-key': 'Paste your team\'s Deepgram key first.',
  refused: 'Deepgram refused the connection: check the key and the internet.',
  closed: 'Deepgram closed the connection.',
  reported: 'Deepgram reported a problem with the sound.',
  'no-socket': 'This browser cannot open the connection.',
};

export function VoiceReport({ vexo }: { vexo: VexoBand }) {
  const [open, setOpen] = useState(false);
  const [key, setKey] = useState('');
  const [text, setText] = useState('');
  const [interim, setInterim] = useState('');
  const [listening, setListening] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const link = useRef<DeepgramLink | null>(null);
  const stopMic = useRef<(() => Promise<void>) | null>(null);
  const bandOn = vexo.phase === 'connected';

  async function start() {
    setStatus(null);
    const l = deepgramLink({ key: key.trim() });
    link.current = l;
    l.start({
      onText: ({ final, text: t }) => {
        if (final) {
          setText((prev) => (prev ? `${prev} ${t}` : t).trim());
          setInterim('');
        } else setInterim(t);
      },
      onError: (p) => {
        setStatus(PROBLEMS[p] ?? `Speech stopped (${p}).`);
        void stop();
      },
    });
    try {
      stopMic.current = await vexo.listen((opus) => l.packet(opus));
      setListening(true);
    } catch (e) {
      l.stop();
      setStatus(e instanceof Error ? e.message : String(e));
    }
  }

  async function stop() {
    setListening(false);
    link.current?.stop();
    link.current = null;
    const s = stopMic.current;
    stopMic.current = null;
    if (s) await s();
  }

  async function send() {
    const words = text.trim();
    if (!words) return;
    setSending(true);
    setStatus(null);
    try {
      const r = await fetch(API + '/api/inspection/report', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: words, station_hint: 'S3' }),
      });
      const body = await r.json().catch(() => null);
      if (r.ok) {
        setStatus('Sent. The defect and draft work order appear on the dashboard in a few seconds.');
        setText('');
      } else {
        const msg = body?.detail?.message ?? body?.detail ?? `status ${r.status}`;
        setStatus(`The server did not record it: ${typeof msg === 'string' ? msg : JSON.stringify(msg)}`);
      }
    } catch {
      setStatus('The BeltGuard server is not running (port 8010).');
    } finally {
      setSending(false);
    }
  }

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="flex items-center gap-2 rounded-xl border border-line bg-surface px-3 py-2 text-sm font-medium text-ink shadow-card hover:bg-panel"
      >
        <Mic size={16} className="text-bad" /> Report defect
      </button>
    );
  }

  return (
    <div className="absolute right-0 top-12 z-30 w-[380px] rounded-card border border-line bg-surface p-4 shadow-lift">
      <div className="flex items-center justify-between">
        <h3 className="card-title">Report a defect</h3>
        <button type="button" aria-label="Close" onClick={() => { void stop(); setOpen(false); }} className="rounded p-1 text-ink-3 hover:bg-panel">
          <X size={16} />
        </button>
      </div>

      {bandOn ? (
        <div className="mt-3 space-y-2">
          <input
            type="password"
            autoComplete="off"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            placeholder="Deepgram key (kept in this page only)"
            className="w-full rounded-lg border border-line px-3 py-2 text-sm outline-none focus:border-info"
          />
          <button
            type="button"
            onClick={() => void (listening ? stop() : start())}
            disabled={!key.trim() && !listening}
            className={`flex w-full items-center justify-center gap-2 rounded-lg px-3 py-2 text-sm font-semibold text-white disabled:opacity-50 ${listening ? 'bg-bad' : 'bg-info'}`}
          >
            {listening ? <><MicOff size={16} /> Stop listening</> : <><Mic size={16} /> Speak through the band</>}
          </button>
          {listening && (
            <p className="text-xs text-ink-2">
              Microphone on (the band's light is solid blue). The sound goes to Deepgram: tell the people near you.
            </p>
          )}
        </div>
      ) : (
        <p className="mt-2 text-xs text-ink-2">Connect the band to speak through it, or type the report below.</p>
      )}

      <textarea
        value={interim ? `${text} ${interim}`.trim() : text}
        onChange={(e) => { setText(e.target.value); setInterim(''); }}
        rows={3}
        placeholder="e.g. Station 3, return idler 14, bearing seized. Needs replacement."
        className="mt-3 w-full resize-none rounded-lg border border-line px-3 py-2 text-sm outline-none focus:border-info"
      />
      <button
        type="button"
        onClick={() => void send()}
        disabled={!text.trim() || sending || listening}
        className="mt-2 flex w-full items-center justify-center gap-2 rounded-lg bg-brand px-3 py-2 text-sm font-semibold text-white disabled:opacity-50"
      >
        <Send size={15} /> {sending ? 'Sending…' : 'Send to BeltGuard'}
      </button>
      {status && <p className="mt-2 text-xs text-ink-2">{status}</p>}
    </div>
  );
}
