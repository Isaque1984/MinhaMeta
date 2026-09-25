import os
import asyncio
import secrets
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from passlib.context import CryptContext
from supabase import create_client, Client


# =========================================================
# CONFIGURAÇÃO
# =========================================================

APP_NAME = "Minha Meta PRO API"

MP_ACCESS_TOKEN = os.getenv(
    "MP_ACCESS_TOKEN",
    ""
).strip()

SUPABASE_URL = os.getenv(
    "SUPABASE_URL",
    ""
).strip()

SUPABASE_KEY = os.getenv(
    "SUPABASE_KEY",
    ""
).strip()


# =========================================================
# APP
# =========================================================

app = FastAPI(
    title=APP_NAME
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================================================
# SUPABASE
# =========================================================

supabase: Client | None = None

if SUPABASE_URL and SUPABASE_KEY:

    try:

        supabase = create_client(
            SUPABASE_URL,
            SUPABASE_KEY
        )

        print("Supabase conectado.")

    except Exception as erro:

        print(
            "Erro ao conectar ao Supabase:",
            erro
        )


# =========================================================
# SENHAS
# =========================================================

pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto"
)


# =========================================================
# MODELOS
# =========================================================

class ContaPRO(BaseModel):

    email: str
    password: str


class LoginPRO(BaseModel):

    email: str
    password: str


class SessaoPRO(BaseModel):

    token: str


# =========================================================
# UTILIDADES
# =========================================================

def normalizar_email(
    email: str
) -> str:

    return str(
        email or ""
    ).strip().lower()


def agora_utc():

    return datetime.now(
        timezone.utc
    )


def gerar_token():

    return secrets.token_urlsafe(
        48
    )


def status_pro_ativo(
    status
) -> bool:

    return str(
        status or ""
    ).strip().lower() in {
        "authorized",
        "active"
    }


# =========================================================
# MERCADO PAGO
# =========================================================

async def mp_get(
    path: str,
    params=None
):

    if not MP_ACCESS_TOKEN:

        raise Exception(
            "MP_ACCESS_TOKEN não configurado."
        )

    url = (
        "https://api.mercadopago.com"
        + path
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

        resposta = await client.get(
            url,
            headers=headers,
            params=params
        )

    if resposta.status_code >= 400:

        raise Exception(
            f"Mercado Pago HTTP "
            f"{resposta.status_code}: "
            f"{resposta.text}"
        )

    return resposta.json()


# =========================================================
# BUSCAR ASSINATURAS NO MERCADO PAGO
# =========================================================

async def buscar_assinaturas_por_email(
    email: str
):

    email = normalizar_email(
        email
    )

    if not email:
        return []

    resultados_finais = []

    # -----------------------------------------------------
    # TENTATIVA 1
    # payer_email
    # -----------------------------------------------------

    try:

        dados = await mp_get(
            "/preapproval/search",
            params={
                "payer_email": email,
                "limit": 50
            }
        )

        resultados = (
            dados.get("results")
            or []
        )

        for item in resultados:

            payer_email = normalizar_email(
                item.get("payer_email")
            )

            if payer_email == email:

                resultados_finais.append(
                    item
                )

                continue

            payer = item.get(
                "payer"
            )

            if isinstance(
                payer,
                dict
            ):

                nested_email = normalizar_email(
                    payer.get("email")
                )

                if nested_email == email:

                    resultados_finais.append(
                        item
                    )

    except Exception as erro:

        print(
            "Busca payer_email falhou:",
            erro
        )

    # -----------------------------------------------------
    # TENTATIVA 2
    # q
    # -----------------------------------------------------

    try:

        dados = await mp_get(
            "/preapproval/search",
            params={
                "q": email,
                "limit": 50
            }
        )

        resultados = (
            dados.get("results")
            or []
        )

        for item in resultados:

            payer_email = normalizar_email(
                item.get("payer_email")
            )

            if payer_email == email:

                resultados_finais.append(
                    item
                )

                continue

            payer = item.get(
                "payer"
            )

            if isinstance(
                payer,
                dict
            ):

                nested_email = normalizar_email(
                    payer.get("email")
                )

                if nested_email == email:

                    resultados_finais.append(
                        item
                    )

    except Exception as erro:

        print(
            "Busca q falhou:",
            erro
        )

    # -----------------------------------------------------
    # REMOVER DUPLICADOS
    # -----------------------------------------------------

    unicos = {}

    for item in resultados_finais:

        identificador = (
            item.get("id")
            or item.get("preapproval_id")
        )

        if identificador:

            unicos[
                str(identificador)
            ] = item

    return list(
        unicos.values()
    )


# =========================================================
# BUSCAR ASSINATURA POR ID
# =========================================================

async def buscar_assinatura_por_id(
    subscription_id
):

    if not subscription_id:

        return None

    try:

        return await mp_get(
            f"/preapproval/{subscription_id}"
        )

    except Exception as erro:

        print(
            "Erro buscando assinatura por ID:",
            erro
        )

        return None


# =========================================================
# VERIFICAR ASSINATURA
# =========================================================

async def verificar_assinatura_atual(
    email: str,
    subscription_id=None
):

    email = normalizar_email(
        email
    )

    # -----------------------------------------------------
    # 1 — TENTAR PELO ID SALVO
    # -----------------------------------------------------

    if subscription_id:

        assinatura = (
            await buscar_assinatura_por_id(
                subscription_id
            )
        )

        if assinatura:

            payer_email = normalizar_email(
                assinatura.get(
                    "payer_email"
                )
            )

            if not payer_email:

                payer = assinatura.get(
                    "payer"
                )

                if isinstance(
                    payer,
                    dict
                ):

                    payer_email = normalizar_email(
                        payer.get(
                            "email"
                        )
                    )

            # Se o ID pertence ao mesmo e-mail,
            # podemos usar imediatamente.

            if (
                not payer_email
                or payer_email == email
            ):

                status = str(
                    assinatura.get(
                        "status"
                    )
                    or ""
                ).lower()

                subscription_id_found = (
                    assinatura.get("id")
                    or assinatura.get(
                        "preapproval_id"
                    )
                    or subscription_id
                )

                next_payment_date = (
                    assinatura.get(
                        "next_payment_date"
                    )
                    or assinatura.get(
                        "date_next_billing"
                    )
                )

                ativo = status_pro_ativo(
                    status
                )

                return {
                    "pro": ativo,
                    "status": status,
                    "subscription_id":
                        subscription_id_found,
                    "next_payment_date":
                        next_payment_date,
                    "subscription":
                        assinatura
                }

    # -----------------------------------------------------
    # 2 — BUSCAR PELO E-MAIL
    # -----------------------------------------------------

    assinaturas = (
        await buscar_assinaturas_por_email(
            email
        )
    )

    if not assinaturas:

        return {
            "pro": False,
            "status": "inactive",
            "subscription_id": None,
            "next_payment_date": None,
            "subscription": None
        }

    # -----------------------------------------------------
    # PRIMEIRO: PROCURAR UMA ATIVA
    # -----------------------------------------------------

    for assinatura in assinaturas:

        status = str(
            assinatura.get(
                "status"
            )
            or ""
        ).lower()

        if status_pro_ativo(
            status
        ):

            subscription_id_found = (
                assinatura.get("id")
                or assinatura.get(
                    "preapproval_id"
                )
            )

            next_payment_date = (
                assinatura.get(
                    "next_payment_date"
                )
                or assinatura.get(
                    "date_next_billing"
                )
            )

            return {
                "pro": True,
                "status": status,
                "subscription_id":
                    subscription_id_found,
                "next_payment_date":
                    next_payment_date,
                "subscription":
                    assinatura
            }

    # -----------------------------------------------------
    # EXISTE ASSINATURA, MAS ESTÁ INATIVA
    # -----------------------------------------------------

    primeira = assinaturas[0]

    status = str(
        primeira.get(
            "status"
        )
        or "inactive"
    ).lower()

    subscription_id_found = (
        primeira.get("id")
        or primeira.get(
            "preapproval_id"
        )
    )

    next_payment_date = (
        primeira.get(
            "next_payment_date"
        )
        or primeira.get(
            "date_next_billing"
        )
    )

    return {
        "pro": False,
        "status": status,
        "subscription_id":
            subscription_id_found,
        "next_payment_date":
            next_payment_date,
        "subscription":
            primeira
    }


# =========================================================
# ATUALIZAR USUÁRIO NO SUPABASE
# =========================================================

async def atualizar_assinatura_usuario(
    email: str
):

    if not supabase:
        return None

    email = normalizar_email(
        email
    )

    try:

        resultado = (
            supabase
            .table("pro_users")
            .select("*")
            .eq("email", email)
            .limit(1)
            .execute()
        )

        usuarios = (
            resultado.data
            or []
        )

        if not usuarios:
            return None

        usuario = usuarios[0]

        subscription_id = (
            usuario.get(
                "subscription_id"
            )
        )

        verificacao = (
            await verificar_assinatura_atual(
                email,
                subscription_id
            )
        )

        dados_update = {

            "active":
                verificacao["pro"],

            "status":
                verificacao["status"],

            "subscription_id":
                verificacao[
                    "subscription_id"
                ],

            "next_payment_date":
                verificacao[
                    "next_payment_date"
                ]
        }

        (
            supabase
            .table("pro_users")
            .update(dados_update)
            .eq("email", email)
            .execute()
        )

        return verificacao

    except Exception as erro:

        print(
            "Erro atualizando usuário:",
            erro
        )

        return None


# =========================================================
# CRIAR CONTA PRO
# =========================================================

@app.post("/criar-conta-pro")
async def criar_conta_pro(
    dados: ContaPRO
):

    email = normalizar_email(
        dados.email
    )

    password = str(
        dados.password or ""
    )

    if not email:

        raise HTTPException(
            status_code=400,
            detail="Digite seu e-mail."
        )

    if len(password) < 6:

        raise HTTPException(
            status_code=400,
            detail=(
                "A senha precisa ter "
                "pelo menos 6 caracteres."
            )
        )

    if not supabase:

        raise HTTPException(
            status_code=500,
            detail=(
                "Banco de dados não configurado."
            )
        )

    # -----------------------------------------------------
    # VERIFICAR MERCADO PAGO
    # -----------------------------------------------------

    try:

        verificacao = (
            await verificar_assinatura_atual(
                email
            )
        )

    except Exception as erro:

        print(
            "Erro Mercado Pago:",
            erro
        )

        raise HTTPException(
            status_code=502,
            detail=(
                "Não foi possível verificar "
                "sua assinatura agora."
            )
        )

    if not verificacao["pro"]:

        raise HTTPException(
            status_code=403,
            detail=(
                "Sua assinatura PRO não está ativa. "
                "Use o mesmo e-mail utilizado "
                "no Mercado Pago."
            )
        )

    # -----------------------------------------------------
    # VERIFICAR CONTA EXISTENTE
    # -----------------------------------------------------

    try:

        existente = (
            supabase
            .table("pro_users")
            .select("*")
            .eq("email", email)
            .limit(1)
            .execute()
        )

        if existente.data:

            raise HTTPException(
                status_code=400,
                detail=(
                    "Já existe uma conta PRO "
                    "com este e-mail. "
                    "Entre normalmente."
                )
            )

    except HTTPException:

        raise

    except Exception as erro:

        print(
            "Erro verificando conta:",
            erro
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "Erro ao consultar sua conta."
            )
        )

    # -----------------------------------------------------
    # CRIAR CONTA
    # -----------------------------------------------------

    try:

        password_hash = (
            pwd_context.hash(
                password
            )
        )

    except Exception as erro:

        print(
            "Erro gerando senha:",
            erro
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "Erro ao proteger sua senha."
            )
        )

    token = gerar_token()

    registro = {

        "email":
            email,

        "password_hash":
            password_hash,

        "active":
            True,

        "status":
            verificacao[
                "status"
            ],

        "subscription_id":
            verificacao[
                "subscription_id"
            ],

        "next_payment_date":
            verificacao[
                "next_payment_date"
            ],

        "token":
            token,

        "token_created_at":
            agora_utc().isoformat()
    }

    try:

        insercao = (
            supabase
            .table("pro_users")
            .insert(registro)
            .execute()
        )

        if not insercao.data:

            raise Exception(
                "Supabase não retornou "
                "o registro criado."
            )

    except Exception as erro:

        print(
            "Erro criando conta:",
            erro
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "Não foi possível criar "
                "sua conta PRO."
            )
        )

    return {

        "ok":
            True,

        "pro":
            True,

        "status":
            verificacao[
                "status"
            ],

        "email":
            email,

        "subscription_id":
            verificacao[
                "subscription_id"
            ],

        "next_payment_date":
            verificacao[
                "next_payment_date"
            ],

        "token":
            token
    }


# =========================================================
# LOGIN PRO
# =========================================================

@app.post("/login-pro")
async def login_pro(
    dados: LoginPRO
):

    email = normalizar_email(
        dados.email
    )

    password = str(
        dados.password or ""
    )

    if not email:

        raise HTTPException(
            status_code=400,
            detail="Digite seu e-mail."
        )

    if not password:

        raise HTTPException(
            status_code=400,
            detail="Digite sua senha."
        )

    if not supabase:

        raise HTTPException(
            status_code=500,
            detail=(
                "Banco de dados não configurado."
            )
        )

    # -----------------------------------------------------
    # BUSCAR CONTA
    # -----------------------------------------------------

    try:

        resultado = (
            supabase
            .table("pro_users")
            .select("*")
            .eq("email", email)
            .limit(1)
            .execute()
        )

        usuarios = (
            resultado.data
            or []
        )

    except Exception as erro:

        print(
            "Erro buscando usuário:",
            erro
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "Erro ao consultar sua conta."
            )
        )

    if not usuarios:

        raise HTTPException(
            status_code=401,
            detail=(
                "Conta PRO não encontrada. "
                "Use 'Criar minha conta PRO' "
                "no primeiro acesso."
            )
        )

    usuario = usuarios[0]

    # -----------------------------------------------------
    # CONFERIR SENHA
    # -----------------------------------------------------

    password_hash = (
        usuario.get(
            "password_hash"
        )
        or ""
    )

    try:

        senha_correta = (
            pwd_context.verify(
                password,
                password_hash
            )
        )

    except Exception as erro:

        print(
            "Erro verificando senha:",
            erro
        )

        senha_correta = False

    if not senha_correta:

        raise HTTPException(
            status_code=401,
            detail=(
                "E-mail ou senha incorretos."
            )
        )

    # -----------------------------------------------------
    # VERIFICAR MERCADO PAGO DIRETAMENTE
    # -----------------------------------------------------

    try:

        verificacao = (
            await verificar_assinatura_atual(
                email,
                usuario.get(
                    "subscription_id"
                )
            )
        )

        # -------------------------------------------------
        # SE NÃO ACHOU PELO ID,
        # FAZ UMA NOVA BUSCA EXCLUSIVA PELO E-MAIL
        # -------------------------------------------------

        if not verificacao["pro"]:

            verificacao = (
                await verificar_assinatura_atual(
                    email
                )
            )

    except Exception as erro:

        print(
            "Erro verificando Mercado Pago:",
            erro
        )

        raise HTTPException(
            status_code=502,
            detail=(
                "Não foi possível verificar "
                "sua assinatura no Mercado Pago."
            )
        )

    # -----------------------------------------------------
    # ASSINATURA NÃO ATIVA
    # -----------------------------------------------------

    if not verificacao["pro"]:

        print(
            "Login recusado:",
            email,
            "| status:",
            verificacao.get(
                "status"
            ),
            "| assinatura:",
            verificacao.get(
                "subscription_id"
            )
        )

        raise HTTPException(
            status_code=403,
            detail=(
                "Sua assinatura PRO não está ativa."
            )
        )

    # -----------------------------------------------------
    # GERAR NOVO TOKEN
    # -----------------------------------------------------

    token = gerar_token()

    # -----------------------------------------------------
    # ATUALIZAR CONTA
    # -----------------------------------------------------

    try:

        (
            supabase
            .table("pro_users")
            .update({

                "active":
                    True,

                "status":
                    verificacao[
                        "status"
                    ],

                "subscription_id":
                    verificacao[
                        "subscription_id"
                    ],

                "next_payment_date":
                    verificacao[
                        "next_payment_date"
                    ],

                "token":
                    token,

                "token_created_at":
                    agora_utc().isoformat()

            })
            .eq(
                "email",
                email
            )
            .execute()
        )

    except Exception as erro:

        print(
            "Erro atualizando login:",
            erro
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "Não foi possível iniciar "
                "sua sessão."
            )
        )

    return {

        "ok":
            True,

        "pro":
            True,

        "status":
            verificacao[
                "status"
            ],

        "email":
            email,

        "subscription_id":
            verificacao[
                "subscription_id"
            ],

        "next_payment_date":
            verificacao[
                "next_payment_date"
            ],

        "token":
            token
    }


# =========================================================
# VERIFICAR SESSÃO
# =========================================================

@app.post("/verificar-sessao")
async def verificar_sessao(
    dados: SessaoPRO
):

    token = str(
        dados.token or ""
    ).strip()

    if not token:

        raise HTTPException(
            status_code=401,
            detail="Sessão inválida."
        )

    if not supabase:

        raise HTTPException(
            status_code=500,
            detail=(
                "Banco de dados não configurado."
            )
        )

    try:

        resultado = (
            supabase
            .table("pro_users")
            .select("*")
            .eq("token", token)
            .limit(1)
            .execute()
        )

        usuarios = (
            resultado.data
            or []
        )

    except Exception as erro:

        print(
            "Erro verificando sessão:",
            erro
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "Erro ao verificar sessão."
            )
        )

    if not usuarios:

        raise HTTPException(
            status_code=401,
            detail=(
                "Sessão inválida ou expirada."
            )
        )

    usuario = usuarios[0]

    email = normalizar_email(
        usuario.get(
            "email"
        )
    )

    # -----------------------------------------------------
    # CONFIRMAR NOVAMENTE NO MERCADO PAGO
    # -----------------------------------------------------

    try:

        verificacao = (
            await verificar_assinatura_atual(
                email,
                usuario.get(
                    "subscription_id"
                )
            )
        )

        if not verificacao["pro"]:

            verificacao = (
                await verificar_assinatura_atual(
                    email
                )
            )

    except Exception as erro:

        print(
            "Erro verificando sessão no MP:",
            erro
        )

        raise HTTPException(
            status_code=502,
            detail=(
                "Não foi possível verificar "
                "sua assinatura."
            )
        )

    if not verificacao["pro"]:

        raise HTTPException(
            status_code=403,
            detail=(
                "Sua assinatura PRO não está ativa."
            )
        )

    # -----------------------------------------------------
    # ATUALIZAR BANCO
    # -----------------------------------------------------

    try:

        (
            supabase
            .table("pro_users")
            .update({

                "active":
                    True,

                "status":
                    verificacao[
                        "status"
                    ],

                "subscription_id":
                    verificacao[
                        "subscription_id"
                    ],

                "next_payment_date":
                    verificacao[
                        "next_payment_date"
                    ]

            })
            .eq(
                "email",
                email
            )
            .execute()
        )

    except Exception as erro:

        print(
            "Erro atualizando sessão:",
            erro
        )

    return {

        "ok":
            True,

        "pro":
            True,

        "status":
            verificacao[
                "status"
            ],

        "email":
            email,

        "subscription_id":
            verificacao[
                "subscription_id"
            ],

        "next_payment_date":
            verificacao[
                "next_payment_date"
            ]
    }


# =========================================================
# VERIFICAR PRO POR E-MAIL
# =========================================================

@app.get("/verificar-pro")
async def verificar_pro(
    email: str
):

    email = normalizar_email(
        email
    )

    if not email:

        raise HTTPException(
            status_code=400,
            detail="Informe o e-mail."
        )

    try:

        verificacao = (
            await verificar_assinatura_atual(
                email
            )
        )

    except Exception as erro:

        print(
            "Erro verificando PRO:",
            erro
        )

        raise HTTPException(
            status_code=502,
            detail=(
                "Erro ao consultar "
                "o Mercado Pago."
            )
        )

    return {

        "pro":
            verificacao[
                "pro"
            ],

        "status":
            verificacao[
                "status"
            ],

        "subscription_id":
            verificacao[
                "subscription_id"
            ],

        "email":
            email,

        "next_payment_date":
            verificacao[
                "next_payment_date"
            ]
    }


# =========================================================
# SINCRONIZAR PRO
# =========================================================

@app.post("/sincronizar-pro")
async def sincronizar_pro(
    dados: SessaoPRO
):

    token = str(
        dados.token or ""
    ).strip()

    if not token:

        raise HTTPException(
            status_code=401,
            detail="Sessão inválida."
        )

    if not supabase:

        raise HTTPException(
            status_code=500,
            detail=(
                "Banco de dados não configurado."
            )
        )

    try:

        resultado = (
            supabase
            .table("pro_users")
            .select("*")
            .eq("token", token)
            .limit(1)
            .execute()
        )

        usuarios = (
            resultado.data
            or []
        )

    except Exception as erro:

        print(
            "Erro buscando sessão:",
            erro
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "Erro ao consultar sessão."
            )
        )

    if not usuarios:

        raise HTTPException(
            status_code=401,
            detail="Sessão inválida."
        )

    usuario = usuarios[0]

    email = normalizar_email(
        usuario.get(
            "email"
        )
    )

    try:

        verificacao = (
            await verificar_assinatura_atual(
                email,
                usuario.get(
                    "subscription_id"
                )
            )
        )

        if not verificacao["pro"]:

            verificacao = (
                await verificar_assinatura_atual(
                    email
                )
            )

    except Exception as erro:

        print(
            "Erro sincronizando PRO:",
            erro
        )

        raise HTTPException(
            status_code=502,
            detail=(
                "Não foi possível verificar "
                "sua assinatura."
            )
        )

    if not verificacao["pro"]:

        raise HTTPException(
            status_code=403,
            detail=(
                "Sua assinatura PRO não está ativa."
            )
        )

    try:

        (
            supabase
            .table("pro_users")
            .update({

                "active":
                    True,

                "status":
                    verificacao[
                        "status"
                    ],

                "subscription_id":
                    verificacao[
                        "subscription_id"
                    ],

                "next_payment_date":
                    verificacao[
                        "next_payment_date"
                    ]

            })
            .eq(
                "email",
                email
            )
            .execute()
        )

    except Exception as erro:

        print(
            "Erro atualizando sincronização:",
            erro
        )

    return {

        "ok":
            True,

        "pro":
            True,

        "status":
            verificacao[
                "status"
            ],

        "email":
            email,

        "subscription_id":
            verificacao[
                "subscription_id"
            ],

        "next_payment_date":
            verificacao[
                "next_payment_date"
            ]
    }


# =========================================================
# WEBHOOK MERCADO PAGO
# =========================================================

@app.post("/webhook/mercadopago")
async def webhook_mercadopago(
    request: Request
):

    try:

        payload = await request.json()

        print(
            "Webhook Mercado Pago recebido:"
        )

        print(
            payload
        )

    except Exception as erro:

        print(
            "Erro lendo webhook:",
            erro
        )

    return {
        "ok": True
    }


# =========================================================
# HEALTH
# =========================================================

@app.get("/health")
async def health():

    return {

        "ok":
            True,

        "app":
            APP_NAME
    }


# =========================================================
# ROOT
# =========================================================

@app.get("/")
async def root():

    return {

        "app":
            APP_NAME,

        "status":
            "online"
    }


# =========================================================
# VERIFICAÇÃO AUTOMÁTICA
# =========================================================

async def verificar_usuarios_periodicamente():

    while True:

        try:

            if supabase:

                resultado = (
                    supabase
                    .table("pro_users")
                    .select(
                        "email,subscription_id"
                    )
                    .execute()
                )

                usuarios = (
                    resultado.data
                    or []
                )

                for usuario in usuarios:

                    email = normalizar_email(
                        usuario.get(
                            "email"
                        )
                    )

                    if email:

                        try:

                            await atualizar_assinatura_usuario(
                                email
                            )

                        except Exception as erro:

                            print(
                                "Erro verificando",
                                email,
                                erro
                            )

                        await asyncio.sleep(
                            1
                        )

        except Exception as erro:

            print(
                "Erro na verificação automática:",
                erro
            )

        await asyncio.sleep(
            60 * 60 * 24
        )


# =========================================================
# STARTUP
# =========================================================

@app.on_event(
    "startup"
)
async def startup_event():

    print(
        "===================================="
    )

    print(
        f"{APP_NAME} iniciado."
    )

    print(
        "Mercado Pago:",
        "CONFIGURADO"
        if MP_ACCESS_TOKEN
        else "NÃO CONFIGURADO"
    )

    print(
        "Supabase:",
        "CONFIGURADO"
        if supabase
        else "NÃO CONFIGURADO"
    )

    print(
        "===================================="
    )

    asyncio.create_task(
        verificar_usuarios_periodicamente()
        )
