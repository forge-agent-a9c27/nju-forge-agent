Forge 编程智能体

公开 Git 仓库：[发布后在此填写 GitHub/Gitee 地址]

Forge 是一个从零实现的命令行 coding agent。它直连 OpenAI-compatible Chat Completions API，不使用 LangChain、Agents SDK 等 agent 框架，也不依赖服务端代码执行或文件工具。运行时仅需 Python 3.8+，无第三方依赖。

运行：先在系统环境中设置 FORGE_API_KEY、FORGE_MODEL；如使用兼容网关，再设置 FORGE_BASE_URL。进入仓库执行“python agent.py -w <工作目录>”，然后输入任务；也可在命令末尾直接附加单次任务。配置可放在不会入库的 .forge/config.json。执行“python -m unittest discover -s tests -v”可运行全部离线测试。

特色：
1. 自主闭环：模型原生 tool calling 驱动“观察—决策—执行—反馈”，支持连续多轮任务。
2. 工程工具：目录浏览、带行号/版本哈希的读取、正则搜索、原子写入、精确编辑、本地命令和动态计划均为自行实现。
3. 可控可退：文件限定在工作区，敏感路径受保护；编辑带乐观锁和事务号，可防覆盖并回滚；危险命令分级确认，灾难性命令永久阻断，子进程不继承凭据。
4. 稳定运行：API 指数退避，异常转为模型可见观察；工具参数校验，超时与输出截断，重复调用熔断，步数上限强制收敛。
5. 长程上下文：按完整 assistant-tool 事务压缩旧历史，保留需求、决策和关键结果，不破坏工具调用协议。

常用选项：--approval ask/auto/read-only，--max-steps，--context-tokens，--show-reasoning。输入 /help 查看交互命令。详细架构、风险边界与答辩说明见 docs/DESIGN.md；配置方法见 docs/SETUP.md；演示流程见 docs/DEMO.md。

