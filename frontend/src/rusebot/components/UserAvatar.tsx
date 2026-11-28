import { cn } from '../lib/cn';

export function UserAvatar({ name, photo, large = false }: { name: string; photo: string | null; large?: boolean }) {
  return (
    <span data-user-avatar className={cn(
      'flex shrink-0 items-center justify-center overflow-hidden rounded-full bg-raised font-medium text-ink-secondary',
      large ? 'size-24 text-[28px]' : 'size-8 text-[13px]',
    )}>
      {photo ? <img src={photo} alt="Your profile photo" className="size-full object-cover" /> : name.trim().charAt(0).toUpperCase() || '?'}
    </span>
  );
}
