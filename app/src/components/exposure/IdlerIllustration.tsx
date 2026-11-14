// Small decorative thumbnail of a return idler (roller) on its bracket, used by RecommendationCard.
// Mirrors the reference: an orange-red roller (the defective one) sitting in a green frame.
// Pure SVG with token classes only, so it follows the theme and needs no image asset.
import clsx from 'clsx';

export function IdlerIllustration({ size = 88, className }: { size?: number; className?: string }) {
  return (
    <svg
      viewBox="0 0 88 88"
      width={size}
      height={size}
      aria-hidden="true"
      focusable="false"
      className={clsx('block', className)}
    >
      {/* soft ground shadow */}
      <ellipse cx="44" cy="74" rx="32" ry="5" className="fill-ink/10" />

      {/* whole assembly tilted for a three-quarter view */}
      <g transform="rotate(-16 44 46)">
        {/* bracket frame: base rail + two uprights */}
        <rect x="8" y="62" width="72" height="6" rx="2" className="fill-ok/60" />
        <rect x="9" y="34" width="7" height="31" rx="2" className="fill-ok/60" />
        <rect x="72" y="34" width="7" height="31" rx="2" className="fill-ok/60" />
        <rect x="9" y="34" width="7" height="4" rx="1.5" className="fill-ok" />
        <rect x="72" y="34" width="7" height="4" rx="1.5" className="fill-ok" />

        {/* shaft through both bearings */}
        <rect x="11" y="42.5" width="66" height="3" rx="1.5" className="fill-ink-3" />

        {/* far end cap (mostly hidden) */}
        <ellipse cx="22" cy="44" rx="5" ry="13" className="fill-bad/70" />

        {/* roller body */}
        <rect x="22" y="31" width="44" height="26" className="fill-bad/70" />
        {/* top highlight band and lower shade for a cylindrical read */}
        <rect x="22" y="34" width="44" height="4" rx="2" className="fill-bad-bg/80" />
        <rect x="22" y="40" width="44" height="1.5" className="fill-bad-bg/40" />
        <rect x="22" y="51" width="44" height="6" className="fill-bad/30" />
        {/* wear marks */}
        <path d="M38 47 l4 -2 M46 49 l3 -1.5" strokeWidth="1.2" strokeLinecap="round" className="stroke-bad-bg/70" fill="none" />

        {/* near end cap with bearing hub */}
        <ellipse cx="66" cy="44" rx="5.5" ry="13" className="fill-bad" />
        <ellipse cx="66" cy="44" rx="4" ry="10" className="fill-bad-bg" />
        <ellipse cx="66" cy="44" rx="2.6" ry="5" className="fill-ink-3" />
        <ellipse cx="66" cy="44" rx="1.2" ry="2.2" className="fill-ink-2" />
      </g>
    </svg>
  );
}
