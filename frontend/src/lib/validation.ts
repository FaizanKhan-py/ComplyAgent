/** Client-side checks for the sign-in and sign-up forms. The server repeats all of them. */

export const PASSWORD_MIN_LENGTH = 8
export const PASSWORD_MAX_LENGTH = 128

export function emailError(value: string): string | null {
  const email = value.trim()
  if (!email) return 'Enter your email address.'
  // Deliberately loose: the server does the real validation.
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return 'Enter a valid email address.'
  return null
}

export function nameError(value: string): string | null {
  const name = value.trim()
  if (!name) return 'Enter your full name.'
  if (name.length > 100) return 'The name must be at most 100 characters.'
  return null
}

export function passwordError(password: string, email = ''): string | null {
  if (!password) return 'Enter a password.'
  if (password.length < PASSWORD_MIN_LENGTH) {
    return `Use at least ${PASSWORD_MIN_LENGTH} characters.`
  }
  if (password.length > PASSWORD_MAX_LENGTH) {
    return `Use at most ${PASSWORD_MAX_LENGTH} characters.`
  }
  if (email && password.toLowerCase() === email.trim().toLowerCase()) {
    return 'The password must not be your email address.'
  }
  return null
}

export interface Strength {
  /** 0 (too short) to 4 (strong). */
  score: 0 | 1 | 2 | 3 | 4
  label: 'Too short' | 'Weak' | 'Fair' | 'Good' | 'Strong'
}

const LABELS = ['Too short', 'Weak', 'Fair', 'Good', 'Strong'] as const

/** A rough guide based on length and variety. Length counts most. */
export function passwordStrength(password: string): Strength {
  if (password.length < PASSWORD_MIN_LENGTH) return { score: 0, label: LABELS[0] }
  const kinds = [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].filter((kind) => kind.test(password)).length
  let score = 1
  if (password.length >= 12) score += 1
  if (password.length >= 16) score += 1
  if (kinds >= 3) score += 1
  if (kinds === 1 && score > 2) score = 2
  if (new Set(password).size <= 3) score = 1 // "aaaaaaaaaaaa"
  const bounded = Math.min(score, 4) as Strength['score']
  return { score: bounded, label: LABELS[bounded] }
}
