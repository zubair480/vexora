// Private illustration for ActiveToolCard: a cordless impact wrench drawn with token colours.
import { motion, useReducedMotion } from 'framer-motion';

export function ImpactWrenchArt({ active = false, className }: { active?: boolean; className?: string }) {
  const reduce = useReducedMotion();
  const vibrate = active && !reduce;

  return (
    <svg viewBox="0 0 96 96" className={className} role="img" aria-label="Impact wrench illustration">
      {/* soft ground shadow */}
      <ellipse cx="48" cy="86" rx="28" ry="4" fill="rgb(var(--brand))" opacity={0.15} />

      <motion.g
        animate={vibrate ? { x: [0, -0.6, 0.6, -0.4, 0] } : { x: 0 }}
        transition={vibrate ? { duration: 0.22, repeat: Infinity, ease: 'linear' } : { duration: 0.2 }}
      >
        {/* anvil + socket */}
        <rect x="8" y="27" width="9" height="10" rx="2" fill="rgb(var(--ink-3))" />
        <rect x="4" y="29.5" width="5" height="5" rx="1" fill="rgb(var(--ink-3))" opacity={0.8} />
        <rect x="15" y="24" width="7" height="16" rx="2" fill="rgb(var(--ink))" opacity={0.85} />

        {/* housing / motor body */}
        <path
          d="M20 22c0-3.3 2.7-6 6-6h34c6.6 0 12 5.4 12 12v2c0 6.6-5.4 12-12 12H26c-3.3 0-6-2.7-6-6Z"
          fill="rgb(var(--warn))"
        />
        {/* vent slots */}
        <g stroke="rgb(var(--brand))" strokeWidth={1.6} strokeLinecap="round" opacity={0.7}>
          <path d="M58 23v10" />
          <path d="M62 23v10" />
          <path d="M66 24v8" />
        </g>
        {/* top highlight */}
        <path d="M27 20h31c3.5 0 6.6 1.6 8.6 4.2H27Z" fill="rgb(var(--surface))" opacity={0.5} />

        {/* grip */}
        <path d="M38 42h17l-3 26H41Z" fill="rgb(var(--ink))" opacity={0.85} />
        <g stroke="rgb(var(--surface))" strokeWidth={1} opacity={0.18}>
          <path d="M41 50h11" />
          <path d="M41 55h10.5" />
          <path d="M41.5 60h10" />
        </g>

        {/* trigger */}
        <path d="M33.5 43h5v8.5c-2.8 0-5-2.2-5-5Z" fill="rgb(var(--brand))" />

        {/* battery pack */}
        <rect x="32" y="66" width="30" height="8" rx="2.5" fill="rgb(var(--warn))" />
        <rect x="30" y="72" width="34" height="9" rx="3" fill="rgb(var(--ink))" opacity={0.85} />
        <rect x="34" y="74.5" width="8" height="2" rx="1" fill="rgb(var(--ok))" />
        <rect x="34" y="67.5" width="20" height="1.6" rx="0.8" fill="rgb(var(--surface))" opacity={0.5} />
      </motion.g>
    </svg>
  );
}
