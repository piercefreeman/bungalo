import { headers } from "next/headers";
import { DashboardClient } from "./dashboard-client";

export default async function DashboardPage() {
  const headersList = await headers();
  const host = headersList.get("host") || "localhost";
  
  return <DashboardClient host={host} />;
}
