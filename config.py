"""系统配置"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# 数据库配置
DATABASE_PATH = os.getenv("BLACKBOARD_DB_PATH", str(DATA_DIR / "blackboard.db"))

# 智能体配置
HEARTBEAT_TIMEOUT_SECONDS = int(os.getenv("HEARTBEAT_TIMEOUT", "60"))
HEARTBEAT_CHECK_INTERVAL = int(os.getenv("HEARTBEAT_CHECK_INTERVAL", "30"))

# WebSocket 入站控制：防止超大订阅消息和无界频道列表占用内存。
WEBSOCKET_MAX_MESSAGE_BYTES = int(
    os.getenv("WEBSOCKET_MAX_MESSAGE_BYTES", str(64 * 1024))
)
WEBSOCKET_MAX_CHANNELS = int(os.getenv("WEBSOCKET_MAX_CHANNELS", "64"))

# 服务配置
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
