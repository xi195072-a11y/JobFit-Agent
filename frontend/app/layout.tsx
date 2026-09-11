import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";

import "./globals.css";

export const metadata: Metadata = {
  title: "JobFit Agent",
  description: "JobFit Agent 产品化前端（presentation-only）",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>
        <header className="site-header">
          <div className="site-header-inner">
            <span className="site-brand">JobFit Agent</span>
            <nav className="site-nav" aria-label="主导航">
              <ul>
                <li>
                  <Link href="/">Dashboard</Link>
                </li>
                <li>
                  <Link href="/jobs">Jobs</Link>
                </li>
                <li>
                  <Link href="/analyses/new">新建分析</Link>
                </li>
              </ul>
            </nav>
          </div>
        </header>
        <main className="site-main">{children}</main>
      </body>
    </html>
  );
}
