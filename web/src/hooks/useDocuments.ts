import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { DocumentStatus } from "../api/types";

export function useDocuments(params: { status?: DocumentStatus; limit?: number; offset?: number } = {}) {
  return useQuery({
    queryKey: ["documents", params],
    queryFn: () => api.listDocuments(params),
  });
}

export function useDocument(documentId: string | undefined) {
  return useQuery({
    queryKey: ["document", documentId],
    queryFn: () => api.getDocument(documentId as string),
    enabled: Boolean(documentId),
  });
}

export function useUploadDocuments() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (files: File[]) => api.uploadDocuments(files),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["documents"] });
      queryClient.invalidateQueries({ queryKey: ["stats"] });
    },
  });
}
