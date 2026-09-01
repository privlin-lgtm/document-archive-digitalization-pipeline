import { Fragment } from "react";

/**
 * Renders a Postgres ts_headline() snippet, which wraps matched terms in
 * <b>...</b>. The snippet embeds the *document's own OCR'd text* — not
 * fully trusted input — so this parses out just the <b> spans and renders
 * everything else as plain text, rather than dangerouslySetInnerHTML'ing
 * the raw string (which would execute any HTML/script that happened to
 * make it into a scanned document's OCR output).
 */
export function SnippetHighlight({ snippet }: { snippet: string }) {
  const parts = snippet.split(/(<b>.*?<\/b>)/g);
  return (
    <>
      {parts.map((part, index) => {
        const match = /^<b>(.*)<\/b>$/.exec(part);
        return (
          <Fragment key={index}>
            {match ? <mark>{match[1]}</mark> : part}
          </Fragment>
        );
      })}
    </>
  );
}
