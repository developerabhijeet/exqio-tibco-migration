"use client";

// The sign-in screen's model: the sample accounts it offers, the claims a token for one of them
// would carry, and the session it records in this browser.
//
// What this is. A walkthrough of the sign-in your callers see, drawn from the identity
// configuration on the Settings screen. It contacts no identity provider, issues no token and
// validates nothing. What it does do is real: choosing an account sets the acting persona, which
// is what the step and gate permissions are read from and what every API request carries as
// X-User. So the flow demonstrates the provider's screens and the role mapping behind them,
// and ends in the application as that person.
//
// What it is not. It is not a door. Every screen of this application is reachable without it, and
// nothing here is an authorisation check: the acting persona is a choice the user makes, exactly
// as it is in the top bar menu. The authentication that does get enforced is in the generated
// .NET service, which validates provider tokens against the keys configured on that same screen.
import { useCallback, useEffect, useState } from "react";
import { PERSONAS, personaOf, type Persona } from "./personas";
import type { IdentityConfig, ProviderKey } from "./identity";
import type { UserName } from "./types";

/**
 * A sample directory account for a persona. The domain is the reserved `example` TLD, so nothing
 * on this screen can be mistaken for a GMF directory user.
 */
export interface Account {
  user: UserName;
  email: string;
  /** Given name, as a provider's account tile shows it. */
  name: string;
  role: string;
  /** Initial for the account tile. */
  initial: string;
}

/** Persona ids are `team.given`, which is where both halves of the sample address come from. */
export function accountOf(persona: Persona): Account {
  const [team, given] = persona.id.split(".");
  const name = given.charAt(0).toUpperCase() + given.slice(1);
  return {
    user: persona.id,
    email: `${given}.${team}@gmf.example`,
    name,
    role: persona.role,
    initial: name.charAt(0),
  };
}

export const ACCOUNTS: Account[] = PERSONAS.map(accountOf);

export function accountFor(user: string): Account {
  return accountOf(personaOf(user));
}

/** The configured authority, or null: a placeholder endpoint nobody typed is worse than nothing. */
export function authorityOf(config: IdentityConfig): string | null {
  return config.instance.trim() || null;
}

export interface Session {
  /** The account signed in on this browser. */
  email: string;
  user: UserName;
  provider: ProviderKey;
  /** ISO time of the sign-in. */
  at: string;
}

const KEY = "exiqo.session";

/**
 * The sign-in recorded in this browser. It is a record of a walkthrough, not a credential: it holds
 * no token, and nothing reads it to decide whether a request is allowed.
 */
export function useSession(): {
  session: Session | null;
  signIn: (account: Account, provider: ProviderKey, at: string) => void;
  signOut: () => void;
} {
  const [session, setSession] = useState<Session | null>(null);

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(KEY);
      if (raw) setSession(JSON.parse(raw) as Session);
    } catch {
      /* storage unavailable, or a record this version cannot read: no session is shown */
    }
  }, []);

  const signIn = useCallback((account: Account, provider: ProviderKey, at: string) => {
    const next: Session = { email: account.email, user: account.user, provider, at };
    setSession(next);
    try {
      window.localStorage.setItem(KEY, JSON.stringify(next));
    } catch {
      /* storage unavailable: the session lasts for this page only */
    }
  }, []);

  const signOut = useCallback(() => {
    setSession(null);
    try {
      window.localStorage.removeItem(KEY);
    } catch {
      /* nothing stored */
    }
  }, []);

  return { session, signIn, signOut };
}
