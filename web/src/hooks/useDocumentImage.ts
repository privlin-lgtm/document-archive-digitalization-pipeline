import { useEffect, useState } from "react";
import { api } from "../api/client";

/**
 * Fetches the document image as an authenticated blob and exposes it as an
 * object URL (see api.getDocumentImageUrl for why — <img src> can't send
 * an Authorization header). Revokes the previous URL on change/unmount so
 * blob URLs don't leak.
 */
export function useDocumentImage(documentId: string | undefined, annotate: boolean) {
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    if (!documentId) {
      return;
    }
    let cancelled = false;
    let objectUrl: string | null = null;

    setIsLoading(true);
    setError(null);
    api
      .getDocumentImageUrl(documentId, annotate)
      .then((url) => {
        if (cancelled) {
          URL.revokeObjectURL(url);
          return;
        }
        objectUrl = url;
        setImageUrl(url);
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err);
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });

    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [documentId, annotate]);

  return { imageUrl, error, isLoading };
}
