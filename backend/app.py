import os
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from supabase import create_client


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


# =========================================================
# SUPABASE
# =========================================================

def get_supabase():

    if not SUPABASE_URL or not SUPABASE_KEY:

        raise HTTPException(
            status_code=500,
            detail="Supabase não configurado no Render"
        )

    return create_client(
        SUPABASE_URL,
        SUPABASE_KEY
    )


# =========================================================
# SENHAS
# =========================================================

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


# =========================================================
# SESSÃO
# =========================================================

def create_session():

    return secrets.token_urlsafe(48)


def normalize_email(email):

    return str(email or "").strip().lower()


# =========================================================
# MODELOS
# =========================================================

class CreateAccount(BaseModel):

    email: str
    password: str


class LoginData(BaseModel):

    email: str
    password: str


class SessionData(BaseModel):

    token: str


# =========================================================
# ROTAS BÁSICAS
# =========================================================

@app.get("/")
def root():

    return {
        "app": "Minha Meta PRO API",
        "status": "online"
    }


@app.get("/health")
def health():

    return {
        "ok": True
    }


# =========================================================
# CONSULTAR MERCADO PAGO PELO E-MAIL
# =========================================================

async def buscar_assinatura_por_email(email):

    if not MP_ACCESS_TOKEN:

        raise Exception(
            "MP_ACCESS_TOKEN não configurado"
        )

    email = normalize_email(email)

    url = (
        "https://api.mercadopago.com/"
        "preapproval/search"
    )

    headers = {
        "Authorization":
            f"Bearer {MP_ACCESS_TOKEN}",

        "Content-Type":
            "application/json"
    }

    params = {
        "payer_email": email
    }

    async with httpx.AsyncClient(
        timeout=20
    ) as client:

        response = await client.get(
            url,
            headers=headers,
            params=params
        )

    if response.status_code >= 400:

        raise Exception(
            f"Mercado Pago HTTP "
            f"{response.status_code}: "
            f"{response.text}"
        )

    data = response.json()

    return data.get(
        "results",
        []
    )


# =========================================================
# CONSULTAR ASSINATURA PELO ID
# =========================================================

async def buscar_assinatura_por_id(
    subscription_id
):

    if not MP_ACCESS_TOKEN:

        raise Exception(
            "MP_ACCESS_TOKEN não configurado"
        )

    url = (
        "https://api.mercadopago.com/"
        f"preapproval/{subscription_id}"
    )

    headers = {
        "Authorization":
            f"Bearer {MP_ACCESS_TOKEN}",

        "Content-Type":
            "application/json"
    }

    async with httpx.AsyncClient(
        timeout=20
    ) as client:

        response = await client.get(
            url,
            headers=headers
        )

    if response.status_code >= 400:

        raise Exception(
            f"Mercado Pago HTTP "
            f"{response.status_code}: "
            f"{response.text}"
        )

    return response.json()


# =========================================================
# VERIFICAR STATUS ATUAL DA ASSINATURA
# =========================================================

async def verificar_assinatura_atual(email, subscription_id=None):

    email = normalize_email(email)

    assinatura = None

    # -----------------------------------------------------
    # Se já temos ID, consulta diretamente
    # -----------------------------------------------------

    if subscription_id:

        try:

            assinatura = await buscar_assinatura_por_id(
                subscription_id
            )

        except Exception:

            assinatura = None

    # -----------------------------------------------------
    # Se não temos ID, procura pelo e-mail
    # -----------------------------------------------------

    if not assinatura:

        assinaturas = await buscar_assinatura_por_email(
            email
        )

        # Primeiro tenta encontrar uma assinatura ativa
        for item in assinaturas:

            status = item.get("status")

            if status == "authorized":

                assinatura = item
                break

        # Se não encontrou ativa,
        # pega a mais recente disponível
        if not assinatura and assinaturas:

            assinatura = assinaturas[0]

    # -----------------------------------------------------
    # Nenhuma assinatura encontrada
    # -----------------------------------------------------

    if not assinatura:

        return {
            "pro": False,
            "status": "inactive",
            "subscription_id": None
        }

    status = assinatura.get(
        "status",
        "inactive"
    )

    subscription_id = assinatura.get(
        "id"
    )

    active_statuses = {
        "authorized",
        "active"
    }

    return {
        "pro": status in active_statuses,
        "status": status,
        "subscription_id": subscription_id,
        "next_payment_date":
            assinatura.get(
                "next_payment_date"
            )
    }


# =========================================================
# ATUALIZAÇÃO DA ASSINATURA EM SEGUNDO PLANO
# =========================================================

async def atualizar_assinatura_usuario(
    user_id,
    email,
    subscription_id=None
):

    try:

        verificacao = await verificar_assinatura_atual(
            email,
            subscription_id
        )

        supabase = get_supabase()

        agora = datetime.now(
            timezone.utc
        ).isoformat()

        novo_status = verificacao.get(
            "status",
            "inactive"
        )

        novo_id = verificacao.get(
            "subscription_id"
        )

        ativo = bool(
            verificacao.get("pro")
        )

        dados = {
            "active": ativo,
            "mp_status": novo_status,
            "mp_checked_at": agora
        }

        if novo_id:

            dados[
                "mercado_pago_subscription_id"
            ] = novo_id

        supabase.table(
            "pro_users"
        ).update(
            dados
        ).eq(
            "id",
            user_id
        ).execute()

        print(
            "Verificação Mercado Pago concluída:",
            email,
            novo_status
        )

    except Exception as e:

        print(
            "Erro na verificação Mercado Pago:",
            str(e)
        )


# =========================================================
# VERIFICAR PRO PELO E-MAIL
# =========================================================

@app.get("/verificar-pro")
async def verificar_pro(email: str):

    email = normalize_email(email)

    if not email:

        raise HTTPException(
            status_code=400,
            detail="E-mail obrigatório"
        )

    try:

        verificacao = await verificar_assinatura_atual(
            email
        )

        return {
            "pro":
                verificacao.get("pro"),

            "status":
                verificacao.get("status"),

            "subscription_id":
                verificacao.get(
                    "subscription_id"
                ),

            "email":
                email,

            "next_payment_date":
                verificacao.get(
                    "next_payment_date"
                )
        }

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=(
                "Erro ao consultar Mercado Pago: "
                + str(e)
            )
        )


# =========================================================
# CRIAR CONTA PRO
# =========================================================

@app.post("/criar-conta-pro")
async def criar_conta_pro(
    data: CreateAccount
):

    email = normalize_email(
        data.email
    )

    password = data.password

    if not email:

        raise HTTPException(
            status_code=400,
            detail="E-mail obrigatório"
        )

    if len(password) < 6:

        raise HTTPException(
            status_code=400,
            detail=(
                "A senha deve ter pelo menos "
                "6 caracteres"
            )
        )

    # -----------------------------------------------------
    # Confirma assinatura
    # -----------------------------------------------------

    verificacao = await verificar_assinatura_atual(
        email
    )

    if not verificacao.get("pro"):

        raise HTTPException(
            status_code=403,
            detail=(
                "Não encontramos uma assinatura "
                "PRO ativa para este e-mail."
            )
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
            detail=(
                "Já existe uma conta PRO "
                "com este e-mail."
            )
        )

    password_hash = hash_password(
        password
    )

    token = create_session()

    expires = (
        datetime.now(timezone.utc)
        + timedelta(days=30)
    )

    resultado = (
        supabase
        .table("pro_users")
        .insert({
            "email": email,
            "password_hash": password_hash,
            "active": True,

            "session_token_hash":
                hashlib.sha256(
                    token.encode()
                ).hexdigest(),

            "session_expires_at":
                expires.isoformat(),

            "mercado_pago_subscription_id":
                verificacao.get(
                    "subscription_id"
                ),

            "mp_status":
                verificacao.get(
                    "status"
                ),

            "mp_checked_at":
                datetime.now(
                    timezone.utc
                ).isoformat()
        })
        .execute()
    )

    if not resultado.data:

        raise HTTPException(
            status_code=500,
            detail=(
                "Não foi possível criar "
                "sua conta."
            )
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
async def login_pro(
    data: LoginData,
    background_tasks: BackgroundTasks
):

    email = normalize_email(
        data.email
    )

    if not email or not data.password:

        raise HTTPException(
            status_code=400,
            detail=(
                "E-mail e senha são obrigatórios."
            )
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
            detail=(
                "E-mail ou senha incorretos."
            )
        )

    usuario = resultado.data[0]

    if not check_password(
        data.password,
        usuario.get(
            "password_hash",
            ""
        )
    ):

        raise HTTPException(
            status_code=401,
            detail=(
                "E-mail ou senha incorretos."
            )
        )

    # -----------------------------------------------------
    # Se a conta está inativa, fazemos uma verificação
    # imediata para permitir uma eventual renovação.
    # -----------------------------------------------------

    if not usuario.get("active"):

        verificacao = (
            await verificar_assinatura_atual(
                email,
                usuario.get(
                    "mercado_pago_subscription_id"
                )
            )
        )

        if not verificacao.get("pro"):

            raise HTTPException(
                status_code=403,
                detail=(
                    "Sua assinatura PRO "
                    "não está ativa."
                )
            )

        supabase.table(
            "pro_users"
        ).update({
            "active": True,

            "mercado_pago_subscription_id":
                verificacao.get(
                    "subscription_id"
                ),

            "mp_status":
                verificacao.get(
                    "status"
                ),

            "mp_checked_at":
                datetime.now(
                    timezone.utc
                ).isoformat()
        }).eq(
            "id",
            usuario["id"]
        ).execute()

    token = create_session()

    expires = (
        datetime.now(timezone.utc)
        + timedelta(days=30)
    )

    supabase.table(
        "pro_users"
    ).update({

        "session_token_hash":
            hashlib.sha256(
                token.encode()
            ).hexdigest(),

        "session_expires_at":
            expires.isoformat()

    }).eq(
        "id",
        usuario["id"]
    ).execute()

    # -----------------------------------------------------
    # Se está ativo, a entrada continua rápida.
    # A próxima verificação é feita em segundo plano.
    # -----------------------------------------------------

    usuario_mp_checked = usuario.get(
        "mp_checked_at"
    )

    precisa_verificar = True

    if usuario_mp_checked:

        try:

            ultima_verificacao = datetime.fromisoformat(
                usuario_mp_checked.replace(
                    "Z",
                    "+00:00"
                )
            )

            limite = (
                datetime.now(timezone.utc)
                - timedelta(days=1)
            )

            if ultima_verificacao > limite:

                precisa_verificar = False

        except Exception:

            precisa_verificar = True

    if precisa_verificar:

        background_tasks.add_task(
            atualizar_assinatura_usuario,
            usuario["id"],
            email,
            usuario.get(
                "mercado_pago_subscription_id"
            )
        )

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
async def verificar_sessao(
    data: SessionData,
    background_tasks: BackgroundTasks
):

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
        .select(
            "id, email, active, "
            "session_expires_at, "
            "mercado_pago_subscription_id, "
            "mp_checked_at"
        )
        .eq(
            "session_token_hash",
            token_hash
        )
        .limit(1)
        .execute()
    )

    if not resultado.data:

        raise HTTPException(
            status_code=401,
            detail=(
                "Sessão inválida "
                "ou expirada."
            )
        )

    usuario = resultado.data[0]

    if not usuario.get("active"):

        raise HTTPException(
            status_code=403,
            detail="Conta PRO inativa."
        )

    expires = usuario.get(
        "session_expires_at"
    )

    if expires:

        try:

            data_expiracao = datetime.fromisoformat(
                expires.replace(
                    "Z",
                    "+00:00"
                )
            )

            if (
                data_expiracao
                < datetime.now(timezone.utc)
            ):

                raise HTTPException(
                    status_code=401,
                    detail="Sessão expirada."
                )

        except ValueError:

            pass

    # -----------------------------------------------------
    # Verificação em segundo plano
    # -----------------------------------------------------

    precisa_verificar = True

    ultima_verificacao = usuario.get(
        "mp_checked_at"
    )

    if ultima_verificacao:

        try:

            ultima = datetime.fromisoformat(
                ultima_verificacao.replace(
                    "Z",
                    "+00:00"
                )
            )

            limite = (
                datetime.now(timezone.utc)
                - timedelta(days=1)
            )

            if ultima > limite:

                precisa_verificar = False

        except Exception:

            precisa_verificar = True

    if precisa_verificar:

        background_tasks.add_task(
            atualizar_assinatura_usuario,
            usuario["id"],
            usuario["email"],
            usuario.get(
                "mercado_pago_subscription_id"
            )
        )

    # -----------------------------------------------------
    # Responde imediatamente
    # -----------------------------------------------------

    return {
        "ok": True,
        "pro": True,
        "email": usuario["email"]
    }


# =========================================================
# WEBHOOK MERCADO PAGO
# =========================================================

@app.post("/webhook/mercadopago")
async def webhook_mercadopago(
    request: Request
):

    try:

        data = await request.json()

    except Exception:

        data = {}

    print(
        "Webhook Mercado Pago recebido:"
    )

    print(data)

    return {
        "received": True
        }
