"""Métricas da Cartaxo Store, sem dado pessoal.

Guarda só o necessário para os números do painel: tipo do evento, perfume, tamanho,
quantidade, valor em centavos, origem e aparelho. Nenhum nome, telefone ou IP.
Nome e preço do perfume vêm do catálogo publicado (o navegador só manda o código e o tamanho).
Detalhe por 90 dias; depois disso fica só o resumo por dia (para sempre).
"""
import json
import re
import threading
import time
from datetime import datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from . import banco

FUSO = ZoneInfo("America/Fortaleza")
DIAS_DETALHE = 90
ABANDONO_SEG = 30 * 60
TIPOS = {"visita", "ver", "add", "carrinho", "pedido"}
ID_PESSOA = re.compile(r"^[0-9a-f]{24}$")
ID_ITEM = re.compile(r"^(f-)?[a-z0-9][a-z0-9-]{0,40}$")
TAMANHOS = {"5", "10", "15", "frasco"}
ROBOS = re.compile(r"bot|crawl|spider|slurp|preview|facebookexternalhit|whatsapp|telegram|headless|lighthouse|pingdom|curl|wget|python-requests", re.I)

MAX_POR_DIA = 20_000           # teto de eventos guardados por dia (protege o disco contra envio em massa)
MAX_LINHAS = 500_000           # teto da tabela de eventos (cerca de 100 MB)
LOTE = 5_000                   # a limpeza apaga em lotes, sem travar o site
SEGUNDO_PLANO = True           # a limpeza automática roda numa linha de execução separada

_trava = threading.Lock()
_trava_limpeza = threading.Lock()
_cont = {"dia": None, "n": 0, "total": None}
_cat = {"chave": None, "itens": {}, "lido": 0.0}


def preparar(con) -> None:
    con.execute("""CREATE TABLE IF NOT EXISTS eventos (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, dia TEXT NOT NULL, tipo TEXT NOT NULL,
        visitante TEXT NOT NULL, sessao TEXT NOT NULL, item TEXT, nome TEXT, tamanho TEXT,
        qtd INTEGER, valor INTEGER, origem TEXT, aparelho TEXT, itens TEXT)""")
    con.execute("CREATE INDEX IF NOT EXISTS ev_dia ON eventos (dia, tipo)")
    con.execute("CREATE INDEX IF NOT EXISTS ev_sessao ON eventos (sessao)")
    con.execute("""CREATE TABLE IF NOT EXISTS resumo_dia (
        dia TEXT NOT NULL, metrica TEXT NOT NULL, n INTEGER NOT NULL, valor INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (dia, metrica))""")


def hoje(agora: float = None) -> str:
    return datetime.fromtimestamp(agora if agora is not None else time.time(), FUSO).date().isoformat()


def aparelho(ua: str) -> str:
    if re.search(r"iPad|Tablet", ua or "", re.I):
        return "Tablet"
    if re.search(r"Mobi|Android|iPhone", ua or "", re.I):
        return "Celular"
    return "Computador"


def eh_robo(ua: str) -> bool:
    return not ua or bool(ROBOS.search(ua))


def origem(ref: str, us: str, um: str, ad: int, host: str) -> str:
    us, um = (us or "").lower(), (um or "").lower()
    try:
        rh = (urlparse(ref).hostname or "").lower() if ref else ""
    except ValueError:
        rh = ""
    texto = " ".join((us, rh))
    if ad or um in ("cpc", "ppc", "paid", "ads", "anuncio", "anúncio", "paid_social", "paidsocial"):
        return "Anúncio"
    if "instagram" in texto or us in ("ig", "insta"):
        return "Instagram"
    if "facebook" in texto or us in ("fb", "meta") or rh.endswith("fb.com"):
        return "Facebook"
    if "whatsapp" in texto or us in ("wa", "zap") or rh in ("wa.me", "api.whatsapp.com"):
        return "WhatsApp"
    if "google" in texto:
        return "Google"
    if "tiktok" in texto:
        return "TikTok"
    if us:
        return "Outros: " + us[:30]
    if rh and rh != (host or "").lower() and not rh.endswith("cartaxostore.com.br"):
        return "Outros sites"
    return "Direto"


def _catalogo() -> dict:
    """Código -> {"n": nome, "z": {tamanho: preço em centavos}}, lido do site publicado (cache de 30 s)."""
    agora = time.time()
    if agora - _cat["lido"] < 30:
        return _cat["itens"]
    pub = banco.ler("site", "publicado") or {}
    chave = pub.get("publishedAt")
    if chave != _cat["chave"] or not _cat["itens"]:
        c = pub.get("content") or {}
        itens = {}
        for d in c.get("decants") or []:
            if isinstance(d, dict) and isinstance(d.get("id"), str) and isinstance(d.get("pr"), list):
                itens[d["id"]] = {"n": str(d.get("n") or d["id"])[:80],
                                  "z": {z: v for z, v in zip(("5", "10", "15"), d["pr"]) if isinstance(v, int)}}
        for f in c.get("frascos") or []:
            if isinstance(f, dict) and isinstance(f.get("id"), str) and isinstance(f.get("price"), int):
                itens[f["id"]] = {"n": str(f.get("n") or f["id"])[:80], "z": {"frasco": f["price"]}}
        _cat.update(chave=chave, itens=itens)
    _cat["lido"] = agora
    return _cat["itens"]


def _limpo(v, n=30):
    """Só letras minúsculas sem acento, números, _ e - (para utm_source e utm_medium)."""
    return re.sub(r"[^a-z0-9_-]", "", v.lower())[:n] if isinstance(v, str) else ""


def _int(v, minimo, maximo):
    return v if isinstance(v, int) and not isinstance(v, bool) and minimo <= v <= maximo else None


def _texto(v, n=80):
    return v.strip()[:n] if isinstance(v, str) else ""


def _itens(lista, cat):
    """Só o código, o tamanho e a quantidade vêm do navegador; nome e preço saem do catálogo."""
    if not isinstance(lista, list):
        return None
    saida = []
    for x in lista[:30]:
        if not isinstance(x, dict) or not isinstance(x.get("i"), str) or not ID_ITEM.match(x["i"]):
            continue
        z, q = str(x.get("z", "")), _int(x.get("q"), 1, 20)
        item = cat.get(x["i"])
        if z not in TAMANHOS or q is None or not item or z not in item["z"]:
            continue
        saida.append({"i": x["i"], "n": item["n"], "z": z, "q": q, "p": item["z"][z]})
    return saida


def validar(d: dict):
    """Devolve o evento limpo ou None se for inválido."""
    if not isinstance(d, dict):
        return None
    t, v, s = d.get("t"), d.get("v"), d.get("s")
    if t not in TIPOS or not isinstance(v, str) or not isinstance(s, str) or not ID_PESSOA.match(v) or not ID_PESSOA.match(s):
        return None
    ev = {"tipo": t, "visitante": v, "sessao": s, "item": None, "nome": None, "tamanho": None, "qtd": None, "valor": None, "itens": None}
    if t in ("ver", "add"):
        i = d.get("i")
        if not isinstance(i, str) or not ID_ITEM.match(i):
            return None
        item = _catalogo().get(i)
        if not item:
            return None          # perfume que não está no catálogo publicado
        ev.update(item=i, nome=item["n"])
        if t == "add":
            z, q = str(d.get("z", "")), _int(d.get("q"), 1, 20)
            if z not in TAMANHOS or q is None or z not in item["z"]:
                return None
            ev.update(tamanho=z, qtd=q, valor=item["z"][z] * q)
    elif t in ("carrinho", "pedido"):
        itens = _itens(d.get("itens"), _catalogo())
        if itens is None:
            return None
        if t == "pedido" and not itens:
            return None
        ev.update(itens=json.dumps(itens, ensure_ascii=False), valor=sum(x["p"] * x["q"] for x in itens), qtd=sum(x["q"] for x in itens))
    return ev


def _cabe(con, dia: str) -> bool:
    """Confere os tetos de eventos por dia e da tabela (contagem guardada em memória)."""
    if _cont["total"] is None:
        _cont["total"] = con.execute("SELECT COUNT(*) FROM eventos").fetchone()[0]
    if _cont["dia"] != dia:
        _cont["dia"], _cont["n"] = dia, con.execute("SELECT COUNT(*) FROM eventos WHERE dia=?", (dia,)).fetchone()[0]
    return _cont["n"] < MAX_POR_DIA and _cont["total"] < MAX_LINHAS


def registrar(d: dict, ua: str, host: str, agora: float = None):
    """True = guardado; "cheio" = passou do teto (descartado); False = inválido."""
    ev = validar(d)
    if ev is None:
        return False
    agora = agora if agora is not None else time.time()
    org = origem(_texto(d.get("ref"), 300), _limpo(d.get("us")), _limpo(d.get("um")), 1 if d.get("ad") in (1, True) else 0, host) if ev["tipo"] == "visita" else None
    dia = hoje(agora)
    with banco._trava, banco._db() as con:
        if not _cabe(con, dia):
            return "cheio"
        _cont["n"] += 1
        _cont["total"] += 1
        con.execute(
            "INSERT INTO eventos (ts, dia, tipo, visitante, sessao, item, nome, tamanho, qtd, valor, origem, aparelho, itens) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (int(agora), dia, ev["tipo"], ev["visitante"], ev["sessao"], ev["item"], ev["nome"], ev["tamanho"], ev["qtd"], ev["valor"], org, aparelho(ua), ev["itens"]),
        )
    limpar_se_preciso(agora)
    return True


def limpar_se_preciso(agora: float = None) -> None:
    agora = agora if agora is not None else time.time()
    ultima = banco.config("metricas_limpeza")
    if ultima and agora - float(ultima) < 6 * 3600:
        return
    banco.definir_config("metricas_limpeza", str(agora))
    if SEGUNDO_PLANO:
        threading.Thread(target=limpar, args=(agora,), daemon=True).start()
    else:
        limpar(agora)


def limpar(agora: float = None) -> int:
    """Resume por dia e apaga o detalhe com mais de 90 dias, em lotes (o site segue respondendo)."""
    if not _trava_limpeza.acquire(blocking=False):
        return 0                 # já tem uma limpeza rodando
    try:
        return _limpar(agora)
    finally:
        _trava_limpeza.release()


def _limpar(agora: float = None) -> int:
    agora = agora if agora is not None else time.time()
    corte = (datetime.fromtimestamp(agora, FUSO).date() - timedelta(days=DIAS_DETALHE)).isoformat()
    with banco._db() as con:
        dias = [r["dia"] for r in con.execute("SELECT DISTINCT dia FROM eventos WHERE dia < ?", (corte,))]
    apagados = 0
    for dia in dias:
        with banco._trava, banco._db() as con:
            linhas = {
                "visitas": con.execute("SELECT COUNT(*) n, 0 v FROM eventos WHERE dia=? AND tipo='visita'", (dia,)).fetchone(),
                "visitantes": con.execute("SELECT COUNT(DISTINCT visitante) n, 0 v FROM eventos WHERE dia=? AND tipo='visita'", (dia,)).fetchone(),
                "pedidos": con.execute("SELECT COUNT(*) n, COALESCE(SUM(valor),0) v FROM eventos WHERE dia=? AND tipo='pedido'", (dia,)).fetchone(),
                "adicionados": con.execute("SELECT COALESCE(SUM(qtd),0) n, COALESCE(SUM(valor),0) v FROM eventos WHERE dia=? AND tipo='add'", (dia,)).fetchone(),
            }
            for metrica, r in linhas.items():
                con.execute("INSERT OR REPLACE INTO resumo_dia (dia, metrica, n, valor) VALUES (?,?,?,?)", (dia, metrica, int(r["n"]), int(r["v"])))
        while True:
            with banco._trava, banco._db() as con:
                n = con.execute("DELETE FROM eventos WHERE id IN (SELECT id FROM eventos WHERE dia=? LIMIT ?)", (dia, LOTE)).rowcount
            apagados += n
            if n < LOTE:
                break
            time.sleep(0.05)
    with banco._trava:
        _cont["total"] = None    # recontar na próxima vez
    return apagados


def _top(con, sql, args, limite=8):
    return [dict(r) for r in con.execute(sql + f" LIMIT {int(limite)}", args)]


def relatorio(dias: int, agora: float = None) -> dict:
    agora = agora if agora is not None else time.time()
    dias = dias if dias in (1, 7, 30) else 7
    fim = datetime.fromtimestamp(agora, FUSO).date()
    ini = fim - timedelta(days=dias - 1)
    a, b = ini.isoformat(), fim.isoformat()
    with banco._db() as con:
        um = lambda sql: con.execute(sql, (a, b)).fetchone()
        visitas = um("SELECT COUNT(*) n FROM eventos WHERE tipo='visita' AND dia BETWEEN ? AND ?")["n"]
        visitantes = um("SELECT COUNT(DISTINCT visitante) n FROM eventos WHERE tipo='visita' AND dia BETWEEN ? AND ?")["n"]
        ped = um("SELECT COUNT(*) n, COALESCE(SUM(valor),0) v FROM eventos WHERE tipo='pedido' AND dia BETWEEN ? AND ?")
        por_dia = {r["dia"]: dict(r) for r in con.execute(
            """SELECT dia, SUM(tipo='visita') visitas, COUNT(DISTINCT CASE WHEN tipo='visita' THEN visitante END) visitantes,
                      SUM(tipo='pedido') pedidos FROM eventos WHERE dia BETWEEN ? AND ? GROUP BY dia""", (a, b))}
        serie = []
        d = ini
        while d <= fim:
            k = d.isoformat()
            r = por_dia.get(k, {})
            serie.append({"dia": k, "visitas": int(r.get("visitas") or 0), "visitantes": int(r.get("visitantes") or 0), "pedidos": int(r.get("pedidos") or 0)})
            d += timedelta(days=1)
        vistos = _top(con, "SELECT item, MAX(nome) nome, COUNT(*) n FROM eventos WHERE tipo='ver' AND dia BETWEEN ? AND ? GROUP BY item ORDER BY n DESC, nome", (a, b))
        adicionados = _top(con, "SELECT item, MAX(nome) nome, SUM(qtd) n FROM eventos WHERE tipo='add' AND dia BETWEEN ? AND ? GROUP BY item ORDER BY n DESC, nome", (a, b))
        tamanhos = [{"nome": ("Frasco inteiro" if r["tamanho"] == "frasco" else r["tamanho"] + " ml"), "n": r["n"]} for r in con.execute(
            "SELECT tamanho, SUM(qtd) n FROM eventos WHERE tipo='add' AND dia BETWEEN ? AND ? GROUP BY tamanho ORDER BY n DESC", (a, b))]
        aparelhos = [{"nome": r["aparelho"], "n": r["n"]} for r in con.execute(
            "SELECT aparelho, COUNT(*) n FROM eventos WHERE tipo='visita' AND dia BETWEEN ? AND ? GROUP BY aparelho ORDER BY n DESC", (a, b))]
        # origem de cada sessão = a da primeira visita
        origem_sessao = {r["sessao"]: r["origem"] for r in con.execute(
            "SELECT sessao, origem FROM eventos WHERE tipo='visita' AND dia BETWEEN ? AND ? ORDER BY ts DESC", (a, b))}
        sessoes_pedido = {r["sessao"] for r in con.execute("SELECT DISTINCT sessao FROM eventos WHERE tipo='pedido' AND dia BETWEEN ? AND ?", (a, b))}
        cont_origem, ped_origem = {}, {}
        for r in con.execute("SELECT origem, COUNT(*) n FROM eventos WHERE tipo='visita' AND dia BETWEEN ? AND ? GROUP BY origem", (a, b)):
            cont_origem[r["origem"] or "Direto"] = r["n"]
        for r in con.execute("SELECT sessao, COUNT(*) n FROM eventos WHERE tipo='pedido' AND dia BETWEEN ? AND ? GROUP BY sessao", (a, b)):
            o = origem_sessao.get(r["sessao"]) or "Direto"
            ped_origem[o] = ped_origem.get(o, 0) + r["n"]
            cont_origem.setdefault(o, 0)
        origens = sorted(({"nome": o, "n": n, "pedidos": ped_origem.get(o, 0)} for o, n in cont_origem.items()), key=lambda x: (-x["n"], -x["pedidos"], x["nome"]))
        # itens dos pedidos
        pedidos_itens = {}
        for r in con.execute("SELECT itens FROM eventos WHERE tipo='pedido' AND dia BETWEEN ? AND ?", (a, b)):
            for x in json.loads(r["itens"] or "[]"):
                e = pedidos_itens.setdefault(x["i"], {"nome": x["n"], "n": 0, "valor": 0})
                e["n"] += x["q"]
                e["valor"] += x["q"] * x["p"]
        # carrinhos abandonados: último carrinho da sessão com itens, sem pedido e parado há 30 min
        ultimos = con.execute(
            """SELECT e.sessao, e.itens, e.valor, e.ts FROM eventos e
               JOIN (SELECT sessao, MAX(id) mid FROM eventos WHERE tipo='carrinho' AND dia BETWEEN ? AND ? GROUP BY sessao) u ON e.id=u.mid""", (a, b)).fetchall()
        ativos = {r["sessao"]: r["ts"] for r in con.execute("SELECT sessao, MAX(ts) ts FROM eventos WHERE dia BETWEEN ? AND ? GROUP BY sessao", (a, b))}
    abandonados, valor_ab, ab_itens = 0, 0, {}
    for r in ultimos:
        itens = json.loads(r["itens"] or "[]")
        if not itens or r["sessao"] in sessoes_pedido or agora - ativos.get(r["sessao"], r["ts"]) < ABANDONO_SEG:
            continue
        abandonados += 1
        valor_ab += int(r["valor"] or 0)
        for x in itens:
            e = ab_itens.setdefault(x["i"], {"nome": x["n"], "n": 0})
            e["n"] += x["q"]
    ordenar = lambda dic: sorted(dic.values(), key=lambda x: (-x["n"], x["nome"]))[:8]
    return {
        "dias": dias, "inicio": a, "fim": b,
        "visitas": visitas, "visitantes": visitantes, "pedidos": ped["n"], "valor_pedidos": int(ped["v"]),
        "abandonados": abandonados, "valor_abandonado": valor_ab,
        "por_dia": serie,
        "top_vistos": [{"nome": r["nome"], "n": r["n"]} for r in vistos],
        "top_adicionados": [{"nome": r["nome"], "n": r["n"]} for r in adicionados],
        "top_pedidos": ordenar(pedidos_itens),
        "top_abandonados": ordenar(ab_itens),
        "origens": origens[:10], "aparelhos": aparelhos, "tamanhos": tamanhos,
    }
