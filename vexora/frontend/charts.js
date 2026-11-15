// Canvas charts for BeltGuard (design v2, shadcn zinc): live strip charts and the rail sparkline.
// No chart library — ring buffers drawn on requestAnimationFrame.

const dpr = () => Math.min(window.devicePixelRatio || 1, 2);
const MONO = '"Geist Mono", ui-monospace, Consolas, monospace';
const GRID = '#F4F4F5';
const AXIS = '#E4E4E7';
const LABEL = '#71717A';

function fitCanvas(canvas) {
  const r = canvas.getBoundingClientRect();
  const d = dpr();
  const w = Math.max(1, Math.round(r.width * d));
  const h = Math.max(1, Math.round(r.height * d));
  if (canvas.width !== w) canvas.width = w;
  if (canvas.height !== h) canvas.height = h;
  return d;
}

const isShown = (c) => c.offsetParent !== null && c.clientWidth > 0 && c.clientHeight > 0;

export class StripChart {
  constructor(canvas, { colors, minRange = 1, seconds = 8 } = {}) {
    this.c = canvas;
    this.g = canvas.getContext('2d');
    this.colors = colors;
    this.minRange = minRange;
    this.range = minRange;
    this.seconds = seconds;
    this.cap = 8192;
    this.buf = [new Float32Array(this.cap), new Float32Array(this.cap), new Float32Array(this.cap)];
    this.head = 0;
    this.count = 0;
    this.visible = 400;
    this.d = fitCanvas(canvas);
    new ResizeObserver(() => { this.d = fitCanvas(canvas); }).observe(canvas);
  }

  setRate(hz) {
    this.visible = Math.max(100, Math.min(this.cap, Math.round((hz || 50) * this.seconds)));
  }

  push(v) {
    if (!v) return;
    const i = this.head;
    this.buf[0][i] = +v[0] || 0;
    this.buf[1][i] = +v[1] || 0;
    this.buf[2][i] = +v[2] || 0;
    this.head = (i + 1) % this.cap;
    if (this.count < this.cap) this.count += 1;
  }

  clear() { this.head = 0; this.count = 0; }

  draw() {
    if (!isShown(this.c)) return;           // hidden tab: skip the work
    this.d = fitCanvas(this.c);
    const { g, c, d } = this;
    const W = c.width, H = c.height;
    g.clearRect(0, 0, W, H);
    const n = Math.min(this.count, this.visible);

    let m = 0;
    for (let k = 0; k < 3; k += 1) {
      const b = this.buf[k];
      for (let j = 0; j < n; j += 1) {
        const v = Math.abs(b[(this.head - 1 - j + this.cap) % this.cap]);
        if (v > m) m = v;
      }
    }
    const target = Math.max(this.minRange, m * 1.12);
    this.range += (target - this.range) * (target > this.range ? 0.3 : 0.02);

    const padL = 40 * d, padR = 2 * d, top = 2 * d, bot = 2 * d;
    const pw = W - padL - padR, ph = H - top - bot, mid = top + ph / 2;

    g.lineWidth = 1;
    g.strokeStyle = GRID;
    g.beginPath();
    for (let i = 0; i <= 8; i += 1) {
      const x = Math.round(padL + (i * pw) / 8) + 0.5;
      g.moveTo(x, top); g.lineTo(x, top + ph);
    }
    g.moveTo(padL, Math.round(top) + 0.5); g.lineTo(padL + pw, Math.round(top) + 0.5);
    g.moveTo(padL, Math.round(top + ph) - 0.5); g.lineTo(padL + pw, Math.round(top + ph) - 0.5);
    g.stroke();
    g.strokeStyle = AXIS;
    g.beginPath(); g.moveTo(padL, Math.round(mid) + 0.5); g.lineTo(padL + pw, Math.round(mid) + 0.5); g.stroke();

    const r = this.range;
    const f = (v) => (v < 10 ? v.toFixed(1) : v.toFixed(0));
    g.fillStyle = LABEL;
    g.font = `${11 * d}px ${MONO}`;
    g.textAlign = 'right';
    g.textBaseline = 'middle';
    g.fillText(`+${f(r)}`, padL - 8 * d, top + 6 * d);
    g.fillText('0', padL - 8 * d, mid);
    g.fillText(`−${f(r)}`, padL - 8 * d, top + ph - 6 * d);

    if (n > 1) {
      const step = pw / (this.visible - 1);
      g.lineWidth = 1.5 * d;
      g.lineJoin = 'round';
      for (let k = 2; k >= 0; k -= 1) {
        const b = this.buf[k];
        g.strokeStyle = this.colors[k];
        g.beginPath();
        for (let j = 0; j < n; j += 1) {
          const v = b[(this.head - n + j + this.cap) % this.cap];
          const x = padL + pw - (n - 1 - j) * step;
          const y = mid - (Math.max(-r, Math.min(r, v)) / r) * (ph / 2);
          if (j === 0) g.moveTo(x, y); else g.lineTo(x, y);
        }
        g.stroke();
      }
    }
  }
}

// One-series sparkline: dynamic part of the wrist acceleration magnitude (|a| minus its running mean), in g.
export class Sparkline {
  constructor(canvas, { seconds = 10, minRange = 0.05 } = {}) {
    this.c = canvas;
    this.g = canvas.getContext('2d');
    this.seconds = seconds;
    this.minRange = minRange;
    this.range = minRange;
    this.cap = 8192;
    this.buf = new Float32Array(this.cap);
    this.head = 0;
    this.count = 0;
    this.visible = 500;
    this.mean = 1;
    this.color = '#18181B';
    this.d = fitCanvas(canvas);
    new ResizeObserver(() => { this.d = fitCanvas(canvas); }).observe(canvas);
  }

  setRate(hz) { this.visible = Math.max(100, Math.min(this.cap, Math.round((hz || 50) * this.seconds))); }
  setColor(c) { this.color = c; }
  clear() { this.head = 0; this.count = 0; }

  push(a) {
    if (!a) return;
    const mag = Math.hypot(+a[0] || 0, +a[1] || 0, +a[2] || 0);
    this.mean += (mag - this.mean) * 0.02;
    this.buf[this.head] = mag - this.mean;
    this.head = (this.head + 1) % this.cap;
    if (this.count < this.cap) this.count += 1;
  }

  draw() {
    if (!isShown(this.c)) return;
    this.d = fitCanvas(this.c);
    const { g, c, d } = this;
    const W = c.width, H = c.height;
    g.clearRect(0, 0, W, H);
    const n = Math.min(this.count, this.visible);
    let m = 0;
    for (let j = 0; j < n; j += 1) {
      const v = Math.abs(this.buf[(this.head - 1 - j + this.cap) % this.cap]);
      if (v > m) m = v;
    }
    const target = Math.max(this.minRange, m * 1.15);
    this.range += (target - this.range) * (target > this.range ? 0.3 : 0.03);
    const mid = H / 2;
    g.strokeStyle = AXIS;
    g.lineWidth = 1;
    g.setLineDash([3 * d, 3 * d]);
    g.beginPath(); g.moveTo(0, Math.round(mid) + 0.5); g.lineTo(W, Math.round(mid) + 0.5); g.stroke();
    g.setLineDash([]);
    if (n < 2) return;
    const step = W / (this.visible - 1);
    const r = this.range;
    g.beginPath();
    for (let j = 0; j < n; j += 1) {
      const v = this.buf[(this.head - n + j + this.cap) % this.cap];
      const x = W - (n - 1 - j) * step;
      const y = mid - (Math.max(-r, Math.min(r, v)) / r) * (H / 2 - 2 * d);
      if (j === 0) g.moveTo(x, y); else g.lineTo(x, y);
    }
    g.strokeStyle = this.color;
    g.lineWidth = 1.5 * d;
    g.lineJoin = 'round';
    g.stroke();
  }
}
