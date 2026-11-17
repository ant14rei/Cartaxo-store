"""Servidor do site Cartaxo Store (decants e perfumes).

Rotas públicas: /, /_blob/<id> (fotos), /api/db/site/publicado, /api/eu, /saude.
Rotas do painel (com login): /entrar, /sair, /api/db/..., /api/fotos, /api/backup.
"""
import asyncio
import html
import io
import json
import re
import secrets
import tempfile
import threading
import time
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from . import banco, metricas, seguranca

RAIZ = Path(__file__).resolve().parent.parent
FUSO = ZoneInfo("America/Fortaleza")
MAX_DOC = 600 * 1024            # 600 KB por documento de conteúdo
MAX_FOTO = 12 * 1024 * 1024     # 12 MB por foto (o painel já reduz para 1600 px)
COTA_FOTOS = 3 * 1024 * 1024 * 1024  # 3 GB de fotos no total
ID_DOC = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
ID_FOTO = re.compile(r"^[0-9a-f]{32}$")
# Quem pode ler e gravar cada documento (o resto não existe)
REGRAS = {("site", "publicado"): "publico", ("rascunho", "atual"): "admin"}

_paginas: dict = {}


def pagina(nome: str) -> str:
    if nome not in _paginas:
        _paginas[nome] = (RAIZ / "static" / nome).read_text(encoding="utf-8")
    return _paginas[nome]


def logado(request: Request) -> bool:
    return seguranca.token_valido(request.cookies.get(seguranca.COOKIE, ""))


def https(request: Request) -> bool:
    return request.headers.get("x-forwarded-proto", request.url.scheme) == "https"


def mesma_origem(request: Request) -> bool:
    origem = request.headers.get("origin")
    if not origem:
        return True
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
    return origem.split("://", 1)[-1] == host


def pedido_seguro(request: Request) -> bool:
    """Proteção contra pedidos forjados por outros sites."""
    return request.headers.get("x-cartaxo") == "1" and mesma_origem(request)


def json_seguro(dados) -> str:
    return json.dumps(dados, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


async def ler_limitado(request: Request, limite: int):
    """Lê o corpo em partes e para assim que passar do limite."""
    try:
        if int(request.headers.get("content-length", "0")) > limite:
            return None
    except ValueError:
        return None
    partes, total = [], 0
    async for parte in request.stream():
        total += len(parte)
        if total > limite:
            return None
        partes.append(parte)
    return b"".join(partes)


def nao_autorizado():
    return JSONResponse({"erro": "Entre no painel para fazer isso."}, status_code=401)


# ---------------- páginas ----------------
async def inicio(request: Request):
    pub = banco.ler("site", "publicado")
    inicial = "<script>window.CARTAXO_INICIAL=" + (json_seguro(pub) if pub else "null") + ";</script>"
    corpo = pagina("index.html").replace("<!--CARTAXO_INICIAL-->", inicial, 1)
    return HTMLResponse(corpo, headers={"Cache-Control": "no-cache"})


def tela_entrar(msg: str = "", status: int = 200):
    aviso = f'<p class="erro" role="alert">{html.escape(msg)}</p>' if msg else ""
    if seguranca.credenciais()[0] is None:
        aviso = '<p class="erro" role="alert">O acesso ao painel ainda não foi configurado. Defina ADMIN_EMAIL e ADMIN_SENHA (mínimo de 8 caracteres) no Railway.</p>'
    return HTMLResponse(pagina("entrar.html").replace("<!--AVISO-->", aviso, 1), status_code=status, headers={"Cache-Control": "no-store"})


async def entrar(request: Request):
    if request.method == "GET":
        if logado(request):
            return RedirectResponse("/#admin", status_code=303)
        return tela_entrar()
    if not mesma_origem(request):
        return tela_entrar("Pedido recusado. Abra a página de entrada de novo.", 403)
    ip = seguranca.ip_do_cliente(request)
    if seguranca.bloqueado(ip):
        return tela_entrar("Muitas tentativas erradas. Aguarde 15 minutos e tente de novo.", 429)
    espera = seguranca.atraso()
    if espera:
        await asyncio.sleep(espera)
    form = await request.form(max_files=0, max_fields=5)
    if not seguranca.conferir(str(form.get("email", "")), str(form.get("senha", ""))):
        seguranca.registrar_erro(ip)
        return tela_entrar("E-mail ou senha incorretos.", 401)
    seguranca.limpar_erros(ip)
    r = RedirectResponse("/#admin", status_code=303)
    r.set_cookie(seguranca.COOKIE, seguranca.criar_token(), max_age=seguranca.DURACAO, httponly=True, secure=https(request), samesite="strict", path="/")
    return r


async def sair(request: Request):
    if not mesma_origem(request):
        return Response("Pedido recusado.", status_code=403)
    if logado(request):
        seguranca.encerrar_sessoes()
    r = RedirectResponse("/", status_code=303)
    r.delete_cookie(seguranca.COOKIE, path="/")
    return r


async def saude(request: Request):
    return JSONResponse({"ok": True})


async def eu(request: Request):
    return JSONResponse({"admin": logado(request)}, headers={"Cache-Control": "no-store"})


# ---------------- conteúdo ----------------
def regra(col: str, id_: str):
    if (col, id_) in REGRAS:
        return REGRAS[(col, id_)]
    if col == "historico" and ID_DOC.match(id_):
        return "admin"
    return None


def documento_valido(col: str, dados) -> bool:
    if not isinstance(dados, dict) or not isinstance(dados.get("content"), dict):
        return False
    if col == "historico" and not isinstance(dados.get("at"), str):
        return False
    return True


async def doc(request: Request):
    col, id_ = request.path_params["col"], request.path_params["id"]
    r = regra(col, id_)
    if r is None:
        return JSONResponse({"erro": "Não encontrado."}, status_code=404)
    publico_leitura = r == "publico" and request.method == "GET"
    if not publico_leitura and not logado(request):
        return nao_autorizado()
    if request.method != "GET" and not pedido_seguro(request):
        return JSONResponse({"erro": "Pedido recusado."}, status_code=403)
    if request.method == "GET":
        dados = banco.ler(col, id_)
        if dados is None:
            return JSONResponse({"erro": "Não encontrado."}, status_code=404)
        return JSONResponse({"id": id_, "data": dados}, headers={"Cache-Control": "no-store"})
    if request.method == "PUT":
        corpo = await ler_limitado(request, MAX_DOC)
        if corpo is None:
            return JSONResponse({"erro": "Conteúdo grande demais."}, status_code=413)
        try:
            dados = json.loads(corpo)
        except ValueError:
            return JSONResponse({"erro": "Conteúdo inválido."}, status_code=400)
        if not documento_valido(col, dados):
            return JSONResponse({"erro": "Conteúdo inválido."}, status_code=400)
        banco.gravar(col, id_, dados)
        if col == "historico":   # guarda no máximo 50 versões antigas
            for velho in [d for d in banco.listar("historico", "at", desc=True, limite=1000) if not d["id"].startswith("antes-da-leva")][50:]:
                banco.apagar("historico", velho["id"])
        return JSONResponse({"ok": True})
    if request.method == "DELETE":
        if col != "historico":
            return JSONResponse({"erro": "Não é possível apagar este conteúdo."}, status_code=405)
        banco.apagar(col, id_)
        return JSONResponse({"ok": True})
    return JSONResponse({"erro": "Método não aceito."}, status_code=405)


async def colecao(request: Request):
    col = request.path_params["col"]
    if col != "historico":
        return JSONResponse({"erro": "Não encontrado."}, status_code=404)
    if not logado(request):
        return nao_autorizado()
    campo = request.query_params.get("ordem", "")
    campo = campo if campo in ("", "at") else ""
    try:
        limite = int(request.query_params.get("limite", "100"))
    except ValueError:
        limite = 100
    docs = banco.listar(col, campo, desc=request.query_params.get("dir") == "desc", limite=limite)
    return JSONResponse({"docs": docs}, headers={"Cache-Control": "no-store"})


# ---------------- fotos ----------------
def tipo_da_foto(cabeca: bytes):
    if cabeca.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if cabeca.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if cabeca[:4] == b"RIFF" and cabeca[8:12] == b"WEBP":
        return "image/webp"
    if cabeca[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return None


async def enviar_foto(request: Request):
    if not logado(request):
        return nao_autorizado()
    if not pedido_seguro(request):
        return JSONResponse({"erro": "Pedido recusado."}, status_code=403)
    try:
        if int(request.headers.get("content-length", "0")) > MAX_FOTO + 64 * 1024:
            return JSONResponse({"erro": "A foto passa de 12 MB."}, status_code=413)
    except ValueError:
        return JSONResponse({"erro": "Pedido inválido."}, status_code=400)
    async with request.form(max_files=1, max_fields=2) as form:
        arq = form.get("foto")
        if arq is None or not hasattr(arq, "read"):
            return JSONResponse({"erro": "Nenhuma foto enviada."}, status_code=400)
        dados = await arq.read(MAX_FOTO + 1)
    if len(dados) > MAX_FOTO:
        return JSONResponse({"erro": "A foto passa de 12 MB."}, status_code=413)
    tipo = tipo_da_foto(dados[:16])
    if not dados or tipo is None:
        return JSONResponse({"erro": "Formato não aceito. Use PNG, JPG, WEBP ou GIF."}, status_code=415)
    if banco.espaco_fotos() + len(dados) > COTA_FOTOS:
        return JSONResponse({"erro": "O espaço de fotos acabou. Remova fotos antigas."}, status_code=507)
    id_ = secrets.token_hex(16)
    (banco.pasta_fotos() / id_).write_bytes(dados)
    banco.registrar_foto(id_, tipo, len(dados))
    return JSONResponse({"id": id_, "tipo": tipo, "bytes": len(dados)})


async def ver_foto(request: Request):
    id_ = request.path_params["id"]
    info = banco.foto(id_) if ID_FOTO.match(id_) else None
    caminho = banco.pasta_fotos() / id_
    if not info or not caminho.is_file():
        return Response("Foto não encontrada.", status_code=404)
    return FileResponse(caminho, media_type=info["tipo"], headers={"Cache-Control": "public, max-age=31536000, immutable"})


# ---------------- métricas (anônimas) ----------------
_limite_ev: dict = {}
_trava_ev = threading.Lock()
MAX_EV_MIN = 120          # eventos por minuto por endereço


def _muitos_eventos(ip: str) -> bool:
    agora = time.time()
    with _trava_ev:
        if len(_limite_ev) > 5000:          # tira só os endereços parados; se continuar cheio, recusa endereços novos
            for k in [k for k, v in _limite_ev.items() if not v or agora - v[-1] >= 60]:
                del _limite_ev[k]
            if len(_limite_ev) > 5000 and ip not in _limite_ev:
                return True
        lista = [t for t in _limite_ev.get(ip, []) if agora - t < 60]
        lista.append(agora)
        _limite_ev[ip] = lista
        return len(lista) > MAX_EV_MIN


async def evento(request: Request):
    if not mesma_origem(request):
        return Response(status_code=403)
    if _muitos_eventos(seguranca.ip_do_cliente(request)):
        return Response(status_code=429)
    corpo = await ler_limitado(request, 8 * 1024)
    if corpo is None:
        return Response(status_code=413)
    ua = request.headers.get("user-agent", "")
    if logado(request) or metricas.eh_robo(ua):
        return Response(status_code=204)      # o dono e os robôs não entram na conta
    try:
        dados = json.loads(corpo)
    except ValueError:
        return Response(status_code=400)
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
    resultado = metricas.registrar(dados, ua, host.split(":")[0])
    if resultado == "cheio":
        return Response(status_code=204)      # teto do dia ou da tabela: descarta sem erro para o visitante
    if not resultado:
        return Response(status_code=400)
    return Response(status_code=204)


async def ver_metricas(request: Request):
    if not logado(request):
        return nao_autorizado()
    try:
        dias = int(request.query_params.get("dias", "7"))
    except ValueError:
        dias = 7
    return JSONResponse(metricas.relatorio(dias), headers={"Cache-Control": "no-store"})


# ---------------- ícones, manifesto e robots ----------------
ESTATICOS = {
    "/favicon.ico": ("favicon.ico", "image/x-icon"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
    "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
    "/icon-192.png": ("icon-192.png", "image/png"),
    "/icon-512.png": ("icon-512.png", "image/png"),
    "/site.webmanifest": ("site.webmanifest", "application/manifest+json"),
    "/robots.txt": ("robots.txt", "text/plain; charset=utf-8"),
}


async def estatico(request: Request):
    nome, tipo = ESTATICOS[request.url.path]
    return FileResponse(RAIZ / "static" / "icones" / nome, media_type=tipo, headers={"Cache-Control": "public, max-age=86400"})


# ---------------- cópia de segurança ----------------
async def backup(request: Request):
    if not logado(request):
        return RedirectResponse("/entrar", status_code=303)
    mem = io.BytesIO()
    with tempfile.TemporaryDirectory() as tmp:
        copia = Path(tmp) / "cartaxo.db"
        banco.copia_do_banco(copia)
        with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(copia, "cartaxo.db")
            for f in banco.pasta_fotos().iterdir():
                if f.is_file() and ID_FOTO.match(f.name):
                    z.write(f, "fotos/" + f.name)
    nome = "cartaxo-backup-" + datetime.now(FUSO).strftime("%Y-%m-%d-%Hh%M") + ".zip"
    return Response(mem.getvalue(), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{nome}"', "Cache-Control": "no-store"})


class Protecao(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        resposta = await call_next(request)
        seguranca.cabecalhos(resposta, https(request))
        return resposta


@asynccontextmanager
async def ciclo(app):
    banco.preparar()
    print("Banco pronto", flush=True)
    yield


app = Starlette(
    routes=[
        Route("/", inicio),
        Route("/entrar", entrar, methods=["GET", "POST"]),
        Route("/sair", sair, methods=["POST"]),
        Route("/saude", saude),
        Route("/api/eu", eu),
        Route("/api/db/{col}/{id}", doc, methods=["GET", "PUT", "DELETE"]),
        Route("/api/db/{col}", colecao),
        Route("/api/fotos", enviar_foto, methods=["POST"]),
        Route("/api/backup", backup),
        Route("/_blob/{id}", ver_foto),
        Route("/api/ev", evento, methods=["POST"]),
        Route("/api/metricas", ver_metricas),
        *[Route(caminho, estatico) for caminho in ESTATICOS],
    ],
    middleware=[],
    lifespan=ciclo,
)
app.add_middleware(Protecao)
