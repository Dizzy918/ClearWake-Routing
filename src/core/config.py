from dotenv import load_dotenv
load_dotenv()

from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    MONGODB_URI: str = "mongodb://localhost:27017"
    DB_NAME: str = "clearwake"
    APP_PORT: int = 8080
    APP_ENV: str = "development"
    ROUTING_ENGINE_URL: Optional[str] = None
    ROUTING_API_KEY: Optional[str] = None
    RATE_LIMIT_REQUESTS_PER_MINUTE: int = 60
    LOG_LEVEL: str = "info"
    MAP_PROVIDER: str = "carto-voyager"

    AI_ANOMALY_SPEED_THRESHOLD: float = 2.0
    AI_ANOMALY_COURSE_THRESHOLD: float = 15.0
    AI_REROUTE_FUEL_THRESHOLD: float = 0.05
    AI_REROUTE_TIME_THRESHOLD: float = 0.10
    AI_RECOMMENDATION_EXPIRY_HOURS: int = 24
    AI_WEATHER_FACTOR_SUMMER: float = 1.0
    AI_WEATHER_FACTOR_WINTER: float = 1.15

    # JWT auth — change JWT_SECRET in production.
    # (>= 32 bytes so HS256 doesn't warn about insecure key length)
    JWT_SECRET: str = "dev-only-secret-change-me-before-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRES_MINUTES: int = 60

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
