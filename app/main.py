from fastapi import FastAPI

from app.routers import auth, health, interactions, places, recommendations, users

app = FastAPI(title="CARA Backend")

app.include_router(health.router)
app.include_router(places.router)
app.include_router(auth.router)
app.include_router(recommendations.router)
app.include_router(interactions.router)
app.include_router(users.router)
