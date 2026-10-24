"use client";

/* eslint-disable @next/next/no-img-element */
import { useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, Check, ChevronLeft, Loader2, LogIn, ShieldCheck } from "lucide-react";
import { MicrosoftIcon } from "@/components/ui/BrandIcons";
import { useIdentity } from "@/lib/identity";
import { ACCOUNTS, useSession, type Account } from "@/lib/signin";
import { personaOf } from "@/lib/personas";
import { useAppState } from "@/lib/state";
import type { UserName } from "@/lib/types";

/**
 * The sign-in screen: the provider hand-off, the account chooser, the consent and the claims a
 * token would carry, drawn from whatever is configured under Settings → Authentication.
 *
 * No identity provider is called and no token is issued here: what the screen does is set who you
 * are acting as, which is what the step and gate permissions are read from, and then open the
 * application. Nothing redirects here either, so the application stays reachable without signing
 * in through the link below the card.
 */
export default function SignInPage() {
  const router = useRouter();
  const { setUser } = useAppState();
  const { config } = useIdentity();
  const { session, signIn } = useSession();
  const [stage, setStage] = useState<"provider" | "account" | "signing" | "done">("provider");
  const [account, setAccount] = useState<Account | null>(null);
  // The name a caller knows the provider by. Settings keeps the configuration names
  // (Entra ID, Okta), which is where the tenant and client details are typed.
  const provider = config.provider === "okta" ? "Okta" : "Microsoft";

  const choose = (a: Account) => {
    setAccount(a);
    setStage("signing");
    window.setTimeout(() => {
      signIn(a, config.provider, new Date().toISOString());
      setUser(a.user as UserName);
      setStage("done");
    }, 1100);
  };

  // Signing in lands on step 1: the wizard starts where the estate is uploaded, whatever step
  // this browser was last left on. The home page reads ?step= and then cleans the URL.
  const enter = () => router.push("/?step=upload");

  return (
    <div className="flex min-h-full flex-col items-center justify-center bg-plum px-5 py-10">
      {/* The brand glow behind the card, so the screen reads as the product's own and not as a
          bare form on a dark rectangle. */}
      <div
        aria-hidden
        className="pointer-events-none fixed inset-0 opacity-70"
        style={{
          background:
            "radial-gradient(60% 50% at 50% 0%, rgba(155,93,229,0.38) 0%, rgba(32,6,59,0) 70%), radial-gradient(40% 40% at 85% 100%, rgba(217,198,245,0.14) 0%, rgba(32,6,59,0) 70%)",
        }}
      />

      <div className="relative w-full max-w-[460px]">
        <div className="flex items-center justify-center gap-3">
          <img src="/rsi-logo-white.svg" alt="RSI" className="h-9 w-auto" />
          <span className="leading-tight">
            <span className="block font-display text-[19px] font-semibold tracking-[-0.01em] text-white">
              ExiQO Migrate
            </span>
            <span className="block text-[12px] text-lilac">TIBCO BusinessWorks to .NET</span>
          </span>
        </div>

        <div
          data-testid="signin-card"
          data-stage={stage}
          className="mt-7 overflow-hidden rounded-card bg-white shadow-pop"
        >
          <div className="flex items-center justify-between gap-3 border-b border-line bg-warm px-5 py-2.5">
            <span className="flex items-center gap-2 text-eyebrow uppercase text-slate">
              <ShieldCheck size={14} aria-hidden />
              {stage === "provider" ? "Sign in" : stage === "done" ? "Signed in" : provider}
            </span>
            {stage === "account" ? (
              <button
                type="button"
                className="btn-ghost"
                data-testid="signin-back"
                onClick={() => setStage("provider")}
              >
                <ChevronLeft size={15} aria-hidden /> Back
              </button>
            ) : null}
          </div>

          <div className="px-7 py-7">
            {stage === "provider" ? (
              <Provider
                provider={provider}
                microsoft={config.provider !== "okta"}
                session={session}
                onContinue={() => setStage("account")}
              />
            ) : stage === "account" ? (
              <Chooser onChoose={choose} />
            ) : stage === "signing" ? (
              <Signing provider={provider} account={account} />
            ) : account ? (
              <Done account={account} onEnter={enter} />
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}

function Provider({
  provider,
  microsoft,
  session,
  onContinue,
}: {
  provider: string;
  microsoft: boolean;
  session: { email: string } | null;
  onContinue: () => void;
}) {
  return (
    <>
      <h1 className="font-display text-[24px] font-semibold tracking-[-0.01em] text-plum">Sign in</h1>

      <button
        type="button"
        className="btn-primary mt-5 w-full justify-center py-2.5 text-[14.5px]"
        data-testid="signin-continue"
        onClick={onContinue}
      >
        {microsoft ? <MicrosoftIcon size={17} /> : <LogIn size={16} aria-hidden />} Sign in with {provider}
      </button>

      {session ? (
        <p className="mt-5 rounded-btn bg-gate-bg px-3 py-2 text-[12.5px] text-ink" data-testid="signin-existing">
          <Check size={13} className="mr-1 inline align-[-2px] text-gate" aria-hidden />
          Last signed in on this browser as <span className="font-medium">{session.email}</span>.
        </p>
      ) : null}
    </>
  );
}

function Chooser({ onChoose }: { onChoose: (a: Account) => void }) {
  return (
    <>
      {/* The provider's name is in the card's header bar, which is where its own screens put it. */}
      <h1 className="font-display text-[22px] font-semibold text-plum">Choose an account</h1>

      <ul className="mt-5 divide-y divide-line overflow-hidden rounded-btn border border-line" data-testid="signin-accounts">
        {ACCOUNTS.map((a) => (
          <li key={a.user}>
            <button
              type="button"
              data-testid={`signin-account-${a.user}`}
              onClick={() => onChoose(a)}
              className="flex w-full items-center gap-3 px-3.5 py-3 text-left transition-colors hover:bg-warm"
            >
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-purple-lt text-[13.5px] font-semibold text-purple">
                {a.initial}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[14px] font-medium text-ink">{a.email}</span>
                <span className="block truncate text-[12px] text-slate">
                  {a.role} · gates {personaOf(a.user).gates.join(", ")}
                </span>
              </span>
              <ArrowRight size={15} className="shrink-0 text-slate" aria-hidden />
            </button>
          </li>
        ))}
      </ul>

    </>
  );
}

function Signing({ provider, account }: { provider: string; account: Account | null }) {
  return (
    <div className="flex min-h-[248px] flex-col items-center justify-center text-center">
      <Loader2 size={30} className="animate-spin text-purple" aria-hidden />
      <p className="mt-4 text-[14px] text-ink">Redirecting to {provider}</p>
      <p className="mt-1 text-[12.5px] text-slate">{account?.email}</p>
    </div>
  );
}

function Done({ account, onEnter }: { account: Account; onEnter: () => void }) {
  const persona = personaOf(account.user);
  return (
    <div data-testid="signin-done">
      <p className="flex items-center gap-2 text-[15px] font-semibold text-gate">
        <Check size={18} aria-hidden /> Signed in as {account.email}
      </p>
      <p className="mt-1.5 text-[13px] text-slate">
        Acting as <span className="font-medium text-ink">{persona.role}</span>. {persona.responsibility}
      </p>
      <button type="button" className="btn-primary mt-6 w-full justify-center py-2.5" data-testid="signin-enter" onClick={onEnter}>
        Continue to ExiQO Migrate <ArrowRight size={16} aria-hidden />
      </button>
    </div>
  );
}
