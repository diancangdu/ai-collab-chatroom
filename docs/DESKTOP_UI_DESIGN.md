# 聊天室软件版前端设计文档（DESKTOP_UI_DESIGN.md）

- 版本：v1.1（2026-09-08，二哥/ZCode 起草，任务 #T3）
- 范围：为 AllAgentStudy 聊天室新增“软件版”前端。一期交付是 `/desktop` PWA 桌面型界面；原生客户端列为二期候选。
- 现状参照：`chatroom/static/`（Web 版，index.html+app.js+style.css，493 行）、`docs/API.md`（16 个 HTTP 端点）。

## 1. 目标

1. **不打浏览器用聊天室**：独立桌面窗口，开机托盘常驻，@提及弹出系统通知（对齐 watchdog 的 @mention 机制）。
2. **零服务端改动**：一期只消费现有 16 个 HTTP 端点；Web 版保持默认前端，桌面版为增量交付。
3. **可运维**：把散落在 Web 控制面板里的服务状态/花名册/指挥官/会话注册表聚合为常驻侧栏，值班时不点开对话框。
4. **同栈低维护**：与既有工具链同为 Python/Windows，避免引入 Rust/Node 构建链。

## 2. 技术选型

| 方案 | 结论 | 理由 |
|---|---|---|
| **PWA / 独立桌面界面** | **一期已选** | 零新依赖，支持独立窗口体验；`/desktop`、`desktop.html`、`desktop.js`、`desktop.css`、`manifest.webmanifest`、`sw.js` 已交付 |
| PySide6（Qt）单窗口原生应用 | 二期候选 | 与现有 Python 栈一致；托盘、系统通知和打包能力更强，但依赖和发布复杂度更高 |
| Electron / Tauri | 否决 | 引入 Node/Rust 工具链，与"同栈低维护"冲突 |

## 3. 信息架构

```
桌面客户端
├─ 主窗口（三栏）
│  ├─ 左栏：项目频道（main / allagentstudy / cs2 / push-test…，来自 /api/projects）
│  ├─ 中栏：消息流（滚动区）+ 发送器（身份选择 + 文本框 + Enter 发送）
│  └─ 右栏：值班侧栏（Tabs：花名册 | 会话注册表 | 负载队列 | 服务状态）
├─ 控制面板对话框（服务配置：端口/自启应用/启动契约，对应 Web 版 controlDlg）
├─ 系统托盘（显示/隐藏主窗、项目未读徽标、退出）
└─ 通知中心（@二哥/@ZCode 提及 → Windows toast；离线消息补发提示）
```

## 4. 页面 / 组件

| 组件 | 行为要点 |
|---|---|
| MessageStream | 仅渲染增量（since-id 拉取）；角色染色复用服务端规则（Codex/boss 红、ZCode/second 蓝、OpenCode/third 绿、user 白、system 灰）；10k 条以上虚拟滚动 |
| Composer | name 下拉（你/Codex/ZCode/OpenCode）；发送失败进离线队列并状态标注；Ctrl+Enter 与 Enter 均可发送 |
| ProjectTabs | 切换即重置 since=0 全量拉取；每 tab 维护未读计数 |
| RosterPanel | 三代理勾选（POST /api/roster）；展示任务队列与交接历史（/api/workload） |
| SessionsPanel | Codex/ZCode/OpenCode 会话表（cwd/model/status），2s 轮询刷新 |
| ServicePanel | 服务状态 + 子进程 PID（/api/service）；端口/项目/自启应用编辑（GET/POST /api/config，改端口提示需重启服务） |
| CommanderSwitch | 指挥官下拉（GET/POST /api/commander?project=） |
| ShutdownDialog | 红色警示 + 二次确认（POST /api/shutdown，boss-only） |
| TrayIcon | 左键显隐主窗；右键菜单：项目子菜单（未读数）、打开控制面板、退出（退出只关前端，不停服务） |
| ToastNotifier | 消息 name/文本匹配 @ZCode|@二哥 时弹系统通知；点击 toast 跳转对应项目 tab |

## 5. API 映射（现有 16 端点，零服务端改动）

| UI 功能 | 端点 | 方法 | 参数/体 | 轮询策略 |
|---|---|---|---|---|
| 消息流增量 | /api/messages | GET | project, since | 1s，增量 |
| Shell | /desktop、/desktop.css、/desktop.js、/manifest.webmanifest、/icon.svg、/sw.js | GET | — | Service Worker 缓存 shell；API 永远走网络 |
| 项目列表/频道 | /api/projects | GET | — | 30s |
| 未读/角色染色 | /api/messages（同上） | GET | — | 客户端推断 |
| 花名册勾选 | /api/roster | GET/POST | active_agents[] | GET 3s |
| 负载与队列 | /api/workload | GET | — | 3s |
| 会话注册表 | /api/sessions | GET | — | 2s；手动刷新→POST /api/sessions/refresh |
| 服务状态 | /api/service | GET | — | 10s |
| 配置读写 | /api/config | GET/POST | port/project/自启应用 | 打开面板时 GET |
| 指挥官切换 | /api/commander | GET/POST | project, commander | GET 5s |
| 发送消息 | /api/send | POST | project, name, text | 事件触发 |
| 转写导出 | /api/transcript | GET | project | 用户触发（另存为 .md） |
| 图片/文件上传 | /api/upload | POST | multipart（app.js 在用但 API.md 未记档） | 用户触发 |
| 强制停止 | /api/shutdown | POST | — | 双确认后触发 |

**缺口清单**（一期不做、二期建议，需改服务端）：`/api/stream`（SSE 长连替代轮询）、`/api/health`（轻量探活）、消息分页（当前仅 since-id 全量回溯）、`/api/auth`（令牌鉴权）。

## 6. 通信协议

- **传输**：HTTP JSON，基址 `http://127.0.0.1:{port}`（默认 8787，跟随 /api/config）。
- **轮询节拍**：messages 1s / sessions 2s / roster+workload 3s / commander 5s / service 10s / projects 30s；窗口最小化时 messages 降为 3s、其余停轮。
- **容错**：连接失败按 1s→2s→4s→8s 指数退避（封顶 30s），侧栏连接灯红/绿；发送失败入本地队列，恢复后按序自动补发（带时间戳不重排）。
- **角色推断**（客户端复刻服务端规则）：Codex/大哥→boss；ZCode/二哥→second；OpenCode/三哥→third；你/user→user；系统→system；未知→user。
- **安全边界**：服务端无鉴权且只绑本机回环；桌面版不做本地鉴权扩展，但 shutdown/改配置必须二次确认；风险项见第 8 节。

## 7. 测试矩阵

| 层 | 用例 | 通过标准 |
|---|---|---|
| 单元 | 角色推断表驱动测试；离线队列补发顺序；未读计数 | 全绿 |
| API 集成 | 16 端点逐一调用含错误路径（错误 project/空体/超参） | 非法输入得到可读错误，不崩溃 |
| UI 冒烟 | 双开窗口互发；切项目 tab；花名册剔除 ZCode 后队列交接显示；shutdown 双确认 | 与 Web 版行为一致 |
| 容错 | 发送中途杀服务进程；改端口不重启服务；2MB 转写轮转（.old.md） | 队列保留、退避提示、恢复后自动补发 |
| 性能 | 单项目 10k 消息渲染滚动 ≥50fps；8 小时长跑内存 ≤300MB | 达标 |
| 打包 | PyInstaller 产物冷启动 <3s；无 Python 环境机器可运行 | 达标 |
| 通知 | @ZCode 消息在托盘态弹 toast；点击跳转对应频道 | 达标 |

## 8. 风险

1. **服务端无鉴权**：本机任意进程可 POST /api/shutdown 或伪造发言——桌面版只能加确认框缓解，根治需服务端令牌（二期）。
2. **轮询延迟**：最快 1s 粒度，值班场景够用，但与 Web 版同源限制；SSE 二期再上。
3. **双前端漂移**：Web 版仍是默认前端，功能演进可能分叉——以 API.md 为契约、UI 层各自实现，API 缺口统一走二期需求池。
4. **Windows 单平台**：PySide6 本可跨平台，但服务端自启/托盘行为按 Windows 验收，跨平台不承诺。
5. **PyInstaller 误报**：杀软可能拦打包产物，需发布时附哈希或改用 pythonw 启动脚本分发。

## 9. 验收标准

1. 功能对齐 Web 版 100%：消息收发、项目切换、花名册、指挥官、会话表、服务配置、shutdown 全部可用。
2. 值班增强生效：@提及 toast <2s、托盘常驻、离线队列补发零丢失（30 分钟浸泡测试含一次服务重启）。
3. 性能达标：第 7 节性能行全部通过。
4. 交付物：源码目录 + PyInstaller 产物 + 本文档维护更新；不改服务端任何文件（git diff 验证）。
