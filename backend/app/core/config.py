from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "A股洞察终端"
    database_url: str = "sqlite:///./data/stock_analysis.db"
    tushare_token: SecretStr = SecretStr("")
    tushare_calls_per_minute: int = 120
    tushare_daily_budget: int = 5000
    llm_api_key: SecretStr = SecretStr("")
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    use_mock_data: bool = True
    quote_refresh_seconds: int = 30
    bootstrap_history_days: int = 90
    news_refresh_minutes: int = 15
    # 真实数据模式下自选股新闻源：akshare（默认，按代码拉东财个股新闻）或 tushare。
    # use_mock_data=True 时忽略，维持演示/种子数据不变。
    news_source: str = "akshare"
    # akshare 真实模式下，除自选股外再并入综合评分(score_snapshots.total_score)前 N 名
    # 一起增量拉新闻，避免 top-N 的 llm 新闻老化出 3 天评分窗口。0=仅自选；不改变
    # news_source 选择，也不影响 mock/stream 演示路径（那里仍按自选股流匹配）。
    news_top_n: int = 0
    enable_scheduler: bool = True
    cors_origins: str = "http://127.0.0.1:5173,http://localhost:5173"
    allowed_hosts: str = "127.0.0.1,localhost"

    # —— 微信推送（企业微信群机器人）——
    # 这个 URL 是**凭据**：拿到它的人可以往那个群发任意消息。只存服务器 .env，
    # 永不进版本库、永不回传给前端（`SystemStatus` 只暴露"配没配"这一个布尔）。
    wecom_webhook_url: SecretStr = SecretStr("")
    # 显式 kill switch。真正的门是 `notify_enabled and webhook 非空` 两条同时成立。
    notify_enabled: bool = True
    # 企业微信 markdown 上限 4096 **字节**，留余量。
    notify_max_bytes: int = 4000
    # 假定本金。**不能硬编码**：页面的 equity 来自 URL 参数，它喂 `weight_pct`，而
    # `weight_pct` 门控 `decision` 的 `add`（加仓）分支——两边取不同本金，同一只票
    # 同一根收盘，网页会说「加仓」而推送说「继续持有」。所以走配置，并把它印在消息里。
    notify_equity: float = 1_000_000.0
    notify_daily_hour: int = 19

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def allowed_origins(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def allowed_host_list(self) -> list[str]:
        return [item.strip() for item in self.allowed_hosts.split(",") if item.strip()]

    @property
    def notify_configured(self) -> bool:
        """只回"配没配"，**永不回传 URL 本身**。照抄 `tushare_configured` 的做法。"""
        return bool(self.notify_enabled and self.wecom_webhook_url.get_secret_value())

    def ensure_data_dir(self) -> None:
        if self.database_url.startswith("sqlite:///./"):
            Path(self.database_url.removeprefix("sqlite:///./")).parent.mkdir(
                parents=True, exist_ok=True
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
