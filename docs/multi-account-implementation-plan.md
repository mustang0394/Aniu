# 多妙想 Key、多独立模拟账户实施方案

> 本文档交给另一位工作模型或开发者后，应能够直接按阶段实施。它描述目标、代码边界、数据迁移、运行时逻辑、接口契约、前端行为和验收方式。实现时以仓库当前代码和测试为最终事实来源；若代码已经变化，先重新核对对应符号和调用链。

## 1. 目标与边界

当前系统是一个全局 AppSettings、一个妙想 Key、一套模拟交易仓位和一套调度/运行逻辑。目标是抽出 TradingAccount 层，使每个账户成为一套独立的交易子系统：

~~~text
全局系统设置
  ├── 应用显示和登录
  ├── 全局默认 LLM
  ├── 全局 UZI Key、UZI LLM 和 UZI 报告库
  └── 全局技能目录管理

TradingAccount A
  ├── 妙想 Key A 及其外部模拟仓位
  ├── 独立提示词、决策风格和选股范围
  ├── 独立 Skills 选择
  ├── 可选的独立 LLM
  ├── 独立自动化会话
  ├── 独立定时任务
  ├── 独立运行/订单历史
  └── 独立总览缓存

TradingAccount B
  └── 与 A 同构，所有运行数据和外部 Key 隔离
~~~

必须满足：

1. 一个妙想 Key 对应一个交易账户。
2. 不同账户的资金、持仓、订单、提示词、市场范围、Skills、自动化上下文、任务、运行记录和缓存互不影响。
3. 不同账户可以并发运行；同一账户默认串行执行交易型任务。
4. 每个账户可以有零个或多个自己的定时任务。
5. 账户可以配置自己的大模型；未配置或配置不完整时使用全局大模型。
6. UZI 报告保持全局，所有账户都可以通过 uzi_report_context 查询。
7. UZI 报告生成只使用全局 LLM，不随交易账户切换。
8. 旧单账户数据库升级后自动创建默认账户并保持原有行为。

本次不做：

- 多用户权限、租户、团队和角色系统。
- 自己实现模拟交易撮合或本地仓位账本；仓位继续由妙想 Key 对应的外部模拟账户管理。
- 将 UZI 报告复制到每个账户。
- Celery、Redis 或外部任务队列。
- 修改 analysis/trade/chat 的工具安全边界。

## 2. 当前实现事实和最高风险

实施前必须阅读 CLAUDE.md 及以下文件：

~~~text
backend/app/db/models.py
backend/app/db/database.py
backend/app/services/aniu_service.py
backend/app/services/llm_service.py
backend/app/services/chat_session_service.py
backend/app/services/skill_admin_service.py
backend/app/skills/catalog.py
backend/app/skills/runtime.py
backend/app/skills/registry.py
backend/app/skills/providers.py
backend/app/skills/context.py
backend/skills/mx_core/
backend/skills/uzi_report_context/
frontend/src/stores/legacy.ts
frontend/src/services/api.ts
frontend/src/views/OverviewView.vue
frontend/src/views/ScheduleView.vue
frontend/src/views/TasksView.vue
frontend/src/views/SettingsView.vue
~~~

当前关键事实：

- AppSettings 是单行全局配置，混合保存妙想 Key、LLM、提示词、市场、风控、Telegram 和自动化会话。
- StrategySchedule、StrategyRun 没有账户外键。
- AniUService._run_lock 是全局锁，一个账户运行时会阻塞所有账户。
- 账户总览缓存是一份全局缓存。
- _get_recent_account_snapshot 会从所有运行记录寻找最近账户工具结果。
- automation_session_id 当前在 AppSettings 中。
- build_skill_context 和多个 Skill 通过 app_settings 推导妙想 Client。
- SkillRegistry/SkillCatalog 是全局单例，不能通过修改全局 disabled 状态实现账户隔离。
- UZI Stage 1 当前从 AppSettings.mx_api_key 取 Key。
- 数据库无 Alembic，新增字段必须同时修改 SQLAlchemy 模型和 init_db 的幂等迁移。
- 前端 store 只有一份 settings、schedules、account 和 runtimeOverview。

最高风险是仍然存在隐式全局账户状态。只增加账户选择器或只给 StrategyRun 增加 account_id 都不算完成。必须让每次运行显式携带账户上下文，并让妙想 Client、Skills、缓存、自动化会话和调度都从该上下文读取。

## 3. 总体实施顺序

1. 建立基线和临时 SQLite 测试工具。
2. 新增 TradingAccount 模型。
3. 扩展数据库迁移并创建默认账户。
4. 新增账户 Schema、CRUD 和脱敏逻辑。
5. 实现 ResolvedLLMConfig 和 AccountRunContext。
6. 给调度、运行、会话增加账户归属。
7. 改造妙想 Client、SkillContext、SkillRuntime 和账户缓存。
8. 用账户锁和任务 lease 替换全局运行锁。
9. 实现账户级调度和重试隔离。
10. 保持 UZI 全局并迁移 UZI Key。
11. 增加账户级和全局聚合 API。
12. 改造聊天与自动化会话。
13. 改造前端账户管理、调度、任务、总览和聊天。
14. 增加迁移、并发、Key 隔离和 Skills 隔离测试。
15. 运行旧数据库迁移演练、全量测试和 Docker 构建。

每阶段完成后先运行对应测试，不要最后一次性排错。

## 4. 阶段 0：基线

先执行：

~~~bash
cd backend
./.venv/bin/pytest
cd ../frontend
npm run build
~~~

记录：

- 后端测试结果。
- 前端类型检查结果。
- SQLite 表结构。
- AppSettings 是否存在以及旧 mx_api_key 是否有值。
- 当前任务、运行、订单和自动化会话数量。
- 是否存在 data/aniu.sqlite3、旧 data/aniu.db 或备份。

在 backend/tests 增加统一工具：

~~~python
def reset_test_database(monkeypatch, tmp_path):
    # monkeypatch SQLITE_DB_PATH
    # get_settings.cache_clear()
    # database._engine = None
    # database._session_local = None
    # init_db()

def create_account(db, **overrides):
    ...

def create_schedule(db, account_id, **overrides):
    ...

def create_run(db, account_id, **overrides):
    ...
~~~

所有测试必须使用临时数据库，不得污染项目 data 目录。

## 5. 阶段 1：数据库模型

### 5.1 新增 TradingAccount

在 backend/app/db/models.py 新增表 trading_accounts：

~~~python
class TradingAccount(Base):
    __tablename__ = "trading_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    slug: Mapped[str] = mapped_column(String(96), unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    mx_api_key: Mapped[str | None] = mapped_column(String(512), nullable=True)

    account_llm_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    llm_provider_name: Mapped[str | None] = mapped_column(String(32), nullable=True)
    llm_base_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    llm_api_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    llm_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    llm_reasoning_effort: Mapped[str | None] = mapped_column(String(64), nullable=True)
    llm_max_retries: Mapped[int | None] = mapped_column(Integer, nullable=True)
    llm_enable_reasoning_content_echo: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )

    system_prompt: Mapped[str] = mapped_column(Text)
    analyst_prompt: Mapped[str] = mapped_column(Text)
    market_query: Mapped[str] = mapped_column(String(255))
    news_query: Mapped[str] = mapped_column(String(255))
    screener_query: Mapped[str] = mapped_column(String(255))
    max_actions: Mapped[int] = mapped_column(Integer, default=2)
    trade_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    allowed_markets_json: Mapped[str] = mapped_column(Text)

    disabled_skill_ids_json: Mapped[str] = mapped_column(Text, default="[]")

    automation_session_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    automation_context_window_tokens: Mapped[int] = mapped_column(
        Integer, default=128000
    )
    automation_recent_message_limit: Mapped[int] = mapped_column(
        Integer, default=24
    )
    automation_enable_auto_compaction: Mapped[bool] = mapped_column(
        Boolean, default=True
    )
    automation_idle_summary_hours: Mapped[int] = mapped_column(Integer, default=12)
    automation_context_source: Mapped[str | None] = mapped_column(
        String(32), nullable=True, default="default"
    )
    automation_context_detected_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )

    tg_bot_token: Mapped[str | None] = mapped_column(String(512), nullable=True)
    tg_chat_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    tg_notify_trade_enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    capital_seal_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    capital_seal_amount: Mapped[float] = mapped_column(Float, default=0.0)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
~~~

规则：

- mx_api_key 是账户对应的妙想 Key。
- 妙想 Base URL 继续使用全局环境配置。
- account_llm_enabled 控制账户级大模型是否参与解析。
- disabled_skill_ids_json 保存账户禁用的 Skill ID。
- archived 账户不能创建新任务或运行，但仍保留历史。
- name 不要求唯一，slug 必须唯一。
- 不物理删除账户。

### 5.2 收缩 AppSettings 的业务职责

AppSettings 保留全局字段：

~~~text
app_display_name
provider_name
llm_base_url
llm_api_key
llm_model
llm_reasoning_effort
llm_max_retries
llm_enable_reasoning_content_echo
disabled_skill_ids_json
uzi_mx_api_key
created_at
updated_at
~~~

增加：

~~~python
uzi_mx_api_key: Mapped[str | None] = mapped_column(
    String(512), nullable=True
)
~~~

以下旧字段可以保留在 ORM 中用于迁移，但新运行逻辑不得读取：

~~~text
mx_api_key
system_prompt
analyst_prompt
market_query
news_query
screener_query
max_actions
trade_enabled
allowed_markets_json
automation_session_id
automation_context_*
tg_*
capital_seal_*
~~~

### 5.3 StrategySchedule

增加：

~~~python
trading_account_id: Mapped[int] = mapped_column(
    ForeignKey("trading_accounts.id"),
    index=True,
)
lease_token: Mapped[str | None] = mapped_column(
    String(64), nullable=True
)
lease_until: Mapped[datetime | None] = mapped_column(
    DateTime, nullable=True
)
~~~

每个任务必须有一个账户归属。服务层的所有任务查询和更新都必须带 trading_account_id。

### 5.4 StrategyRun

增加：

~~~python
trading_account_id: Mapped[int] = mapped_column(
    ForeignKey("trading_accounts.id"),
    index=True,
)
trading_account_name_snapshot: Mapped[str | None] = mapped_column(
    String(64), nullable=True
)
llm_config_source: Mapped[str | None] = mapped_column(
    String(16), nullable=True
)
llm_model_snapshot: Mapped[str | None] = mapped_column(
    String(128), nullable=True
)
~~~

运行创建时记录账户 ID、启动时账户名、实际 LLM 来源和模型。不要将明文 Key 写入运行 JSON。

### 5.5 ChatSession

增加：

~~~python
trading_account_id: Mapped[int] = mapped_column(
    ForeignKey("trading_accounts.id"),
    index=True,
)
~~~

自动化会话 slug 使用 automation-{account_id}，一个账户最多一个 automation 会话。普通聊天也绑定账户。

### 5.6 TradeOrder 和 UziReportJob

TradeOrder 通过 run_id 间接归属账户。查询订单时必须 join/filter StrategyRun.trading_account_id。

UziReportJob 不增加账户 ID，报告库保持全局。

## 6. 阶段 2：数据库迁移

在 backend/app/db/database.py 扩展 init_db：

~~~python
def init_db() -> None:
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    _ensure_app_settings_columns(engine)
    _ensure_trading_account_columns(engine)
    _ensure_strategy_schedule_columns(engine)
    _ensure_strategy_run_columns(engine)
    _ensure_chat_session_columns(engine)
    _ensure_trading_account_indexes(engine)
    _backfill_default_trading_account(engine)
    _backfill_uzi_mx_api_key(engine)
    _backfill_schedule_accounts(engine)
    _backfill_run_accounts(engine)
    _backfill_chat_session_accounts(engine)
~~~

### 6.1 旧表新增账户列

SQLite 旧表迁移阶段先允许 NULL：

~~~sql
ALTER TABLE strategy_schedules ADD COLUMN trading_account_id INTEGER;
ALTER TABLE strategy_runs ADD COLUMN trading_account_id INTEGER;
ALTER TABLE chat_sessions ADD COLUMN trading_account_id INTEGER;
~~~

然后将所有旧记录回填到 slug=default 的账户：

~~~sql
UPDATE strategy_schedules
SET trading_account_id = (
    SELECT id FROM trading_accounts WHERE slug = 'default'
)
WHERE trading_account_id IS NULL;
~~~

运行和会话同理。服务层必须拒绝新建账户 ID 为空的记录。

### 6.2 默认账户

实现幂等 _backfill_default_trading_account：

~~~python
# 读取第一条 AppSettings
# 查找 slug="default" 的 TradingAccount
# 不存在时从旧 AppSettings 复制账户字段
# 创建 name="默认账户", slug="default"
# 重复启动不创建第二个账户
~~~

复制映射：

~~~text
AppSettings.mx_api_key              -> TradingAccount.mx_api_key
AppSettings.system_prompt           -> TradingAccount.system_prompt
AppSettings.analyst_prompt          -> TradingAccount.analyst_prompt
AppSettings.market_query            -> TradingAccount.market_query
AppSettings.news_query              -> TradingAccount.news_query
AppSettings.screener_query          -> TradingAccount.screener_query
AppSettings.max_actions             -> TradingAccount.max_actions
AppSettings.trade_enabled           -> TradingAccount.trade_enabled
AppSettings.allowed_markets_json    -> TradingAccount.allowed_markets_json
AppSettings.disabled_skill_ids_json -> TradingAccount.disabled_skill_ids_json
AppSettings.automation_*            -> TradingAccount.automation_*
AppSettings.tg_*                    -> TradingAccount.tg_*
AppSettings.capital_seal_*          -> TradingAccount.capital_seal_*
~~~

默认账户的独立 LLM 配置留空，使迁移后使用全局 LLM。

### 6.3 UZI Key

迁移逻辑：

~~~python
if not settings.uzi_mx_api_key:
    settings.uzi_mx_api_key = settings.mx_api_key
~~~

UZI 服务优先读取 uzi_mx_api_key。迁移兼容期间可以临时回退旧 mx_api_key，但一旦全局 Key 已明确配置，账户 Key 变化不得影响 UZI。

### 6.4 迁移测试

必须覆盖：

- 空库初始化。
- 旧单账户库初始化。
- 重复初始化。
- 旧 aniu.db 兼容。
- 默认账户只创建一次。
- 所有旧任务、运行和会话回填默认账户。
- 原有 Skill 禁用状态保留。
- UZI Key 不丢失。

## 7. 阶段 3：账户配置和运行上下文

建议新增：

~~~text
backend/app/schemas/accounts.py
backend/app/services/account_service.py
backend/app/services/account_context.py
~~~

### 7.1 账户 Schema

账户读模型包含：

~~~text
id
name
slug
enabled
archived
sort_order
mx_api_key 脱敏
has_mx_api_key
account_llm_enabled
llm_api_key 脱敏
has_account_llm_config
resolved_llm_source: account/global/none
llm_model
账户提示词、市场、策略和风控字段
账户 Skills 状态
created_at
updated_at
~~~

更新模型使用可选字段，避免完整表单更新时覆盖未加载的密钥：

~~~python
class TradingAccountUpdate(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    mx_api_key: str | None = None
    account_llm_enabled: bool | None = None
    llm_provider_name: str | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    # 其余账户字段均为 Optional
~~~

规则：

- 字符串含 **** 时保持原值。
- 空字符串明确清除。
- API 永不返回明文 Key。
- 日志只记录字段名，不记录字段值。
- 不能通过更新接口修改 slug。

### 7.2 ResolvedLLMConfig

在 account_context.py 定义：

~~~python
@dataclass(frozen=True)
class ResolvedLLMConfig:
    provider_name: str
    base_url: str | None
    api_key: str | None
    model: str
    reasoning_effort: str | None
    max_retries: int
    enable_reasoning_content_echo: bool
    source: Literal["account", "global"]
~~~

解析：

~~~python
def resolve_llm_config(account, global_settings):
    if account.account_llm_enabled:
        ready = (
            account.llm_base_url
            and account.llm_api_key
            and account.llm_model
        )
        if ready:
            return ResolvedLLMConfig(
                provider_name=account.llm_provider_name
                    or global_settings.provider_name,
                base_url=account.llm_base_url,
                api_key=account.llm_api_key,
                model=account.llm_model,
                reasoning_effort=account.llm_reasoning_effort,
                max_retries=(
                    account.llm_max_retries
                    or global_settings.llm_max_retries
                ),
                enable_reasoning_content_echo=(
                    account.llm_enable_reasoning_content_echo
                    if account.llm_enable_reasoning_content_echo is not None
                    else global_settings.llm_enable_reasoning_content_echo
                ),
                source="account",
            )

    if global_settings.llm_base_url and global_settings.llm_api_key:
        return ResolvedLLMConfig(
            provider_name=global_settings.provider_name,
            base_url=global_settings.llm_base_url,
            api_key=global_settings.llm_api_key,
            model=global_settings.llm_model,
            reasoning_effort=global_settings.llm_reasoning_effort,
            max_retries=global_settings.llm_max_retries,
            enable_reasoning_content_echo=(
                global_settings.llm_enable_reasoning_content_echo
            ),
            source="global",
        )

    raise RuntimeError("账户和全局大模型配置均不可用。")
~~~

账户配置必须整套有效才使用；不允许把账户 URL 和全局 Key 半混合。

### 7.3 AccountRunContext

定义不可变上下文：

~~~python
@dataclass(frozen=True)
class AccountRunContext:
    account_id: int
    account_name: str
    run_type: str
    schedule_id: int | None
    schedule_name: str | None
    task_prompt: str

    mx_api_key: str
    mx_api_base_url: str
    llm: ResolvedLLMConfig

    system_prompt: str
    analyst_prompt: str
    market_query: str
    news_query: str
    screener_query: str
    allowed_markets: tuple[str, ...]
    max_actions: int
    trade_enabled: bool

    disabled_skill_ids: frozenset[str]

    automation_session_id: int | None
    automation_context_window_tokens: int
    automation_recent_message_limit: int
    automation_enable_auto_compaction: bool
    automation_idle_summary_hours: int

    tg_bot_token: str | None
    tg_chat_id: str | None
    tg_notify_trade_enabled: bool

    capital_seal_enabled: bool
    capital_seal_amount: float
~~~

构造函数：

~~~python
def build_account_run_context(
    db,
    *,
    account_id: int,
    schedule_id: int | None,
    manual_run_type: str | None,
) -> AccountRunContext:
    # 读取全局设置
    # 读取并校验账户存在、enabled=True、archived=False
    # 读取并校验 schedule 属于账户
    # 解析 run_type 和 task_prompt
    # 解析账户/全局 LLM
    # 读取账户 disabled skills
    # 创建或获取账户自动化会话
    # 返回不可变上下文
~~~

过渡期可以把上下文转换成 SimpleNamespace 传给现有 llm_service，但运行链最终只能依赖 AccountRunContext。

## 8. 阶段 4：AI 运行链

### 8.1 _prepare_run

修改 AniUService._prepare_run：

~~~python
def _prepare_run(
    self,
    *,
    trigger_source: str,
    account_id: int,
    schedule_id: int | None,
    manual_run_type: str | None = None,
) -> tuple[int, AccountRunContext]:
~~~

步骤：

1. 读取全局设置。
2. 读取账户。
3. 校验账户启用且未归档。
4. 如果有 schedule_id，校验任务属于 account_id。
5. 解析 run_type、task_prompt。
6. 构造 AccountRunContext。
7. 创建 StrategyRun，写入账户 ID、账户名快照、LLM 来源和模型快照。
8. 返回 run_id 和上下文。

### 8.2 _run_body

修改为：

~~~python
def _run_body(
    self,
    *,
    run_id: int,
    account_context: AccountRunContext,
    trigger_source: str,
    schedule_id: int | None,
    emit=None,
    return_full_run=True,
):
~~~

步骤：

1. 用 account_context.mx_api_key 创建当前账户 MXClient。
2. 创建带账户选择的 SkillContext。
3. 创建账户 LLM settings adapter。
4. 准备账户自动化会话。
5. 调用 llm_service.run_agent_with_messages。
6. 从 tool history 提取实际交易动作。
7. 写入当前 StrategyRun 和 TradeOrder。
8. 只更新当前账户当前 schedule 的状态。
9. 使用账户 Telegram 配置发送通知。
10. 发布带账户 ID 和名称的 SSE 事件。
11. 在 finally 中释放账户锁。

SSE 事件示例：

~~~json
{
  "run_id": 123,
  "trading_account_id": 7,
  "trading_account_name": "趋势账户",
  "type": "stage"
}
~~~

### 8.3 运行记录查询

以下方法增加 account_id：

~~~text
list_runs
list_runs_page
get_runtime_overview
get_run
get_run_raw_tool_preview
delete_run
~~~

查询必须包含：

~~~python
StrategyRun.trading_account_id == account_id
~~~

get_run/delete_run 不能只按 run_id 操作而跳过账户归属校验。

### 8.4 自动化会话

将当前 _get_or_create_persistent_session(db) 改为：

~~~python
_get_or_create_persistent_session(
    db,
    trading_account_id=account_id,
)
~~~

查询：

~~~python
select(ChatSession).where(
    ChatSession.kind == "automation",
    ChatSession.trading_account_id == account_id,
    ChatSession.slug == f"automation-{account_id}",
)
~~~

摘要、压缩、消息和 run 关联都只使用当前账户会话。

## 9. 阶段 5：Skills 隔离

### 9.1 两层配置

全局层负责技能目录安装、删除、发现和全局硬禁用。账户层负责当前账户是否启用技能。

有效禁用集合：

~~~python
effective_disabled_ids = global_disabled_ids | account_disabled_ids
~~~

账户无法重新启用全局硬禁用技能。账户运行不得修改全局 SkillCatalog。

### 9.2 SkillRuntime

为以下方法增加 disabled_skill_ids：

~~~python
build_tools(
    *,
    run_type: str | None = None,
    disabled_skill_ids: Collection[str] | None = None,
)

execute_tool(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    context: dict[str, Any],
    disabled_skill_ids: Collection[str] | None = None,
)

build_prompt_supplement(
    *,
    run_type: str | None = None,
    disabled_skill_ids: Collection[str] | None = None,
)
~~~

通过只读过滤当前包：

~~~python
def iter_active_packages(self, disabled_ids):
    for package in self._catalog.all_packages():
        if package.always:
            yield package
            continue
        if not package.enabled:
            continue
        if package.id in disabled_ids:
            continue
        yield package
~~~

不能为每个运行设置全局“当前账户 disabled skills”。

### 9.3 SkillContext

build_skill_context 增加：

~~~python
context["trading_account_id"] = account_context.account_id
context["trading_account_name"] = account_context.account_name
context["account_context"] = account_context
context["disabled_skill_ids"] = account_context.disabled_skill_ids
context["mx_client_config"] = {
    "api_key": account_context.mx_api_key,
    "base_url": account_context.mx_api_base_url,
}
~~~

妙想工具必须从 context 的 client 调用，禁止 Handler 自己查询 AppSettings。

### 9.4 具体 Skill 约束

mx_core：

- 资金、持仓、订单和下单使用当前账户 Client。
- 市场限制使用账户 allowed_markets。
- 资金封印使用账户 capital_seal_*。
- 最大动作数使用账户 max_actions。
- analysis 仍然不能看到交易工具。

chat_context：

- get_account_overview 带账户 ID。
- list_runs_page 带账户 ID。
- get_run 校验账户 ID。
- 不允许跨账户读取。

uzi_report_context：

- 查询全局 UziReportJob。
- 不增加账户过滤。
- 不读取其他账户交易数据。
- 保留历史报告免责声明。

## 10. 阶段 6：账户总览和缓存

当前单份缓存改为：

~~~python
self._account_overview_cache: dict[int, dict[str, Any]]
self._account_overview_cache_expires_at: dict[int, datetime]
~~~

所有缓存读写必须接收 account_id。

### 10.1 最近运行快照

将 _get_recent_account_snapshot 改为按账户过滤：

~~~python
stmt = (
    select(StrategyRun)
    .where(StrategyRun.trading_account_id == account_id)
    .order_by(StrategyRun.started_at.desc())
    .limit(20)
)
~~~

账户 A 无法使用账户 B 的最近余额、持仓或订单结果。

### 10.2 账户总览服务

~~~python
def get_account_overview(
    self,
    account_id: int,
    *,
    include_raw: bool = False,
    force_refresh: bool = False,
) -> dict[str, Any]:
~~~

步骤：

1. 校验账户。
2. 读取账户配置。
3. 读取账户缓存。
4. 创建账户专属 MXClient。
5. 查询余额、持仓、订单。
6. 接口失败时只回退当前账户快照。
7. 应用当前账户资金封印。
8. 写回当前账户缓存。
9. 返回当前账户数据。

### 10.3 全局聚合

新增 get_global_overview，返回各账户和 aggregate：

~~~json
{
  "accounts": [
    {
      "account_id": 1,
      "account_name": "趋势账户",
      "status": "ok",
      "overview": {}
    }
  ],
  "aggregate": {
    "initial_capital": 0,
    "total_assets": 0,
    "cash_balance": 0,
    "total_market_value": 0,
    "holding_profit": 0,
    "daily_profit": 0,
    "total_return_ratio": null,
    "daily_return_ratio": null
  },
  "errors": []
}
~~~

金额求和；收益率按初始资金或前一日资产加权，不能直接相加。单个账户失败只标记该账户，不阻塞其他账户。

## 11. 阶段 7：账户级锁和调度

### 11.1 账户锁

删除全局运行锁的业务作用，新增：

~~~python
self._account_run_locks: dict[int, Lock] = {}
self._account_run_locks_guard = Lock()

def _get_account_run_lock(self, account_id: int) -> Lock:
    with self._account_run_locks_guard:
        return self._account_run_locks.setdefault(account_id, Lock())
~~~

手动、同步、异步和定时运行都使用同一账户锁：

~~~python
lock = self._get_account_run_lock(account_id)
if not lock.acquire(blocking=False):
    raise RuntimeError("该账户已有任务正在运行，请稍后再试。")
try:
    ...
finally:
    lock.release()
~~~

账户 A 的锁不能阻塞账户 B。

### 11.2 运行入口

execute_run 和 start_run_async 都增加 account_id：

~~~python
def execute_run(
    self,
    *,
    account_id: int,
    trigger_source: str = "manual",
    schedule_id: int | None = None,
    manual_run_type: str | None = None,
) -> StrategyRun:
~~~

~~~python
def start_run_async(
    self,
    *,
    account_id: int,
    trigger_source: str = "manual",
    schedule_id: int | None = None,
    manual_run_type: str | None = None,
) -> int:
~~~

### 11.3 调度 lease

任务抢占使用 lease_token 和 lease_until。原子更新示意：

~~~sql
UPDATE strategy_schedules
SET lease_token = :token,
    lease_until = :lease_until
WHERE id = :schedule_id
  AND enabled = 1
  AND (lease_until IS NULL OR lease_until < :now)
  AND (retry_after_at IS NULL OR retry_after_at <= :now)
  AND next_run_at <= :now
~~~

受影响行数为 1 表示抢占成功，0 表示跳过。完成、失败或取消必须清理 lease。

### 11.4 process_due_schedule

改造为：

1. 查询全部启用且到期任务。
2. 对每个任务尝试原子抢占。
3. 检查所属账户仍启用。
4. 按账户分组。
5. 同一账户同一时刻只派发一个交易型任务。
6. 不同账户允许并发。
7. 账户锁竞争失败时保留任务待下一轮。
8. 每个任务独立更新重试次数和下一次时间。

首期继续保留 scheduler 线程和后台 Thread，不引入 Celery。

### 11.5 状态更新

成功：

~~~text
last_run_at = finished_at
retry_count = 0
retry_after_at = null
next_run_at = compute_next_run_at(...)
lease_token = null
lease_until = null
~~~

失败：

~~~text
retry_count += 1
retry_after_at = now + 5 minutes
lease_token = null
lease_until = null
~~~

达到最大重试后清理 retry 状态，等待下一次正常 cron。一个账户失败不能修改另一个账户。

## 12. 阶段 8：UZI 全局改造

UZI 相关模块保持全局，不添加账户 ID：

~~~text
backend/app/api/uzi_router.py
backend/app/services/uzi_report_service.py
backend/app/services/uzi_llm_orchestrator.py
backend/app/services/uzi_worker_client.py
backend/uzi_worker/
~~~

修改点：

1. Stage 1 使用 AppSettings.uzi_mx_api_key。
2. UZI LLM Review 使用全局 AppSettings LLM。
3. UZI 报告不保存 trading_account_id。
4. uzi_report_context 查询全局报告。
5. 账户上下文只提供报告查询能力。
6. 账户 Key 变更不影响明确配置的 UZI Key。

回归：

~~~bash
cd backend
./.venv/bin/pytest tests/test_uzi_report_context_skill.py
./.venv/bin/pytest tests/test_uzi_llm_config_from_settings.py
./.venv/bin/pytest tests/test_uzi_flow.py
~~~

## 13. 阶段 9：后端 API

### 13.1 账户 API

~~~text
GET    /api/aniu/accounts
POST   /api/aniu/accounts
GET    /api/aniu/accounts/{account_id}
PATCH  /api/aniu/accounts/{account_id}
POST   /api/aniu/accounts/{account_id}/archive
POST   /api/aniu/accounts/{account_id}/restore
POST   /api/aniu/accounts/{account_id}/test-mx
POST   /api/aniu/accounts/{account_id}/test-llm
~~~

归档账户时停止其任务但不删除历史；恢复账户不自动重新启用任务。

### 13.2 账户 Skills

~~~text
GET /api/aniu/accounts/{account_id}/skills
PUT /api/aniu/accounts/{account_id}/skills
~~~

返回 global_available、global_hard_disabled、account_enabled、effective_enabled、always_enabled。

### 13.3 账户调度

~~~text
GET /api/aniu/accounts/{account_id}/schedule
PUT /api/aniu/accounts/{account_id}/schedule
~~~

PUT 只替换当前账户任务。

### 13.4 账户运行

~~~text
POST   /api/aniu/accounts/{account_id}/run
POST   /api/aniu/accounts/{account_id}/run-stream
GET    /api/aniu/accounts/{account_id}/runs
GET    /api/aniu/accounts/{account_id}/runs-feed
GET    /api/aniu/accounts/{account_id}/runs/{run_id}
GET    /api/aniu/accounts/{account_id}/runs/{run_id}/events
GET    /api/aniu/accounts/{account_id}/runs/{run_id}/raw-tool-previews/{index}
DELETE /api/aniu/accounts/{account_id}/runs/{run_id}
~~~

详情、删除和 SSE 必须验证 URL 账户 ID 与 run 所属账户一致。

### 13.5 总览

~~~text
GET /api/aniu/accounts/{account_id}/overview
GET /api/aniu/accounts/{account_id}/overview/debug
GET /api/aniu/overview
~~~

### 13.6 旧接口兼容

保留旧 settings、schedule、run、account、runs 接口作为过渡：

- 只有一个未归档账户时自动映射到该账户。
- 多账户时旧交易接口返回 409。
- 旧 settings 只管理全局设置。
- 新前端全部使用账户级 API。
- 旧接口不能绕过账户归属校验。

## 14. 阶段 10：聊天和会话

ChatSession 必须绑定账户。历史会话迁移到默认账户。

新增账户级聊天 API：

~~~text
GET  /api/aniu/accounts/{account_id}/chat/sessions
POST /api/aniu/accounts/{account_id}/chat/sessions
GET  /api/aniu/accounts/{account_id}/chat/sessions/{session_id}/messages
POST /api/aniu/accounts/{account_id}/chat/stream
GET  /api/aniu/accounts/{account_id}/persistent-session
GET  /api/aniu/accounts/{account_id}/persistent-session/messages
~~~

所有服务层操作必须验证 session.trading_account_id == account_id，否则返回 404。

账户聊天必须使用：

- 当前账户妙想 Client。
- 当前账户 LLM 解析结果。
- 当前账户提示词和 Skills。
- 当前账户持仓和运行历史。
- 当前账户自动化会话。
- 全局 UZI 报告 Skill。

多账户模式下没有 account_id 的旧聊天不能默认使用第一个账户。

## 15. 阶段 11：前端 API 和状态

### 15.1 类型

在 frontend/src/types.ts 增加 TradingAccount：

~~~typescript
export interface TradingAccount {
  id: number
  name: string
  slug: string
  enabled: boolean
  archived: boolean
  sort_order: number
  mx_api_key: string | null
  has_mx_api_key: boolean
  account_llm_enabled: boolean
  has_account_llm_config: boolean
  resolved_llm_source: 'account' | 'global' | 'none'
  llm_model: string | null
  system_prompt: string
  analyst_prompt: string
  market_query: string
  news_query: string
  screener_query: string
  max_actions: number
  trade_enabled: boolean
  allowed_markets: MarketKey[]
  disabled_skill_ids: string[]
  tg_notify_trade_enabled: boolean
  capital_seal_enabled: boolean
  capital_seal_amount: number
  created_at: string
  updated_at: string
}
~~~

ScheduleConfig 增加 trading_account_id 和 trading_account_name；RunSummary 增加账户名称快照、LLM 来源和模型快照。

### 15.2 API Client

在 frontend/src/services/api.ts 增加：

~~~typescript
listAccounts()
createAccount(payload)
updateAccount(accountId, payload)
archiveAccount(accountId)
restoreAccount(accountId)
testAccountMx(accountId)
testAccountLlm(accountId)

getAccountOverview(accountId, forceRefresh)
getGlobalOverview()

getAccountSchedule(accountId)
updateAccountSchedule(accountId, payload)

runAccountNow(accountId, scheduleId?, runType?)
runAccountNowStream(accountId, scheduleId?, runType?)
listAccountRuns(accountId, options)
getAccountRun(accountId, runId)
getAccountRunEventsUrl(accountId, runId)
~~~

所有交易性请求必须显式传账户 ID。

### 15.3 账户 Store

新增 frontend/src/stores/tradingAccounts.ts：

~~~typescript
const accounts = ref<TradingAccount[]>([])
const selectedAccountId = ref<number | null>(null)

const accountData = reactive<Record<number, {
  overview: AccountOverview | null
  runtimeOverview: RuntimeOverview | null
  schedules: ScheduleConfig[]
  runs: RunSummary[]
  loading: boolean
  error: string
}>>({})
~~~

切换账户不能覆盖其他账户数据；全局总览单独存储；运行详情按账户和 run ID 缓存；刷新冷却按账户保存。

## 16. 阶段 12：前端页面

### 16.1 账户管理

新增：

~~~text
frontend/src/views/TradingAccountsView.vue
frontend/src/components/accounts/TradingAccountList.vue
frontend/src/components/accounts/TradingAccountForm.vue
frontend/src/components/accounts/TradingAccountLlmForm.vue
frontend/src/components/accounts/TradingAccountSkills.vue
~~~

功能：

- 新建、编辑、归档、恢复。
- 妙想 Key。
- 独立 LLM。
- 提示词、市场范围、决策风格、最大动作数。
- Skills。
- 资金封印和 Telegram 通知。
- 测试妙想 API 和 LLM。
- 显示实际 LLM 来源：账户、全局兜底或未配置。

密钥字段必须沿用当前脱敏逻辑：未修改的 **** 值不覆盖原值，空字符串明确清除。

### 16.2 SettingsView

只保留：

- 应用显示名称。
- 全局 LLM。
- 全局 UZI 妙想 Key。
- 全局技能目录管理。
- 系统级设置。

移除交易账户级妙想 Key、提示词、市场、风控、资金封印和账户通知。

### 16.3 ScheduleView

增加账户选择器。选中账户后只加载和保存该账户任务。保存不能调用全局任务 PUT。

### 16.4 TasksView

增加账户筛选、账户名称、账户 LLM 来源和账户运行状态。运行详情、删除和 SSE 使用账户级 API。

### 16.5 OverviewView

全局比较层：

- 总资产、现金、持仓市值。
- 加权累计收益和当日收益。
- 账户状态、最近运行时间和失败信息。

账户详情层：

- 当前账户资金。
- 当前账户持仓。
- 当前账户订单。
- 当前账户闭环交易。
- 当前账户调度和运行历史。

账户切换后重新加载或读取该账户独立缓存，不能只过滤一份混合数组。

### 16.6 ChatView

增加账户选择器。切换账户时切换会话列表、自动化会话和消息缓存；发送请求显式带账户 ID。

## 17. 测试方案

### 17.1 数据迁移

- 空库初始化。
- 旧单账户库初始化。
- 重复启动。
- 旧 aniu.db。
- 默认账户不重复。
- 任务、运行和会话回填。
- UZI Key 保留。
- 默认账户配置等于旧配置。

### 17.2 LLM 兜底

- 账户配置完整时使用账户 LLM。
- 账户未启用时使用全局 LLM。
- 账户不完整时整体回退全局。
- 两套都不可用时只失败当前账户。
- 运行保存来源和模型。
- Key 不出现在运行 JSON 和日志。

### 17.3 妙想 Key 隔离

使用 FakeMXClient(api_key="key-a") 和 FakeMXClient(api_key="key-b")：

- A 的余额、持仓、订单和下单只使用 key-a。
- B 只使用 key-b。
- A 下单不调用 B。
- 缓存、最近运行快照和总览不串号。

### 17.4 Skills 隔离

A 启用 UZI、禁用 Skill X；B 禁用 UZI、启用 Skill X：

- 工具列表不同。
- prompt supplement 不同。
- 一个账户的禁用不会影响另一个。
- 全局 Catalog 不会被运行时修改。
- 并发运行路由不串号。

### 17.5 调度并发

- A、B 同时到期都能运行。
- A 运行不阻塞 B。
- A 同时两个交易任务只运行一个。
- A 失败和重试不修改 B。
- 停用账户不触发任务。
- lease 成功、重复抢占、过期恢复正确。
- 手动和定时运行遵守同一账户锁。

### 17.6 API 和前端

覆盖账户 CRUD、归档恢复、账户任务、账户运行、账户总览、全局总览、跨账户访问拒绝、旧接口 409、密钥脱敏、账户切换、前端缓存和聊天会话隔离。

### 17.7 UZI 回归

至少运行：

~~~bash
cd backend
./.venv/bin/pytest tests/test_uzi_report_context_skill.py
./.venv/bin/pytest tests/test_uzi_llm_config_from_settings.py
./.venv/bin/pytest tests/test_uzi_flow.py
~~~

## 18. 验收标准

全部满足才算完成：

1. 可新增两个不同妙想 Key 账户。
2. 两账户的资金和持仓查询不串号。
3. 两账户拥有不同提示词、市场范围和 Skills。
4. 两账户拥有不同自动化会话。
5. 两账户拥有各自多个定时任务。
6. 两账户定时任务可以并发。
7. 一个账户失败不阻塞另一个。
8. 同一账户交易任务不会并发操作一个模拟仓位。
9. 运行、订单、聊天和调度历史按账户隔离。
10. 缓存按账户隔离。
11. 账户级 LLM 生效，全局 LLM 兜底生效。
12. 所有账户可以查询全局 UZI 报告。
13. UZI 生成不受账户 LLM 和妙想 Key 影响。
14. 旧数据库可以升级，旧任务和历史记录完整。
15. 全局金额求和正确，收益率加权正确。
16. 单账户总览失败不影响其他账户。
17. Key 不出现在 API 明文、日志、错误和 JSON 运行记录。
18. 后端 pytest 通过。
19. 前端 npm run build 通过。
20. Docker 构建通过。

## 19. 禁止实现方式

不得：

1. 只加前端账户下拉框，后端继续读全局 Key。
2. 只给 StrategyRun 加账户 ID，不改 Client、缓存、Skills 和调度。
3. 每次运行前修改全局 AppSettings.mx_api_key。
4. 使用全局变量保存当前账户。
5. 使用单份总览缓存。
6. 所有账户共用自动化会话。
7. 修改全局 SkillRegistry 来实现账户 Skills。
8. 只显示账户名称而不做数据库归属和服务层校验。
9. 半混合账户和全局 LLM 配置。
10. 复制 UZI 报告到每个账户。
11. 为了并发放开 analysis 的交易工具。
12. 物理删除账户及历史。
13. 用前端过滤代替后端过滤。
14. 删除旧数据库或要求用户手工迁移。
15. 没有跨账户 Mock 测试就声明隔离完成。

## 20. 默认决策

- 账户模型：TradingAccount。
- 表名：trading_accounts。
- 一个妙想 Key 只能绑定一个账户。
- 账户采用归档，不物理删除。
- 每账户一个自动化会话。
- 每账户多个定时任务。
- 同账户默认串行，不同账户可并发。
- 保留全局 scheduler 轮询线程。
- lease 使用数据库字段。
- 全局 UZI Key 为 AppSettings.uzi_mx_api_key。
- 旧妙想 Key 同时迁移到默认账户和 UZI 全局配置。
- 全局技能目录管理和账户技能启用状态分离。
- 全局硬禁用优先于账户启用。
- 新账户级 API 为主接口。
- 旧接口仅在单账户兼容模式下自动映射。
- 第一阶段不引入多用户权限和外部任务队列。

## 21. 最终验证命令

~~~bash
cd backend
./.venv/bin/pytest

cd ../frontend
npm run build

cd ..
docker build -t aniu:multi-account-review .
~~~

另外使用一份复制的旧 SQLite 数据库演练：

1. 启动并完成默认账户迁移。
2. 验证原任务、运行、会话和 UZI 报告仍在。
3. 新增第二账户。
4. 为两个账户创建不同定时任务。
5. 使用两个 FakeMXClient 并发运行。
6. 验证 Key、缓存、运行记录、Skills 和总览均不串号。

