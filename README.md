# AMPgent 科学分析界面

用于查看短肽设计轮次、证据链和确定性分析结果的只读科学工作区。核心数据来自本地数据服务连接的 PostgreSQL 数据库；分析页在实时分析接口启用前读取已校验的发布快照。

## 本地运行

```powershell
npm install
npm run dev
```

`npm run dev` 默认以只读 Observer 模式启动，不导入 Temporal 控制面，会依次完成：

1. 以短超时检查 `127.0.0.1:55432`。已有安全隧道时直接复用，不重复启动。
2. 隧道未监听时，使用相邻目录 `agent-platform/deploy/tunnels/start_019_pepagent_tunnels.ps1` 以隐藏持久进程启动，只读 SSH 转发；不会启动本地 PostgreSQL，也不会在 UI 仓库保存凭据。
3. 在隧道就绪后检查 `http://127.0.0.1:8081/healthz`；如果端口或远端不可用，在有限等待后给出“安全隧道未连接”的中文错误。
4. 如果 Observer 尚未运行，使用相邻目录 `agent-platform/.venv-local` 自动启动不连接 Temporal 的 Observer 接口，并将界面固定启动在 `http://127.0.0.1:5173`。

只读接口启动快，但运行数据仍要求 PostgreSQL 安全隧道可用；隧道不可用时接口请求会快速返回错误，界面保留诚实空态。需要包含写入/Temporal 控制面的开发模式时，使用 `npm run dev:control-plane`。

数据库默认连接本机 `55432` 端口的现有 PostgreSQL 实例。界面右上角“数据连接”可检查连接状态、恢复本机默认地址或设置其他只读数据服务。

如后端位于其他目录，可在运行前设置：

```powershell
$env:AMPGENT_BACKEND_ROOT = 'D:\path\to\agent-platform'
$env:AMPGENT_PYTHON = 'D:\path\to\python.exe'
npm run dev
```

仅调试界面、不自动启动数据服务时使用：

```powershell
npm run dev:ui
```

## 安装到 Windows 开始菜单

```powershell
npm run install:start-menu
```

开始菜单会出现“AMPgent 科学分析”。点击后会在后台启动数据服务和界面，并在就绪后打开浏览器。卸载入口：

```powershell
npm run uninstall:start-menu
```

## 发布检查

```powershell
.\scripts\release-check.ps1
```
