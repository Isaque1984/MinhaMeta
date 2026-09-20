import os
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from supabase import create_client, Client


app = FastAPI(title="Minha Meta PRO API")


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


MP_ACCESS_TOKEN = os.getenv("MP_ACCESS_TOKEN", "").strip()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()


def get_supabase():
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise HTTPException(
            status_code=500,
            detail="Supabase não configurado no Render"
        )

    return create_client(SUPABASE_URL, SUPABASE_KEY)


def hash_password(password):
    salt = secrets.token_hex(16)

    password_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        200000
    ).hex()

    return salt + ":" + password_hash


def check_password(password, stored_hash):
    try:
        salt, original_hash = stored_hash.split(":", 1)

        password_hash = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            200000
        ).hex()

        return secrets.compare_digest(
            password_hash,
            original_hash
        )

    except Exception:
        return False


def create_session():
    return secrets.token_urlsafe(48)


def normalize_email(email):
    return str(email or "").strip().lower()


class CreateAccount(BaseModel):
    email: str
    password: str


class LoginData(BaseModel):
    email: str
    password: str


class SessionData(BaseModel):
    token: str


@app.get("/")
def root():
    return {
        "app": "Minha Meta PRO API",
        "status": "online"
    }


@app.get("/health")
def health():
    return {"ok": True}


# =========================================================
# MERCADO PAGO
# =========================================================

@app.get("/verificar-pro")
async def verificar_pro(email: str):

    if not MP_ACCESS_TOKEN:
        raise HTTPException(
            status_code=500,
            detail="MP_ACCESS_TOKEN não configurado"
        )

    email = normalize_email(email)

    if not email:
        raise HTTPException(
            status_code=400,
            detail="E-mail obrigatório"
        )

    url = "https://api.mercadopago.com/preapproval/search"

    headers = {
        "Authorization": f"Bearer {MP_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    params = {
        "payer_email": email
    }

    try:
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

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao consultar Mercado Pago: {str(e)}"
        )


# =========================================================
# CRIAR CONTA PRO
# =========================================================

@app.post("/criar-conta-pro")
async def criar_conta_pro(data: CreateAccount):

    email = normalize_email(data.email)
    password = data.password

    if not email:
        raise HTTPException(
            status_code=400,
            detail="E-mail obrigatório"
        )

    if len(password) < 6:
        raise HTTPException(
            status_code=400,
            detail="A senha deve ter pelo menos 6 caracteres"
        )

    # Primeiro confirma o pagamento
    verificacao = await verificar_pro(email)

    if not verificacao.get("pro"):
        raise HTTPException(
            status_code=403,
            detail="Não encontramos uma assinatura PRO ativa para este e-mail."
        )

    supabase = get_supabase()

    existente = (
        supabase
        .table("pro_users")
        .select("id")
        .eq("email", email)
        .execute()
    )

    if existente.data:
        raise HTTPException(
            status_code=409,
            detail="Já existe uma conta PRO com este e-mail."
        )

    password_hash = hash_password(password)
    token = create_session()

    expires = datetime.now(timezone.utc) + timedelta(days=30)

    resultado = (
        supabase
        .table("pro_users")
        .insert({
            "email": email,
            "password_hash": password_hash,
            "active": True,
            "session_token_hash": hashlib.sha256(
                token.encode()
            ).hexdigest(),
            "session_expires_at": expires.isoformat()
        })
        .execute()
    )

    if not resultado.data:
        raise HTTPException(
            status_code=500,
            detail="Não foi possível criar sua conta."
        )

    return {
        "ok": True,
        "pro": True,
        "email": email,
        "token": token
    }


# =========================================================
# LOGIN PRO
# =========================================================

@app.post("/login-pro")
async def login_pro(data: LoginData):

    email = normalize_email(data.email)

    if not email or not data.password:
        raise HTTPException(
            status_code=400,
            detail="E-mail e senha são obrigatórios."
        )

    supabase = get_supabase()

    resultado = (
        supabase
        .table("pro_users")
        .select("*")
        .eq("email", email)
        .limit(1)
        .execute()
    )

    if not resultado.data:
        raise HTTPException(
            status_code=401,
            detail="E-mail ou senha incorretos."
        )

    usuario = resultado.data[0]

    if not usuario.get("active"):
        raise HTTPException(
            status_code=403,
            detail="Esta conta PRO está inativa."
        )

    if not check_password(
        data.password,
        usuario.get("password_hash", "")
    ):
        raise HTTPException(
            status_code=401,
            detail="E-mail ou senha incorretos."
        )

    token = create_session()

    expires = datetime.now(timezone.utc) + timedelta(days=30)

    supabase.table("pro_users").update({
        "session_token_hash": hashlib.sha256(
            token.encode()
        ).hexdigest(),
        "session_expires_at": expires.isoformat()
    }).eq("id", usuario["id"]).execute()

    return {
        "ok": True,
        "pro": True,
        "email": email,
        "token": token
    }


# =========================================================
# VERIFICAR SESSÃO
# =========================================================

@app.post("/verificar-sessao")
async def verificar_sessao(data: SessionData):

    if not data.token:
        raise HTTPException(
            status_code=401,
            detail="Sessão inválida."
        )

    token_hash = hashlib.sha256(
        data.token.encode()
    ).hexdigest()

    supabase = get_supabase()

    resultado = (
        supabase
        .table("pro_users")
        .select("email, active, session_expires_at")
        .eq("session_token_hash", token_hash)
        .limit(1)
        .execute()
    )

    if not resultado.data:
        raise HTTPException(
            status_code=401,
            detail="Sessão inválida ou expirada."
        )

    usuario = resultado.data[0]

    if not usuario.get("active"):
        raise HTTPException(
            status_code=403,
            detail="Conta PRO inativa."
        )

    expires = usuario.get("session_expires_at")

    if expires:
        try:
            data_expiracao = datetime.fromisoformat(
                expires.replace("Z", "+00:00")
            )

            if data_expiracao < datetime.now(timezone.utc):
                raise HTTPException(
                    status_code=401,
                    detail="Sessão expirada."
                )

        except ValueError:
            pass

    return {
        "ok": True,
        "pro": True,
        "email": usuario["email"]
    }


# =========================================================
# WEBHOOK MERCADO PAGO
# =========================================================

@app.post("/webhook/mercadopago")
async def webhook_mercadopago(request: Request):

    try:
        data = await request.json()
    except Exception:
        data = {}

    print("Webhook Mercado Pago recebido:")
    print(data)

    return {
        "received": True
    }
