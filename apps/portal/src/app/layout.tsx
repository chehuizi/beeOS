/** Portal 根布局 - M1 占位。 */

export const metadata = {
  title: "beeOS Portal",
  description: "beeOS 私有化 AI 数字员工平台",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
