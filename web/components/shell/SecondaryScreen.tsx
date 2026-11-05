import { PageHeader } from "@/components/ui/PageHeader";

/** Layout for the secondary screens reached from the top bar: the page header, then its sections. */
export function SecondaryScreen({
  title,
  description,
  eyebrow = "Project",
  actions,
  children,
}: {
  title: string;
  description: string;
  eyebrow?: string;
  actions?: React.ReactNode;
  children?: React.ReactNode;
}) {
  return (
    <div className="page">
      <PageHeader back={{ href: "/", label: "Back to the migration" }} eyebrow={eyebrow} title={title} description={description} actions={actions} />
      {children ?? (
        <section className="card">
          <p className="text-slate">This screen is not available in this build yet.</p>
        </section>
      )}
    </div>
  );
}
