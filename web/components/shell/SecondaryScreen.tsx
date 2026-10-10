import Link from "next/link";
import { ArrowLeft } from "lucide-react";

/** Layout for the secondary screens reached from the top bar. */
export function SecondaryScreen({ title, description, children }: { title: string; description: string; children?: React.ReactNode }) {
  return (
    <div className="space-y-6">
      <Link href="/" className="inline-flex items-center gap-1.5 text-[14px] font-medium text-purple hover:underline">
        <ArrowLeft size={16} aria-hidden /> Back to the migration
      </Link>
      <div>
        <p className="eyebrow">Project</p>
        <h1 className="screen-title">{title}</h1>
        <p className="mt-1 text-slate">{description}</p>
      </div>
      {children ?? (
        <section className="card p-8">
          <p className="text-slate">This screen is not available in this build yet.</p>
        </section>
      )}
    </div>
  );
}
