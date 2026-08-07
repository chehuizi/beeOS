/** Portal 首页 - M1 占位。 */

export default function HomePage() {
  return (
    <main className="min-h-screen p-8">
      <h1 className="text-3xl font-bold">beeOS Portal</h1>
      <p className="mt-4 text-gray-600">
        私有化 AI 数字员工平台 · M1 骨架
      </p>
      <div className="mt-8 p-4 border rounded-lg">
        <h2 className="text-xl font-semibold">M1 状态</h2>
        <ul className="mt-2 space-y-1 text-sm">
          <li>✅ 后端 Queen 服务骨架（FastAPI / /health）</li>
          <li>✅ Bee 引擎骨架（占位）</li>
          <li>✅ MonthCloseBox 骨架（占位）</li>
          <li>✅ Portal Web 骨架（占位）</li>
          <li>✅ Docker Compose 开发环境</li>
          <li>🚧 完整功能（M1+ 持续实现）</li>
        </ul>
      </div>
    </main>
  );
}
