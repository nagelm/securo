import { useState } from 'react'
import { CategoryIcon } from '@/components/category-icon'
import { cn } from '@/lib/utils'

const BOX = {
  xs: 'w-4 h-4 rounded',
  sm: 'w-6 h-6 rounded-md',
  md: 'w-8 h-8 rounded-lg',
  lg: 'w-10 h-10 rounded-xl',
  xl: 'w-11 h-11 rounded-xl',
} as const

interface PayeeIconProps {
  /** The icon the user picked for the payee. Wins over the logo. */
  icon?: string | null
  /** The payee's logo URL. */
  logoUrl?: string | null
  /** What to draw when the payee has neither: usually the category's icon. */
  fallbackIcon?: string | null
  fallbackColor?: string | null
  size?: keyof typeof BOX
  className?: string
  /** Called when the logo image fails to load, before the fallback shows. */
  onLogoError?: (url: string) => void
}

/** A transaction's or payee's badge: picked icon, else logo, else category icon.
 *
 *  A logo that fails to load drops to the fallback, the same way asset and
 *  institution logos fall back to their type icon.
 */
export function PayeeIcon({
  icon,
  logoUrl,
  fallbackIcon,
  fallbackColor,
  size = 'md',
  className,
  onLogoError,
}: PayeeIconProps) {
  const [failedUrl, setFailedUrl] = useState<string | null>(null)

  if (icon) {
    return <CategoryIcon icon={icon} color={fallbackColor} size={size} className={className} />
  }
  if (logoUrl && failedUrl !== logoUrl) {
    return (
      <div className={cn(BOX[size], 'shrink-0 overflow-hidden bg-white border border-border', className)}>
        <img
          src={logoUrl}
          alt=""
          loading="lazy"
          referrerPolicy="no-referrer"
          className="w-full h-full object-contain"
          onError={() => {
            setFailedUrl(logoUrl)
            onLogoError?.(logoUrl)
          }}
        />
      </div>
    )
  }
  return <CategoryIcon icon={fallbackIcon} color={fallbackColor} size={size} className={className} />
}
