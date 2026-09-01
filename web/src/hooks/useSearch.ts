import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { SearchFilters } from "../api/types";

export function useSearch(filters: SearchFilters) {
  return useQuery({
    queryKey: ["search", filters],
    queryFn: () => api.search(filters),
    enabled: filters.q.trim().length > 0,
  });
}
