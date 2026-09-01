import { useEffect, useRef, useState, type ReactNode } from "react";

interface SplitPaneProps {
  left: ReactNode;
  right: ReactNode;
  initialLeftPct?: number;
}

export function SplitPane({ left, right, initialLeftPct = 58 }: SplitPaneProps) {
  const [leftPct, setLeftPct] = useState(initialLeftPct);
  const [active, setActive] = useState(false);
  const dragging = useRef(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onMove(event: MouseEvent) {
      if (!dragging.current || !containerRef.current) return;
      const rect = containerRef.current.getBoundingClientRect();
      const pct = ((event.clientX - rect.left) / rect.width) * 100;
      setLeftPct(Math.min(76, Math.max(28, pct)));
    }
    function onUp() {
      dragging.current = false;
      setActive(false);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    }
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, []);

  return (
    <div ref={containerRef} className="split-pane">
      <div className="split-pane-side" style={{ width: `${leftPct}%` }}>
        {left}
      </div>
      <div
        className="split-handle"
        data-active={active ? "true" : "false"}
        onMouseDown={(event) => {
          event.preventDefault();
          dragging.current = true;
          setActive(true);
          document.body.style.cursor = "col-resize";
          document.body.style.userSelect = "none";
        }}
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize document panes"
      />
      <div className="split-pane-side" style={{ flex: 1 }}>
        {right}
      </div>
    </div>
  );
}
