from contextlib import asynccontextmanager

from anyio import to_thread
from fastapi import FastAPI

from app.api.routes import router
from app.services.browser_manager import shutdown_browser, startup_browser


@asynccontextmanager
async def lifespan(_: FastAPI):
    await to_thread.run_sync(startup_browser)
    try:
        yield
    finally:
        await to_thread.run_sync(shutdown_browser)


def create_app() -> FastAPI:
    application = FastAPI(
        title="Operation Point Break",
        description="Compare American Airlines cash vs award fares and calculate cents per point.",
        lifespan=lifespan,
    )
    application.include_router(router)

    return application


app = create_app()
