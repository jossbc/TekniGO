import { useId } from 'react'
import fullLogo from '../assets/brand/logo-original.png'
import symbol from '../assets/brand/isotipo-original.png'

/** Original PNG geometry and alpha; only transparent margins and RGB are changed. */
export default function Logo({ variant = 'full', className = '' }: { variant?: 'full' | 'symbol'; className?: string }) {
  const filterId = `brand-palette-${useId().replace(/:/g, '')}`
  const full = variant === 'full'
  // Source reds are 18/25 (blue) and 242 (orange). Classify before mapping
  // to the existing --blue #2563eb and --orange #ea580c in sRGB.
  const blue = [37, 99, 235]
  const orange = [234, 88, 12]
  const matrix = blue.map((channel, i) => `${(orange[i] - channel) / 255} 0 0 0 ${channel / 255}`).join(' ')

  return <svg className={`brand-logo brand-logo--${variant} ${className}`} viewBox={full ? '136 694 1776 660' : '363 512 1322 1024'} aria-hidden="true" focusable="false">
    <defs>
      <filter id={filterId} x="0" y="0" width="100%" height="100%" colorInterpolationFilters="sRGB">
        <feComponentTransfer><feFuncR type="discrete" tableValues="0 1" /></feComponentTransfer>
        <feColorMatrix type="matrix" values={`${matrix} 0 0 0 1 0`} />
      </filter>
    </defs>
    <image href={full ? fullLogo : symbol} width="2048" height="2048" filter={`url(#${filterId})`} />
  </svg>
}
