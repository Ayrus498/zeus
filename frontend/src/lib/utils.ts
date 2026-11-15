import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export const cn = (...i: ClassValue[]) => twMerge(clsx(i));

export function fmtBytes(n?: number | null): string {
  if (!n) return "–";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0, v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${u[i]}`;
}
export const fmtNum = (n?: number | null) => (n == null ? "–" : n.toLocaleString());
export const fmtCtx = (n: number) => (n >= 1024 ? `${Math.round(n / 1024)}K` : String(n));
