Forge 编程智能体

公开 Git 仓库：https://github.com/liangshaotian/nju-forge-agent

一、项目简介
Forge 是一个从零设计并实现的命令行 coding agent。用户只需描述编程目标，Forge 就会与大语言模型交互，自主查看代码、制定计划、修改文件、执行测试，并根据真实结果继续决策，直到完成任务或触发明确的终止条件。项目不使用 LangChain、LlamaIndex、OpenAI Agents SDK 等 agent 框架，也不依赖 Code Interpreter、Files API 等服务端托管工具；模型协议、对话历史、上下文压缩、工具调用解析、本地执行、错误恢复和循环控制均自行实现。

二、运行方式
运行环境为 Python 3.8 及以上版本，仅使用标准库，无第三方运行时依赖。请在系统环境中设置 FORGE_API_KEY 和 FORGE_MODEL；使用 OpenAI-compatible 网关时再设置 FORGE_BASE_URL。凭据也可放入已被 Git 忽略的 .forge/config.json，任何真实凭据都不应写入仓库、README 或视频。

交互模式：
python agent.py --workspace <项目目录>

单次任务：
python agent.py -w <项目目录> "检查项目，修复失败测试并说明原因"

输入 /help 可查看交互命令，/clear 清除对话但不修改文件，/status 查看上下文和计划状态。常用参数包括 --max-steps、--context-tokens、--timeout、--show-reasoning，以及 --approval ask/auto/read-only。

三、核心能力
1. 自主闭环：模型原生 tool calling 驱动“观察—决策—执行—反馈”，工具结果作为 observation 写回历史，模型依据事实决定下一步。
2. 本地工具：自行实现目录遍历、带行号和 SHA-256 的文件读取、正则搜索、原子写入、精确替换、编辑回滚、本地命令和动态计划。
3. 上下文管理：按完整 assistant-tool 事务压缩旧历史，保留用户目标、关键决策和执行证据，避免破坏工具调用协议。
4. 安全编辑：所有模型路径经规范化后必须位于工作区；.git、.forge、环境文件和常见私钥受保护。写入采用临时文件、fsync 和原子替换；可选版本哈希防止依据旧内容覆盖人工修改，每次编辑返回可校验的撤销事务号。
5. 命令风控：命令分为安全、需确认和永久阻断三级，支持超时、退出码和头尾截断；子进程会移除名称包含 key、token、secret、password 等字段的环境变量。
6. 稳定兼容：API 遇到限流、超时和服务端错误时有限退避重试；若兼容网关吞掉原生 tool_calls，则自动切换为自研文本工具协议并保持后续多轮可用。参数和执行异常会转换为模型可理解的结果；连续三次相同调用触发熔断，达到步数上限后撤销工具权限并生成事实性总结。

四、代码结构
forge_agent/model.py 负责直接 HTTP 请求与模型输出解析；context.py 管理历史和确定性压缩；workspace.py 实现工作区边界、原子编辑和 journal；tools.py 定义 JSON Schema 与本地工具；agent.py 实现核心循环；cli.py 处理终端交互和人工审批。

五、测试与文档
执行 python -m unittest discover -s tests -v 可运行 29 项离线测试，覆盖路径逃逸、敏感文件、并发冲突、回滚、命令分级、凭据隔离、上下文压缩、循环熔断、网关兼容、API 解析以及 CLI 到本地 HTTP 模拟模型再到文件写入和最终回答的完整链路。执行 python -m compileall -q agent.py forge_agent 可做语法检查。

详细架构、设计取舍和已知边界见 docs/DESIGN.md；配置说明见 docs/SETUP.md；两分钟演示流程与最终打包方法见 docs/DEMO.md。命令分类属于纵深防御而非操作系统沙箱，处理未知或恶意项目时仍建议使用容器、虚拟机或低权限账户。
