// Minimal hash router: "#/section/param". Avoids a routing dependency.
import { ReactNode, useEffect, useState } from "react";

export interface Route {
  section: string;
  param: string | null;
}

export function parseHash(hash: string): Route {
  const parts = hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  return { section: parts[0] ?? "overview", param: parts[1] ?? null };
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash));
  useEffect(() => {
    const onChange = () => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

export function href(section: string, param?: string | null): string {
  return `#/${section}${param ? `/${encodeURIComponent(param)}` : ""}`;
}

export function navigate(section: string, param?: string | null): void {
  window.location.hash = href(section, param);
}

export function Link({ to, param, children, className }: { to: string; param?: string | null; children: ReactNode; className?: string }) {
  return <a className={className} href={href(to, param)}>{children}</a>;
}
