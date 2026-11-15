import { useEffect } from "react";
import { AppLayout } from "../layouts/AppLayout";
import { useApp } from "../stores/app";

export default function Workspace() {
  const init = useApp((s) => s.init);
  useEffect(() => { void init(); }, [init]);
  return <AppLayout />;
}
