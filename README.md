# AMPgent 科学分析界面

用于查看短肽设计轮次、证据链和确定性分析结果的只读科学工作区。核心数据来自本地数据服务连接的 PostgreSQL 数据库；分析页在实时分析接口启用前读取已校验的发布快照。

## 本地运行

```powershell
npm install
npm run dev
```

`npm run dev` 会依次完成：

1. 检查 `http://127.0.0.1:8081` 的数据服务。
2. 如果尚未运行，使用相邻目录 `agent-platform/.venv-local` 自动启动只读接口。
3. 将界面固定启动在 `http://127.0.0.1:5173`。

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
