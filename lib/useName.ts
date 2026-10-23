"use client";

import { useEffect, useState } from "react";

const KEY = "freshroots:name";

/** The person's name, remembered in this browser so they don't retype it for every decision. */
export function useName(): [string, (v: string) => void] {
  const [name, setName] = useState("");
  useEffect(() => {
    try {
      setName(localStorage.getItem(KEY) || "");
    } catch {
      /* storage blocked: just start empty */
    }
  }, []);
  const update = (v: string) => {
    setName(v);
    try {
      localStorage.setItem(KEY, v);
    } catch {
      /* ignore */
    }
  };
  return [name, update];
}
