"use client";

/**
 * The Convoy mark: the O in CONVOY is a wheel.
 *
 * Earlier versions set a tyre beneath the word or behind it. Both read as a
 * logo with a picture near it, because the halves stayed separable — remove
 * the wheel and a wordmark survives. Substituting a LETTER makes them one
 * thing: the mark cannot be taken apart without breaking the word.
 *
 * It also survives being small. A tyre under text turns to mud at header size,
 * while a letter-sized wheel stays legible because it is exactly as large as
 * the type around it.
 *
 * One component, two sizes, so the header and the front page cannot drift
 * apart — that particular inconsistency is what makes a product feel assembled
 * rather than designed.
 */

interface Props {
  /** Cap height of the lettering, in px. */
  size?: number;
  /** Colour showing through the tread cut-outs. Must match what sits behind. */
  cut?: string;
  tagline?: boolean;
}

export function ConvoyLogo({ size = 76, cut = "var(--concrete)", tagline = false }: Props) {
  const letter: React.CSSProperties = {
    fontSize: size,
    letterSpacing: "0.05em",
    lineHeight: 1,
  };
  const wheel = size * 0.84;
  const treads = size > 40 ? 18 : 12;

  return (
    <span className="inline-flex flex-col items-center">
      <span className="flex items-center" style={{ gap: size * 0.015 }}>
        <span className="font-display font-bold" style={letter}>C</span>

        <svg
          width={wheel}
          height={wheel}
          viewBox="0 0 100 100"
          aria-hidden
          style={{ marginBottom: size * 0.045 }}
        >
          {/* Tyre */}
          <circle cx="50" cy="50" r="41" fill="none" stroke="currentColor"
                  strokeWidth={size > 40 ? 17 : 18} />
          {/* Tread, cut through the band in whatever sits behind the mark */}
          {Array.from({ length: treads }, (_, n) => {
            const a = ((n * 360) / treads) * (Math.PI / 180);
            return (
              <line
                key={n}
                x1={50 + 33 * Math.cos(a)}
                y1={50 + 33 * Math.sin(a)}
                x2={50 + 50 * Math.cos(a)}
                y2={50 + 50 * Math.sin(a)}
                stroke={cut}
                strokeWidth={size > 40 ? 4.6 : 6}
              />
            );
          })}
          {/* Rim */}
          <circle cx="50" cy="50" r={size > 40 ? 23 : 22} fill="none"
                  stroke="currentColor" strokeWidth={size > 40 ? 6 : 7} />
          {/* Spokes only at display size: at 19px they collapse into a blob */}
          {size > 40 &&
            [0, 36, 72, 108, 144].map((d) => {
              const a = (d * Math.PI) / 180;
              return (
                <line key={d}
                      x1={50 + 20 * Math.cos(a)} y1={50 + 20 * Math.sin(a)}
                      x2={50 - 20 * Math.cos(a)} y2={50 - 20 * Math.sin(a)}
                      stroke="currentColor" strokeWidth="4.4" />
              );
            })}
          <circle cx="50" cy="50" r={size > 40 ? 7.5 : 9} fill="currentColor" />
        </svg>

        <span className="font-display font-bold" style={letter}>NVOY</span>
      </span>

      {tagline && (
        <span
          className="font-mono text-ink-mute"
          style={{
            marginTop: size * 0.16,
            fontSize: Math.max(9, size * 0.13),
            letterSpacing: "0.32em",
          }}
        >
          SMARTER DATA · BETTER WORLD
        </span>
      )}
    </span>
  );
}
