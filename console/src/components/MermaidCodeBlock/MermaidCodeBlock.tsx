import { useEffect, useState } from "react";
import type { Mermaid } from "mermaid";
import styles from "./index.module.less";

let mermaidModule: Mermaid | null = null;
let mermaidInitPromise: Promise<Mermaid> | null = null;
let idCounter = 0;

async function getMermaid(): Promise<Mermaid> {
  if (mermaidModule) return mermaidModule;
  if (!mermaidInitPromise) {
    mermaidInitPromise = import("mermaid").then((mod) => {
      const m = mod.default;
      m.initialize({
        startOnLoad: false,
        theme: "neutral",
        securityLevel: "loose",
      });
      mermaidModule = m;
      return m;
    });
  }
  return mermaidInitPromise;
}

interface MermaidCodeBlockProps {
  chart: string;
}

export function MermaidCodeBlock({ chart }: MermaidCodeBlockProps) {
  const trimmedChart = chart.trim();
  const [svg, setSvg] = useState<string>("");
  const [error, setError] = useState<string>("");
  const [isRendering, setIsRendering] = useState<boolean>(!!trimmedChart);

  useEffect(() => {
    if (!trimmedChart) {
      setSvg("");
      setError("");
      setIsRendering(false);
      return;
    }

    let cancelled = false;
    const id = `mermaid-${Date.now()}-${idCounter++}`;
    setSvg("");
    setError("");
    setIsRendering(true);

    getMermaid()
      .then((mermaid) => mermaid.render(id, trimmedChart))
      .then(({ svg: rendered }) => {
        if (!cancelled) {
          setSvg(rendered);
          setError("");
          setIsRendering(false);
        }
      })
      .catch((renderError) => {
        if (!cancelled) {
          setError(String(renderError));
          setSvg("");
          setIsRendering(false);
        }
        const orphan = document.getElementById("d" + id);
        orphan?.remove();
      });

    return () => {
      cancelled = true;
    };
  }, [trimmedChart]);

  if (error) {
    return (
      <pre className={styles.mermaidError}>
        <code>{chart}</code>
      </pre>
    );
  }

  return (
    <div
      className={`${styles.mermaidDiagram}${
        isRendering ? ` ${styles.isLoading}` : ""
      }`}
    >
      {isRendering ? (
        <div className={styles.placeholder} aria-hidden="true">
          Loading diagram…
        </div>
      ) : null}
      {svg ? (
        <div
          className={styles.content}
          dangerouslySetInnerHTML={{ __html: svg }}
        />
      ) : null}
    </div>
  );
}
