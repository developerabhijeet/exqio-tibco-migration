"use client";

// The product name and support contact come from the install (BRAND_NAME and SUPPORT_CONTACT in
// the API's settings, GET /system/brand), so the UI never carries a vendor name of its own. The
// defaults show until the answer arrives, or when the API cannot be reached.
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";

export interface Brand {
  brand_name: string;
  support_contact: string;
  /** "local": the Connect Azure form takes a secret (local development); otherwise none is entered. */
  deploy_credential_mode: "workload_identity" | "local";
}

export const DEFAULT_BRAND: Brand = { brand_name: "ExiQO", support_contact: "", deploy_credential_mode: "local" };

export function useBrand(): Brand {
  const { data } = useQuery({ queryKey: ["brand"], queryFn: api.brand, staleTime: Infinity, retry: 0 });
  return data ?? DEFAULT_BRAND;
}
