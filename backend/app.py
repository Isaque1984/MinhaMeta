import os
import httpx

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Minha Meta PRO API")


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


MP_ACCESS_TOKEN = os.getenv("MP_ACCESS_TOKEN", "").strip()


@app.get("/")
def root():
    return {
        "app": "Minha Meta PRO API",
        "status": "online"
    }


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/verificar-pro")
async def verificar_pro(email: str):

    if not MP_ACCESS_TOKEN:
        raise HTTPException(
            status_code=500,
            detail="MP_ACCESS_TOKEN não configurado"
        )

    email = email.strip().lower()

    if not email:
        raise HTTPException(
            status_code=400,
            detail="E-mail obrigatório"
        )

    url = "https://api.mercadopago.com/preapproval/search"

    headers = {
        "Authorization": f"Bearer {MP_ACCESS_TOKEN}"
    }

    params = {
        "payer_email": email
    }

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(
            url,
            headers=headers,
            params=params
        )

    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=response.text
        )

    data = response.json()

    subscriptions = data.get("results", [])

    active_statuses = {
        "authorized",
        "active"
    }

    for subscription in subscriptions:

        status = subscription.get("status")

        if status in active_statuses:
            return {
                "pro": True,
                "status": status,
                "subscription_id": subscription.get("id"),
                "email": email,
                "next_payment_date": subscription.get(
                    "next_payment_date"
                )
            }

    return {
        "pro": False,
        "status": "inactive",
        "email": email
    }


@app.post("/webhook/mercadopago")
async def webhook_mercadopago(request=None):

    return {
        "received": True
            }
