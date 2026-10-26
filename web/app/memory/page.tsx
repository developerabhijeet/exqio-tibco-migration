"use client";

import { Suspense } from "react";
import { MemoryExplorer } from "@/components/memory/MemoryExplorer";

// The explorer keeps its view in the query string (useSearchParams), which needs a Suspense
// boundary in the app router.
export default function MemoryPage() {
  return (
    <Suspense fallback={null}>
      <MemoryExplorer />
    </Suspense>
  );
}
