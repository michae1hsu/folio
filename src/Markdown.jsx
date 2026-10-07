import React, { useEffect, useRef } from 'react';
import { marked } from 'marked';
import DOMPurify from 'dompurify';

let diagramLibrary;
let diagramNumber = 0;

export default function Markdown({text = '', className = ''}) {
  const root = useRef();
  useEffect(() => {
    let current = true;
    const container = root.current;
    container.innerHTML = DOMPurify.sanitize(marked.parse(text), {
      FORBID_TAGS: ['img', 'style', 'iframe', 'video', 'audio', 'form'], FORBID_ATTR: ['style'],
    });
    container.querySelectorAll('a').forEach(a => { a.target = '_blank'; a.rel = 'noopener noreferrer'; });
    const diagrams = [...container.querySelectorAll('code.language-mermaid')];
    if (diagrams.length) (async () => {
      diagramLibrary ||= import('mermaid').then(({default: mermaid}) => {
        mermaid.initialize({startOnLoad: false, securityLevel: 'strict', theme: 'neutral', htmlLabels: false, suppressErrorRendering: true});
        return mermaid;
      });
      const mermaid = await diagramLibrary;
      for (const node of diagrams) {
        if (!current) return;
        try {
          const {svg} = await mermaid.render(`folio-diagram-${++diagramNumber}`, node.textContent);
          if (!current) return;
          const figure = document.createElement('figure');
          figure.className = 'rendered-diagram';
          figure.innerHTML = DOMPurify.sanitize(svg, {USE_PROFILES: {svg: true, svgFilters: true}});
          const details = document.createElement('details');
          const summary = document.createElement('summary'); summary.textContent = 'View diagram source';
          details.append(summary, node.parentElement.cloneNode(true));
          figure.append(details);
          node.parentElement.replaceWith(figure);
        } catch {
          if (current) node.parentElement.setAttribute('title', 'Could not render the diagram. The source is preserved for review.');
        }
      }
    })().catch(() => { /* Keep readable source code if the diagram library cannot load. */ });
    return () => { current = false; };
  }, [text]);
  return <article ref={root} className={`markdown-content ${className}`}/>;
}
