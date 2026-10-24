"use client";

// Identity configuration for the generated application: which provider signs its callers in, and
// the endpoint and client details it validates tokens against.
//
// The configuration keys here are the real ones. The generated Program.cs reads Auth:Mode, and in
// any environment other than Development it validates bearer tokens against
// Auth:EntraId:{Instance,TenantId,Audience}; the Okta keys follow the same shape under Auth:Okta.
// So what an architect types here is the configuration the deployed service reads, named exactly as
// the service names it.
//
// Secrets are deliberately not persisted. A client secret or API key typed here stays in the form
// for as long as the page is open and is never written to storage, because browser storage is
// readable by anything running on this origin and a secret belongs in Key Vault, which is where the
// generated application reads it from.
import { useCallback, useEffect, useState } from "react";

export const PROVIDERS = [
  {
    key: "entra",
    name: "Microsoft Entra ID",
    also: "Azure AD",
    section: "Auth:EntraId",
    docs: "OpenID Connect, tokens validated against the tenant's v2.0 metadata",
  },
  {
    key: "okta",
    name: "Okta",
    also: "Okta Workforce Identity",
    section: "Auth:Okta",
    docs: "OpenID Connect, tokens validated against the authorization server's metadata",
  },
] as const;

export type ProviderKey = (typeof PROVIDERS)[number]["key"];

export interface IdentityField {
  key: keyof IdentityConfig & string;
  label: string;
  /** The configuration key the generated application reads this as. */
  setting: (section: string) => string;
  placeholder: string;
  hint: string;
  /** A secret: shown masked, never stored. */
  secret?: boolean;
  optional?: boolean;
}

export interface IdentityConfig {
  provider: ProviderKey;
  instance: string;
  tenantId: string;
  clientId: string;
  audience: string;
  scopes: string;
  /** Not persisted; see the note at the top of this file. */
  clientSecret: string;
  /** Not persisted. */
  apiKey: string;
  requireHttps: boolean;
  /** Minutes of clock skew tolerated when validating a token's lifetime. */
  clockSkew: string;
}

export const EMPTY: IdentityConfig = {
  provider: "entra",
  instance: "",
  tenantId: "",
  clientId: "",
  audience: "",
  // Empty, not a template: a literal "<client-id>" in the generated appsettings preview would be
  // copied into a real configuration by someone reading it quickly.
  scopes: "",
  clientSecret: "",
  apiKey: "",
  requireHttps: true,
  clockSkew: "5",
};

const ENTRA_FIELDS: IdentityField[] = [
  {
    key: "instance",
    label: "Authority endpoint",
    setting: (s) => `${s}:Instance`,
    placeholder: "https://login.microsoftonline.com/",
    hint: "The authority the service builds its metadata address from. Trailing slash included.",
  },
  {
    key: "tenantId",
    label: "Directory (tenant) ID",
    setting: (s) => `${s}:TenantId`,
    placeholder: "00000000-0000-0000-0000-000000000000",
    hint: "The GMF directory the tokens are issued by.",
  },
  {
    key: "clientId",
    label: "Application (client) ID",
    setting: (s) => `${s}:ClientId`,
    placeholder: "00000000-0000-0000-0000-000000000000",
    hint: "The app registration for this service.",
  },
  {
    key: "audience",
    label: "Audience (Application ID URI)",
    setting: (s) => `${s}:Audience`,
    placeholder: "api://gmf-credit",
    hint: "A token whose audience is not this value is rejected.",
  },
  {
    key: "scopes",
    label: "Scope requested by callers",
    setting: (s) => `${s}:Scopes`,
    placeholder: "api://gmf-credit/.default",
    hint: "Shown to a caller and used by the sign-in preview; not validated by the service itself.",
    optional: true,
  },
  {
    key: "clientSecret",
    label: "Client secret",
    setting: () => "Key Vault secret Auth--ClientSecret",
    placeholder: "held in Key Vault, not here",
    hint: "Never stored by this screen. The deployed service reads it from Key Vault with its managed identity.",
    secret: true,
    optional: true,
  },
];

const OKTA_FIELDS: IdentityField[] = [
  {
    key: "instance",
    label: "Okta domain (issuer)",
    setting: (s) => `${s}:Authority`,
    placeholder: "https://gmfinancial.okta.com/oauth2/default",
    hint: "The authorization server the service validates tokens against.",
  },
  {
    key: "clientId",
    label: "Client ID",
    setting: (s) => `${s}:ClientId`,
    placeholder: "0oa1a2b3c4d5e6f7g8h9",
    hint: "The Okta application for this service.",
  },
  {
    key: "audience",
    label: "Audience",
    setting: (s) => `${s}:Audience`,
    placeholder: "api://gmf-credit",
    hint: "A token whose audience is not this value is rejected.",
  },
  {
    key: "scopes",
    label: "Scope requested by callers",
    setting: (s) => `${s}:Scopes`,
    placeholder: "gmf.credit.read",
    hint: "Shown to a caller and used by the sign-in preview; not validated by the service itself.",
    optional: true,
  },
  {
    key: "apiKey",
    label: "API token",
    setting: () => "Key Vault secret Auth--OktaApiToken",
    placeholder: "held in Key Vault, not here",
    hint: "Never stored by this screen. Needed only where the service calls Okta's management API.",
    secret: true,
    optional: true,
  },
];

export function fieldsFor(provider: ProviderKey): IdentityField[] {
  return provider === "okta" ? OKTA_FIELDS : ENTRA_FIELDS;
}

export function providerOf(key: ProviderKey) {
  return PROVIDERS.find((p) => p.key === key) ?? PROVIDERS[0];
}

/** Fields with no value yet, so the screen can say what is still missing rather than look complete. */
export function missing(config: IdentityConfig): IdentityField[] {
  return fieldsFor(config.provider).filter((f) => !f.optional && !String(config[f.key] ?? "").trim());
}

/** The appsettings section the generated application would read, as JSON. */
export function appsettings(config: IdentityConfig): string {
  const section = providerOf(config.provider).section;
  const name = section.split(":")[1];
  const values: Record<string, unknown> = {};
  for (const f of fieldsFor(config.provider)) {
    if (f.secret) continue;
    const value = String(config[f.key] ?? "").trim();
    if (value) values[f.setting(section).split(":").pop() as string] = value;
  }
  values.RequireHttpsMetadata = config.requireHttps;
  const skew = Number.parseInt(config.clockSkew, 10);
  if (Number.isFinite(skew)) values.ClockSkewMinutes = skew;
  return JSON.stringify(
    {
      Auth: {
        Mode: config.provider === "entra" ? "EntraId" : "Okta",
        [name]: values,
      },
    },
    null,
    2,
  );
}

const KEY = "exiqo.identity";

/** The identity configuration, kept in this browser. Secrets are dropped before it is written. */
export function useIdentity(): {
  config: IdentityConfig;
  set: <K extends keyof IdentityConfig>(key: K, value: IdentityConfig[K]) => void;
  reset: () => void;
  saved: boolean;
  save: () => void;
} {
  const [config, setConfig] = useState<IdentityConfig>(EMPTY);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(KEY);
      if (raw) setConfig({ ...EMPTY, ...(JSON.parse(raw) as Partial<IdentityConfig>) });
    } catch {
      /* storage unavailable: the form starts empty */
    }
  }, []);

  const set = useCallback(<K extends keyof IdentityConfig>(key: K, value: IdentityConfig[K]) => {
    setSaved(false);
    setConfig((c) => ({ ...c, [key]: value }));
  }, []);

  const save = useCallback(() => {
    try {
      // clientSecret and apiKey are left out on purpose: a secret does not belong in browser storage.
      const rest = Object.fromEntries(
        Object.entries(config).filter(([k]) => k !== "clientSecret" && k !== "apiKey"),
      );
      window.localStorage.setItem(KEY, JSON.stringify(rest));
      setSaved(true);
    } catch {
      /* storage unavailable: the values last for this page only */
    }
  }, [config]);

  const reset = useCallback(() => {
    try {
      window.localStorage.removeItem(KEY);
    } catch {
      /* nothing stored */
    }
    setConfig(EMPTY);
    setSaved(false);
  }, []);

  return { config, set, reset, saved, save };
}
