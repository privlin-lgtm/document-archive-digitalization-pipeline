import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { EntityCorrectionRequest } from "../api/types";

export function useCorrectEntity(documentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ entityId, body }: { entityId: string; body: EntityCorrectionRequest }) =>
      api.correctEntity(entityId, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["document", documentId] });
    },
  });
}
