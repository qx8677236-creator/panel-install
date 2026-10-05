"""运行配置。开发环境默认只监听本机。"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


# backend/ 目录
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"


class Settings(BaseSettings):
    """面板配置。敏感材料（JWT 密钥、密码哈希）放在 data/，不放进代码。"""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "删库跑路快捷助手"
    host: str = "127.0.0.1"
    port: int = 8000
    # 前端开发服务器来源。生产环境应改成实际域名。
    cors_origins: list[str] = [
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ]
    access_token_minutes: int = 30
    refresh_token_days: int = 7
    # 同一 IP + 用户名连续失败达到上限后锁定
    max_login_failures: int = 5
    lockout_seconds: int = 300
    # 文件管理器的根目录。留空时使用 data/wwwroot。
    # Linux 上建议设为 /www/wwwroot。不能设成 /、/etc、/root 这类系统目录。
    file_root: str = ""
    # 默认只写 data/nginx，并用这份独立配置做 nginx -t / reload。
    # 设为 true 才会改系统的 sites-available，并给正在运行的 Nginx 发 reload。
    nginx_system_mode: bool = False
    nginx_sites_available: str = ""
    nginx_sites_enabled: str = ""
    nginx_bin: str = ""
    certbot_bin: str = ""


settings = Settings()
