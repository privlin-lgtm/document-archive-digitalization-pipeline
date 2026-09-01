import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { FlagSeverity, ReviewFlagStatus, ReviewFlagUpdateRequest } from "../api/types";

export function useReviewFlags(
  params: { status?: ReviewFlagStatus | null; severity?: FlagSeverity; limit?: number; offset?: number } = {},
) {
  return useQuery({
    queryKey: ["review_flags", params],
    queryFn: () => api.listReviewFlags(params),
  });
}

export function useUpdateReviewFlag() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ flagId, body }: { flagId: string; body: ReviewFlagUpdateRequest }) =>
      api.updateReviewFlag(flagId, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["review_flags"] });
      queryClient.invalidateQueries({ queryKey: ["stats"] });
      queryClient.invalidateQueries({ queryKey: ["document"] });
    },
  });
}
