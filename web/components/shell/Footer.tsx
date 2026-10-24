"use client";

import { useBrand } from "@/lib/brand";

export function Footer() {
  const { brand_name, support_contact } = useBrand();
  return (
    <footer className="mt-12 bg-footer">
      <div className="flex w-full items-center justify-between gap-4 px-6 py-6">
        <p className="text-[13px] text-lilac">{brand_name} Migrate</p>
        {support_contact ? (
          <p data-testid="support-contact" className="text-[13px] text-lilac">
            Support: {support_contact}
          </p>
        ) : null}
      </div>
    </footer>
  );
}
