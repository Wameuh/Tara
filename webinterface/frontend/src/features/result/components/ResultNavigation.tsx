export function ResultNavigation({ sections, hrefFor, onSelect, label }: { sections: { id: string; title: string }[]; hrefFor: (id: string) => string; onSelect: (id: string) => void; label: string }) {
  return <nav aria-label={label}>{sections.map((section, index) => <a key={section.id} href={hrefFor(section.id)} onClick={(event) => {
    event.preventDefault();
    history.replaceState(null, "", hrefFor(section.id));
    onSelect(section.id);
  }}><span aria-hidden="true">{String(index + 1).padStart(2, "0")}</span>{section.title}</a>)}</nav>;
}
