"""Login do painel, sessões assinadas, bloqueio de tentativas e cabeçalhos de proteção.

A senha nunca fica no código: o Antonio define ADMIN_EMAIL e ADMIN_SENHA direto no Railway.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time

from . import banco

COOKIE = "cx_sessao"
DURACAO = 12 * 60 * 60          # sessão de 12 horas
JANELA = 15 * 60                # janela de contagem das tentativas
MAX_POR_IP = 5                  # 5 senhas erradas por endereço bloqueiam esse endereço por 15 minutos
MUITOS_ERROS = 20               # acima disso (somando todos), cada tentativa fica mais lenta, sem trancar o dono

_tentativas: dict = {}
_geral: list = []
_trava = threading.Lock()


def credenciais():
    email = (os.environ.get("ADMIN_EMAIL") or "").strip().lower()
    senha = os.environ.get("ADMIN_SENHA") or ""
    return (email, senha) if email and len(senha) >= 8 else (None, None)


def _segredo() -> bytes:
    s = os.environ.get("SESSION_SECRET")
    if not s:
        s = banco.config("segredo_sessao")
        if not s:
            s = secrets.token_hex(32)
            banco.definir_config("segredo_sessao", s)
    _, senha = credenciais()
    # Trocar a senha no Railway derruba todas as sessões abertas.
    return hashlib.sha256((s + "|" + hashlib.sha256((senha or "").encode()).hexdigest()).encode()).digest()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _geracao() -> int:
    try:
        return int(banco.config("geracao_sessao") or 0)
    except ValueError:
        return 0


def encerrar_sessoes() -> None:
    """Sair da conta derruba todas as sessões abertas (inclusive cópias do cookie)."""
    banco.definir_config("geracao_sessao", str(_geracao() + 1))


def criar_token() -> str:
    corpo = _b64(json.dumps({"e": int(time.time()) + DURACAO, "g": _geracao(), "n": secrets.token_hex(8)}).encode())
    assinatura = _b64(hmac.new(_segredo(), corpo.encode(), hashlib.sha256).digest())
    return corpo + "." + assinatura


def token_valido(token: str) -> bool:
    if not token or token.count(".") != 1 or credenciais()[0] is None:
        return False
    corpo, assinatura = token.split(".")
    esperado = _b64(hmac.new(_segredo(), corpo.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(esperado, assinatura):
        return False
    try:
        dados = json.loads(_unb64(corpo))
        return int(dados["e"]) > time.time() and int(dados.get("g", -1)) == _geracao()
    except Exception:
        return False


def conferir(email: str, senha: str) -> bool:
    e, s = credenciais()
    if e is None:
        return False
    ok_email = hmac.compare_digest(hashlib.sha256((email or "").strip().lower().encode()).digest(), hashlib.sha256(e.encode()).digest())
    ok_senha = hmac.compare_digest(hashlib.sha256((senha or "").encode()).digest(), hashlib.sha256(s.encode()).digest())
    return ok_email and ok_senha


def ip_do_cliente(request) -> str:
    # Com o Cloudflare na frente, o endereço real do visitante vem neste cabeçalho.
    # O último item do X-Forwarded-For é o que o próprio Railway acrescenta (não dá para forjar).
    cf = request.headers.get("cf-connecting-ip")
    if cf:
        return cf.strip()[:64]
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[-1].strip()[:64]
    return request.client.host if request.client else "?"


def bloqueado(ip: str) -> bool:
    agora = time.time()
    with _trava:
        lista = [t for t in _tentativas.get(ip, []) if agora - t < JANELA]
        _tentativas[ip] = lista
        return len(lista) >= MAX_POR_IP


def atraso() -> float:
    """Segundos de espera antes de conferir a senha: sobe com o total de erros recentes."""
    agora = time.time()
    with _trava:
        _geral[:] = [t for t in _geral if agora - t < JANELA]
        n = len(_geral)
    return 0.0 if n < MUITOS_ERROS else min(5.0, 0.5 * (n - MUITOS_ERROS + 1))


def registrar_erro(ip: str) -> None:
    agora = time.time()
    with _trava:
        _tentativas.setdefault(ip, []).append(agora)
        _geral.append(agora)


def limpar_erros(ip: str) -> None:
    with _trava:
        _tentativas.pop(ip, None)


def zerar_bloqueios() -> None:
    """Só para os testes."""
    with _trava:
        _tentativas.clear()
        _geral.clear()


def cabecalhos(resposta, https: bool) -> None:
    h = resposta.headers
    h["X-Content-Type-Options"] = "nosniff"
    h["X-Frame-Options"] = "DENY"
    h["Referrer-Policy"] = "strict-origin-when-cross-origin"
    h["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
    h["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; "
        "img-src 'self' data: blob:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'"
    )
    if https:
        h["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
