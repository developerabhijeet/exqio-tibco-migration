import { Lock } from "lucide-react";
import type { DesignRecord } from "@/lib/types";

/** TIBCO substitution variables and the .NET configuration keys they become; secrets go to Key Vault. */
export function ConfigTable({ config, ignored }: { config: DesignRecord["config"]; ignored: DesignRecord["config_ignored"] }) {
  const secrets = config.filter((c) => c.secret).length;
  return (
    <div className="space-y-3">
      {config.length === 0 ? (
        <p className="text-[14px] text-slate">This process reads no substitution variables.</p>
      ) : (
        <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] text-[13.5px]" data-testid="config-table">
          <thead>
            <tr className="table-head">
              <th className="px-3 py-2">TIBCO substitution variable</th>
              <th className="px-3 py-2">.NET configuration key</th>
              <th className="px-3 py-2">Storage</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {config.map((c) => (
              <tr key={c.substvar} data-secret={c.secret}>
                <td className="px-3 py-2 font-mono text-[12.5px]">{c.substvar}</td>
                <td className="px-3 py-2 font-mono text-[12.5px]">{c.key}</td>
                <td className="px-3 py-2">
                  {c.secret ? (
                    <span className="inline-flex items-center gap-1.5 font-medium text-plum">
                      <Lock size={14} className="text-coral" aria-label="secret" /> Key Vault secret
                    </span>
                  ) : (
                    <span className="text-slate">appsettings</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}
      {secrets > 0 ? (
        <p className="flex items-start gap-2 rounded-btn bg-warm px-3 py-2 text-[13px] text-slate">
          <Lock size={14} className="mt-0.5 shrink-0 text-coral" aria-hidden />
          Secret values are never written to appsettings or the repository. The service reads them from Azure Key Vault
          through its managed identity; locally they come from user secrets.
        </p>
      ) : null}
      {ignored.length > 0 ? (
        <details className="rounded-btn border border-line px-3 py-2">
          <summary className="cursor-pointer text-[13.5px] font-medium text-plum">
            {ignored.length} variable{ignored.length > 1 ? "s" : ""} not carried over
          </summary>
          <ul className="mt-2 space-y-0.5 text-[13px]">
            {ignored.map((i) => (
              <li key={i.substvar}>
                <span className="font-mono text-[12.5px]">{i.substvar}</span>: <span className="text-slate">{i.reason}</span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}
