/*
 * Copied from RuseBot 0.1.127 src/lib/cn.ts (Apache-2.0, Copyright 2026 Milind Soni and OpenMausBot contributors).
 */
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
