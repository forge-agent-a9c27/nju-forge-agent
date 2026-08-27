# 配置与运行

Forge 的运行时只有 Python 标准库，支持 Python 3.8 及以上版本。模型端需要提供兼容 Chat Completions 的接口和原生 tool calling 能力。

## 凭据

凭据只从进程环境或本地 `.forge/config.json` 读取；该目录已被 `.gitignore` 排除。建议优先使用会话级环境变量。不要把真实值写入命令历史、仓库、README、截图或演示视频。

PowerShell 可以用隐藏输入设置当前会话的 key：

```powershell
$secure = Read-Host "FORGE_API_KEY" -AsSecureString
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
$env:FORGE_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
$env:FORGE_MODEL = Read-Host "FORGE_MODEL"
```

兼容服务还需设置 `FORGE_BASE_URL`。地址可以是 API 的版本根路径，也可以直接是 `/chat/completions` 地址。特殊网关请求头可通过 `FORGE_EXTRA_HEADERS_JSON` 传入。

## 启动

源码目录直接运行，无需安装：

```powershell
python agent.py --workspace D:\path\to\project
```

单次任务：

```powershell
python agent.py -w D:\path\to\project "检查项目，修复测试并说明原因"
```

也可执行 `python -m pip install -e .`，之后使用 `forge` 命令。

## 审批模式

- `ask`（默认）：普通文件编辑自动执行；网络、安装、删除、Git 写操作和任意脚本等命令由用户确认。
- `auto`：自动执行需确认命令，但灾难性命令仍永久阻断。只应在隔离工作区使用。
- `read-only`：禁用所有可能修改状态的工具，适合仅分析代码。

风险分类只是纵深防御而不是操作系统级沙箱。运行不可信项目时仍应配合容器、虚拟机或低权限账户。

## 测试

```powershell
python -m unittest discover -s tests -v
python -m compileall -q agent.py forge_agent
```

测试完全离线：本地临时 HTTP 服务模拟模型厂商，覆盖从 CLI 请求、tool call、文件写入、tool observation 到最终回答的全链路。

