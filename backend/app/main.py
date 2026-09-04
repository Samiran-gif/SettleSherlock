"""SettleSherlock backend application entrypoint."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.router import api_router
from app.core.config import settings
from app.schemas.common import RootResponse
from app.services.data_loader import data_loader


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown hooks. Later stages wire up DB and model clients here."""
    # Read the settlement CSVs up front so data problems show up at boot,
    # not on the first request.
    data_loader.load()
    yield


def create_app() -> FastAPI:
    """Application factory."""
    app = FastAPI(
        title=settings.PROJECT_NAME,
        description=settings.PROJECT_DESCRIPTION,
        version=settings.VERSION,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router, prefix=settings.API_V1_PREFIX)

    @app.get("/", response_model=RootResponse, tags=["meta"])
    async def root() -> RootResponse:
        """Basic service metadata, handy for confirming the API is reachable."""
        return RootResponse(
            service=settings.PROJECT_NAME,
            version=settings.VERSION,
            description=settings.PROJECT_DESCRIPTION,
            docs_url="/docs",
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        """Keep unexpected failures in the same JSON shape as HTTP errors."""
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "status_code": 500},
        )

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
    )
