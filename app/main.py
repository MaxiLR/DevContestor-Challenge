from fastapi import FastAPI

from app.api.routes import router


def create_app() -> FastAPI:
    application = FastAPI(
        title="Operation Point Break",
        description="Compare American Airlines cash vs award fares and calculate cents per point.",
    )
    application.include_router(router)
    return application


app = create_app()
