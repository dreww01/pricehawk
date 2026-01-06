import uvicorn

from app.core.config import get_settings

if __name__ == "__main__":
    settings = get_settings()

    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=5000,
        reload=settings.debug,
        access_log=True,
        log_level="info",
    )
