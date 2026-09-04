"""Aggregates all versioned API routers."""

from fastapi import APIRouter

from app.api.routes import health, transactions

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(transactions.router)
