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

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def allowed_origins(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def allowed_host_list(self) -> list[str]:
        return [item.strip() for item in self.allowed_hosts.split(",") if item.strip()]

    def ensure_data_dir(self) -> None:
        if self.database_url.startswith("sqlite:///./"):
            Path(self.database_url.removeprefix("sqlite:///./")).parent.mkdir(
                parents=True, exist_ok=True
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
