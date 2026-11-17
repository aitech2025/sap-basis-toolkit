from pathlib import Path
import os

APP_NAME = "SAP Basis Sentinel"
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", "./data/sentinel.db"))
CERTIFICATE_TIMEOUT_SECONDS = float(os.getenv("CERTIFICATE_TIMEOUT_SECONDS", "8"))
DEFAULT_WARNING_DAYS = int(os.getenv("DEFAULT_WARNING_DAYS", "30"))
