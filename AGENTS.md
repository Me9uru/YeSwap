# AetherSwap 协作指南

本文件适用于仓库根目录及全部子目录，是架构边界与开发约定的唯一来源。开始修改前
先阅读 `README.md`。

## 项目概览

- Python 3.10+ 后端，使用 FastAPI、SQLModel/SQLite、Playwright；依赖由 `uv` 管理。
- 前端位于 `web/`，使用 Vue 3、Element Plus、Vite 和 ES Modules；生产资源构建到
  `web/dist/`。
- `run.py` 和 `python -m app` 是纯 Web 服务入口，默认端口为 `28472`；项目不使用桌面 WebView。
- 运行数据默认写入 `config/`；可在导入应用模块前通过 `AETHERSWAP_DATA_DIR`
  指向独立目录。
- 根目录 `.env` 由 `python-dotenv` 在 `app` 初始化时加载，且不覆盖进程中已经存在的
  同名环境变量；该文件是本地配置，不得提交。

## 修改原则

- 先检查 `git status`。仓库可能包含用户尚未提交的修改；不要回退、覆盖或顺手
  格式化任务范围外的文件。
- 修改应小而聚焦。修复缺陷时优先补充能复现问题的回归测试，不进行无关重构。
- 配置字段和持久化格式默认保持兼容；必须破坏兼容时提供显式迁移逻辑。Python 内部导入
  路径不保留旧模块转发层，调用方与测试必须直接引用实际所属子包。
- 不使用通配导入、动态模块别名或复制实现来绕过模块边界。
- 新增依赖时同步更新锁文件：Python 使用 `uv add`/`uv lock`，前端使用 npm；不要手工
  编辑锁文件。
- 用户可见文本和项目文档使用中文；代码标识符沿用现有英文风格。

## 架构边界

依赖方向为：

```text
routes -> workflows / services -> 业务域 -> storage / 平台适配器 -> utils
```

- `app/routes/` 只处理 HTTP 参数、响应协议和服务调用，不放业务实现。
- `app/workflows/`、`app/services/` 及其他业务子包不得导入 FastAPI 或 `app.routes`。
- `steam/`、`buff/`、`steamdt/`、`analysis/`、`utils/` 不得导入 `app`；应用配置、
  日志、代理和持久缓存能力通过显式参数或启动时注入，独立适配器默认不读取应用配置。
- `app/api.py` 仅负责应用、路由和生命周期组装；新的后台业务放入
  `app/background/`，并通过任务队列接线，不得再向单一 worker 文件堆积不同业务。
- `app/` 根目录只允许 `__init__.py`、`__main__.py`、`api.py`、`main.py` 和
  `runtime_env.py`。业务代码必须放入明确的领域子包；禁止创建旧路径转发、聚合导出或
  `sys.modules` 别名来维持内部导入兼容。
- 前端入口位于 `web/src/`。跨模块可变状态放进
  对应的 `*State.js`；不要新增隐式全局变量、依赖脚本加载顺序或向 `window` 挂载业务函数。

架构约束由 `tests/test_architecture.py` 持续验证。

## 业务归属

| 目录或模块 | 职责 |
|---|---|
| `workflows/purchase.py`、`sale.py`、`receiving.py` | 购入、出售和收货的跨域编排 |
| `workflows/maintenance.py` | 备份导入与购买工作流之间的维护互斥 |
| `accounts/repository.py` | 账号元数据及浏览器资料目录 |
| `configuration/schema.py`、`repository.py` | 配置校验、持久化及平台凭证访问 |
| `purchase/selection.py`、`market.py` | 候选筛选、稳定性、平台报价与预检 |
| `purchase/checkout.py` | 购买前验证、采购量与单件/批量路径选择 |
| `purchase/single.py`、`batch.py` | 各自购买请求及结果分类；共享显式 `PurchaseContext` |
| `purchase/payment.py` | 通知、邮件等待、订单关联的人工确认 |
| `purchase/settlement.py`、`identity.py` | 结算落库、部分成交、凭证一致性 |
| `strategies/catalog.py`、`module_specs.py`、`system_presets.py`、`registry.py` | 策略约束、内置模块、系统预设及用户模块登记 |
| `workflows/schedule.py` | 购买时段和配置时区规则 |
| `services/buff_browser_probe.py` | BUFF 浏览器页面挑战检测与用户代理读取 |
| `steam_market/listing_response.py` | Steam 上架响应判定与日志脱敏 |
| `strategies/repository.py` | 策略文件读写、进程内写操作互斥、原子替换 |
| `strategies/validation.py`、`runtime.py` | 合法性检查及现有配置兼容转换 |
| `strategies/expressions.py`、`rules.py`、`simulation.py` | 声明式条件、共享纯规则、样例模拟 |
| `storage/` | 交易及行情缓存模型与仓储；连接统一管理 |
| `services/auth_session.py`、`steam_guard.py` | 登录会话及令牌，各自独立于路由 |
| `services/transactions.py`、`holdings.py` | 交易应用操作和共用持仓补价 |
| `services/backup.py`、`maintenance.py` | 备份恢复和数据重置；原有维护互斥门禁保留 |
| `steam_market/` | Steam 库存、价格、挂单、撤单及市场确认 |
| `notifications/service.py` | PushPlus、邮件付款确认及持仓报告 |
| `maintenance/` | 历史成交同步及错误交易记录修复 |
| `gifts/engine.py` | 可选 Steam 赠礼应用流程 |
| `runtime/state.py` | 进程内状态、日志、付款条件变量及停止状态 |
| `runtime/pipeline_control.py` | 数据重置后的全局停机标记 |
| `utils/money.py` | Steam 展示总价与预计到账换算，币种及最小单位工具 |
| `steam/account.py` | 钱包和地区查询，不经由赠礼引擎 |

`runtime.State` 保留运行状态、日志和付款条件变量。交易查询与写入的实现仅存在于交易
仓储，运行态只提供流程所需调用，不保存第二份交易缓存。认证服务由 `app/api.py` 注入
购买启动函数，服务层不反向导入工作流。HTTP 请求模型由路由定义，服务函数接收显式参数。

## 持久化与退出

应用包初始化时加载根目录 `.env`，`AETHERSWAP_DATA_DIR` 必须在导入应用模块前确定，
默认是仓库 `config/`。凭证、账号、数据库、汇率、策略、checkout 日志及浏览器资料目录
均使用该目录。

策略保存、删除、激活和模块导入在同一个进程内互斥；JSON 使用同目录临时文件、
flush/fsync 和原子替换。此锁不宣称支持多个应用进程共享数据目录。BUFF checkout 原有
持久门禁和跨进程锁保持独立。

应用停止事件与流水线暂停分离。后台等待使用可中断的 `wait()`，关闭时先请求停止再回收
线程池。正在进行的网络调用仍受各自超时约束，不强制打断不可判定的交易写入。Uvicorn
收到退出信号后通过 FastAPI 生命周期通知 worker 停止。

## 前端

前端通过 FastAPI 作为纯 Web 界面提供，不使用桌面 WebView。
`web/src/main.js` 只挂载应用；`App.vue` 通过 `ElConfigProvider` 设置中文，并管理导航、
主题和页面生命周期，按需加载业务页面与付款弹窗。各 Vue 组件显式导入所需的 Element Plus
组件与样式；`views/` 提供响应式业务页面，`widgets/` 提供付款弹窗和数字输入控件。
`services/` 管理 API 协议、配置增量和策略编辑规则；`state/*State.js` 管理跨页状态与
刷新任务，`composables/` 管理请求互斥及错误反馈。旧 DOM 业务层和对应测试已删除；统计
聚合位于 `web/src/services/analytics.js`。新功能直接在 Vue 层实现，不向 `window`
挂载业务函数。

Vite 将生产资源构建到 `web/dist/`，FastAPI 只托管该目录。修改前端源码后运行
`npm --prefix web run build`；本地前后端分离开发可使用 `npm --prefix web run dev`，其
`/api` 请求代理到 `127.0.0.1:28472`。

库存与市场价定时器随应用挂载与卸载统一启动、回收，读取已保存的刷新间隔。设置只提交修改
字段，不以旧配置快照覆盖策略中心的更新。付款确认固定捕获所核对的订单号；对账保留
`intent_id`；交易记录优先携带稳定数据库 ID，实际到账使用 `sale_proceeds`。请求期间禁止
重复提交，失败不自动重试写入。策略模拟在草稿变化后失效；自定义策略启用前必须模拟并确认
差异；固定模块、依赖与互斥保留前后端校验。

赠礼 API 保持兼容，当前 UI 不提供入口。游戏折扣及卡片功能已移除；旧折扣表数据不会由
启动迁移删除。

## 交易与数据安全

这是会接触真实账号、凭证和资金状态的交易应用。修改购买、付款、收货、上架、撤单、
认证或对账代码时：

- 保留幂等、未知结果门禁、账号/资产归属校验、凭证冻结和人工对账语义。
- 外部请求失败或响应含糊时，不得把结果推断为成功，也不得自动重试可能产生重复订单的
  非幂等写操作。
- 金额必须明确平台币种、展示金额与最小货币单位；沿用现有转换函数，不在调用处散落
  `* 100`、汇率或手续费计算。
- 不移除 BUFF 请求节流、checkout 持久门禁、跨进程锁或 Steam 风控保护。
- 文件持久化沿用同目录临时文件、flush/fsync 和原子替换；数据库写入沿用既有事务边界。
- 日志和异常信息不得输出 Cookie、密码、`shared_secret`、`identity_secret`、邮箱授权码、
  代理凭据或完整支付信息。

除非用户明确要求且确认风险，禁止使用真实账号执行下单、付款、收货、上架、撤单、登录
或其他外部写操作。

## 测试隔离

- Python 测试由 `tests/conftest.py` 在收集阶段设置临时 `AETHERSWAP_DATA_DIR`，并在每个
  用例中隔离数据库、账号、凭证、策略和 checkout 状态。架构测试阻止适配器反向依赖应用、
  业务层依赖路由，并限制 `app/` 根目录只能包含入口模块。
- 测试默认禁止外部网络。新增测试应 mock 传输层，不得放宽这一保护，也不得读取仓库
  `config/` 下的真实运行数据。
- 手工验收后端时应使用新建的临时数据目录，并避免让验证过程污染用户配置、数据库或
  Playwright 浏览器资料。
- 策略模拟是样例执行，不是历史回测或实盘验证。未真实计算的步骤必须标为 `sample`，
  交易动作必须跳过。
- 前端测试使用 Vitest/Vue Test Utils 的组件交互，覆盖页面切换、配置增量、策略模拟失效、
  付款订单变化、重复点击和到账金额字段。测试 mock 所有请求。
  `npm --prefix web run preview:isolated` 提供独立视觉验收页面：API 使用内存样例，拒绝
  写请求，不会连接真实后端。

## 测试取舍

测试只保护可观测行为与安全契约。新增测试前先确认没有等价覆盖，每个测试都应能指出它
防止的具体回归；测试数量本身不是目标。

- 不写实现细节断言：mock 调用次数与顺序、私有属性与私有函数、日志文案、源码字符串、
  位置参数下标。这类断言在重构时碎裂，却抓不到真实回归。
- 不写没有失败路径的守护测试：断言常量等于自身、复述 `DEFAULTS` 或白名单，或被测对象
  已无生产调用方。
- 不写重复覆盖：同一行为分支已有等价或更强断言时，合并进现有测试（加参数或加断言），
  不新开用例。参数化用例的每个分支都要有独立语义。
- 重构脚手架测试随重构一起删除：断言旧文件已删除、旧路由未注册、旧模块无引用这类
  一次性守卫，在重构结束、功能稳定后不再有回归价值。
- 精简测试时不得移除任何行为分支的唯一覆盖；删除前先确认该分支在别处仍有等价断言。
- 覆盖归属校验、幂等、未知结果门禁、金额与币种换算、节流、凭证冻结与人工对账的测试
  不受上述规则影响，即使看起来与相邻测试相似也必须保留。

## 模拟边界

模拟使用样例市场数据，响应包含 `mode: sample`。最高折扣规则会按当前提交参数执行，与真实
流程共享 `discount_is_acceptable()`；声明式条件也会执行。其他尚未实现规则计算的步骤返回
`sample`，不能宣称通过。真实交易动作始终跳过。这不是历史回测或完整实盘演练。

## 验证命令

根据改动范围先运行最小相关测试，再在交付前运行适当的完整检查：

```sh
# 单个或相关 Python 测试
uv run pytest tests/test_xxx.py -q

# Python 全量测试与静态检查
uv sync
uv run pytest -q
uv run ruff check app buff steam steamdt analysis utils config

# 前端模块与组件交互测试（Node.js 22+）
npm --prefix web ci
npm --prefix web test
npm --prefix web run build

# 只读样例预览（不连接真实后端）
npm --prefix web run preview:isolated

# 检查空白错误
git diff --check
```

纯文档改动至少运行 `git diff --check`。修改 Python 模块归属或依赖方向时必须运行
`tests/test_architecture.py`；修改前端模块、HTML 或事件绑定时必须运行
`npm --prefix web test`。涉及购买、付款、收货、上架、币种或持久化的改动，应同时运行对应
安全回归测试和 Python 全量测试。

DOM 测试不能替代真实浏览器的视觉或拖拽验收。没有完成某项验证时，应在交付
说明中明确写出，不得将其表述为已通过。

## 交付要求

- 说明改了什么，以及改变行为的原因。
- 列出实际执行的验证命令和结果；未执行的检查说明原因。
- 提醒剩余风险，尤其是未经真实网络、浏览器或交易流程验证的部分。
- 不提交本地运行数据、日志、备份、浏览器资料、`.env`、凭证或密钥。
