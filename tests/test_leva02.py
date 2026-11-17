"""Testes da Leva 02: sem descontos, entrega a combinar, ícones e métricas anônimas.
Rode: python3 -m unittest discover -s tests -q (só dados de teste, nunca o site no ar)."""
import json
import os
import sys
import time
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "tests"))

from test_servidor import EMAIL, SENHA, Base, H  # noqa: E402

from app import banco, main, metricas, seguranca  # noqa: E402

metricas.SEGUNDO_PLANO = False   # nos testes a limpeza roda na hora, para o resultado ser previsível

CEL = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Mobile/15E148 Safari/604.1"}
PC = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130.0 Safari/537.36"}
V1, S1 = "a" * 24, "b" * 24
V2, S2 = "c" * 24, "d" * 24


def antigo():
    """Conteúdo como estava no site antes da Leva 02 (com kit e desconto)."""
    c = json.loads((RAIZ / "seed" / "conteudo.json").read_text(encoding="utf-8"))["content"]
    c["kit"] = {"tiers": [[5, 10], [3, 5]], "eligible": [10, 15], "cap": 8000}
    c["texts"].update({
        "k.titulo": "Prove mais, *pague menos*",
        "k.texto": "Com 3 decants ou mais no pedido, você ganha 5% de desconto.",
        "l.nota": "Fotos de exemplo. Frascos inteiros não entram no desconto do Kit Descoberta.",
        "g.aviso": "Prévia: perfumes, preços, entrega e regras do kit são de exemplo.",
        "e.texto": "Valores de exemplo. O valor final é confirmado pela loja no WhatsApp.",
        "d.subtitulo": "Decants de 5, 10 e 15 ml envasados do frasco original. Escolha, monte seu kit e finalize o pedido pelo WhatsApp.",
        "d.item3": "Frasco com válvula de recrave na máquina",
        "c.p2": "Selecione os perfumes que deseja conhecer e monte uma combinação exclusiva",
    })
    c["ship"] = [
        {"id": "retirada", "n": "Retirada combinada", "d": "Com agendamento", "prazo": "A combinar", "v": 0},
        {"id": "ne", "n": "Correios PAC", "d": "Valores estimados", "prazo": "Conforme os correios", "v": 5790, "freeOver": 9990},
    ]
    c["contato"] = {"whats": "5583981857161", "insta": "cartaxostore_"}
    return c


class TestMigracao(Base):
    def preparar_antigo(self):
        banco.gravar("site", "publicado", {"content": antigo(), "publishedAt": "2026-11-16T21:01:43Z"})
        banco.gravar("rascunho", "atual", {"content": antigo(), "savedAt": "2026-11-16T21:01:43Z"})
        with banco._db() as con:
            con.execute("DELETE FROM config WHERE chave='migracao_leva02'")
        banco.migrar_leva02()

    def test_tira_kit_e_textos_de_desconto(self):
        self.preparar_antigo()
        for doc in ("site/publicado", "rascunho/atual"):
            c = banco.ler(*doc.split("/"))["content"]
            self.assertNotIn("kit", c, doc)
            for k in ("k.titulo", "k.texto", "l.nota", "g.aviso", "e.texto", "d.subtitulo"):
                self.assertNotIn(k, c["texts"], f"{doc} {k}")

    def test_mantem_textos_do_dono(self):
        self.preparar_antigo()
        t = banco.ler("site", "publicado")["content"]["texts"]
        self.assertEqual(t["d.item3"], "Frasco com válvula de recrave na máquina")
        self.assertEqual(t["c.p2"], "Selecione os perfumes que deseja conhecer e monte uma combinação exclusiva")

    def test_entrega_a_combinar_e_instagram(self):
        self.preparar_antigo()
        c = banco.ler("site", "publicado")["content"]
        self.assertTrue(all(e["comb"] for e in c["ship"]))
        self.assertEqual(c["ship"][1]["v"], 5790, "o valor fica guardado para quando ele desmarcar 'a combinar'")
        self.assertNotIn("freeOver", c["ship"][1])
        self.assertEqual(c["contato"]["insta"], "cartaxostore__")

    def test_guarda_copia_e_roda_uma_vez(self):
        self.preparar_antigo()
        copia = banco.ler("historico", "antes-da-leva-02")
        self.assertIn("kit", copia["content"])
        depois = banco.ler("site", "publicado")
        banco.gravar("site", "publicado", {"content": antigo(), "publishedAt": "x"})
        banco.migrar_leva02()
        self.assertIn("kit", banco.ler("site", "publicado")["content"], "a migração não pode rodar de novo")
        self.assertNotEqual(depois, None)

    def test_site_novo_ja_sem_desconto(self):
        c = banco.ler("site", "publicado")["content"]
        self.assertNotIn("kit", c)
        self.assertIsNone(banco.ler("historico", "antes-da-leva-02"))


class TestPagina(Base):
    def test_pagina_sem_kit_e_com_quem_somos(self):
        r = self.c.get("/")
        self.assertNotIn("Kit Descoberta", r.text)
        self.assertNotIn("kitTiers", r.text)
        self.assertNotIn("monte seu kit", r.text)
        self.assertIn('id="sobre"', r.text)
        self.assertIn("https://www.instagram.com/cartaxostore__/", r.text)
        self.assertIn("<b>Cartaxo Store</b>", r.text)
        self.assertNotIn("STORE · DECANTS", r.text)
        self.assertIn('href="/favicon.svg"', r.text)
        self.assertIn('rel="manifest"', r.text)

    def test_login_com_nome_e_icone(self):
        r = self.c.get("/entrar")
        self.assertIn("<b>Cartaxo Store</b>", r.text)
        self.assertIn('href="/favicon.ico"', r.text)

    def test_icones_manifesto_e_robots(self):
        tipos = {"/favicon.ico": "image/x-icon", "/favicon.svg": "image/svg+xml", "/apple-touch-icon.png": "image/png",
                 "/icon-192.png": "image/png", "/icon-512.png": "image/png", "/site.webmanifest": "application/manifest+json"}
        for url, tipo in tipos.items():
            r = self.c.get(url)
            self.assertEqual(r.status_code, 200, url)
            self.assertTrue(r.headers["content-type"].startswith(tipo), url)
            self.assertGreater(len(r.content), 100, url)
        m = self.c.get("/site.webmanifest").json()
        self.assertEqual(m["name"], "Cartaxo Store")
        robots = self.c.get("/robots.txt").text
        self.assertIn("Disallow: /entrar", robots)
        self.assertIn("Disallow: /api/", robots)


class TestEventos(Base):
    def ev(self, dados, cab=CEL):
        return self.c.post("/api/ev", headers=cab, content=json.dumps(dados))

    def rel(self, dias=7, agora=None):
        return metricas.relatorio(dias, agora)

    def test_visita_conta_com_origem_e_aparelho(self):
        r = self.ev({"t": "visita", "v": V1, "s": S1, "ref": "https://l.instagram.com/", "us": "", "um": "", "ad": 0})
        self.assertEqual(r.status_code, 204)
        self.ev({"t": "visita", "v": V1, "s": S1, "ref": "", "us": "", "um": "", "ad": 0})
        self.ev({"t": "visita", "v": V2, "s": S2, "ref": "", "us": "instagram", "um": "anuncio", "ad": 0}, PC)
        d = self.rel()
        self.assertEqual(d["visitas"], 3)
        self.assertEqual(d["visitantes"], 2)
        origens = {o["nome"]: o["n"] for o in d["origens"]}
        self.assertEqual(origens.get("Instagram"), 1)
        self.assertEqual(origens.get("Anúncio"), 1)
        self.assertEqual(origens.get("Direto"), 1)
        aparelhos = {a["nome"]: a["n"] for a in d["aparelhos"]}
        self.assertEqual(aparelhos, {"Celular": 2, "Computador": 1})

    def test_nao_guarda_dado_pessoal(self):
        self.ev({"t": "visita", "v": V1, "s": S1, "ref": "", "telefone": "83999999999", "nome": "Fulano"})
        with banco._db() as con:
            colunas = [r[1] for r in con.execute("PRAGMA table_info(eventos)")]
            linha = dict(con.execute("SELECT * FROM eventos").fetchone())
        self.assertNotIn("ip", colunas)
        self.assertNotIn("83999999999", json.dumps(linha))

    def test_robo_e_dono_nao_contam(self):
        self.ev({"t": "visita", "v": V1, "s": S1}, {"User-Agent": "facebookexternalhit/1.1"})
        self.ev({"t": "visita", "v": V1, "s": S1}, {"User-Agent": ""})
        self.c.post("/entrar", data={"email": EMAIL, "senha": SENHA})
        self.assertEqual(self.ev({"t": "visita", "v": V1, "s": S1}).status_code, 204)
        self.assertEqual(self.rel()["visitas"], 0)

    def test_eventos_invalidos(self):
        self.assertEqual(self.ev({"t": "hack", "v": V1, "s": S1}).status_code, 400)
        self.assertEqual(self.ev({"t": "visita", "v": "123", "s": S1}).status_code, 400)
        self.assertEqual(self.ev({"t": "add", "v": V1, "s": S1, "i": "../x", "z": "5", "q": 1, "p": 100}).status_code, 400)
        self.assertEqual(self.ev({"t": "add", "v": V1, "s": S1, "i": "khamrah", "z": "7", "q": 1, "p": 100}).status_code, 400)
        self.assertEqual(self.ev({"t": "pedido", "v": V1, "s": S1, "itens": []}).status_code, 400)
        self.assertEqual(self.c.post("/api/ev", headers=CEL, content=b"{nao e json").status_code, 400)
        self.assertEqual(self.c.post("/api/ev", headers=CEL, content=b"x" * 9000).status_code, 413)
        self.assertEqual(self.c.post("/api/ev", headers={**CEL, "Origin": "https://site-falso.com"}, content=b"{}").status_code, 403)

    def test_limite_de_eventos(self):
        main._limite_ev.clear()
        codigos = [self.ev({"t": "visita", "v": V1, "s": S1}, {**CEL, "CF-Connecting-IP": "200.9.9.9"}).status_code for _ in range(main.MAX_EV_MIN + 3)]
        self.assertEqual(codigos[-1], 429)
        main._limite_ev.clear()

    def test_perfumes_pedidos_tamanhos_e_abandono(self):
        item = lambda i, n, z, q, p: {"i": i, "n": n, "z": z, "q": q, "p": p}
        # sessão 1: vê, adiciona e envia o pedido
        self.ev({"t": "visita", "v": V1, "s": S1})
        self.ev({"t": "ver", "v": V1, "s": S1, "i": "khamrah", "n": "Khamrah"})
        self.ev({"t": "add", "v": V1, "s": S1, "i": "khamrah", "n": "Khamrah", "z": "10", "q": 2, "p": 4490})
        carrinho1 = [item("khamrah", "Khamrah", "10", 2, 4490)]
        self.ev({"t": "carrinho", "v": V1, "s": S1, "itens": carrinho1, "tot": 8980})
        self.ev({"t": "pedido", "v": V1, "s": S1, "itens": carrinho1, "tot": 8980})
        # sessão 2: adiciona e some (carrinho abandonado)
        antes = time.time() - 3600
        metricas.registrar({"t": "visita", "v": V2, "s": S2, "us": "whatsapp"}, CEL["User-Agent"], "www.cartaxostore.com.br", antes)
        metricas.registrar({"t": "add", "v": V2, "s": S2, "i": "f-asad", "n": "Asad", "z": "frasco", "q": 1, "p": 24990}, CEL["User-Agent"], "", antes)
        metricas.registrar({"t": "carrinho", "v": V2, "s": S2, "itens": [item("f-asad", "Asad", "frasco", 1, 24990)], "tot": 24990}, CEL["User-Agent"], "", antes)
        d = self.rel()
        self.assertEqual(d["pedidos"], 1)
        self.assertEqual(d["valor_pedidos"], 8980)
        self.assertEqual(d["abandonados"], 1)
        self.assertEqual(d["valor_abandonado"], 24990)
        self.assertEqual(d["top_vistos"][0], {"nome": "Khamrah", "n": 1})
        self.assertEqual(d["top_pedidos"][0], {"nome": "Khamrah", "n": 2, "valor": 8980})
        self.assertEqual(d["top_abandonados"][0], {"nome": "Asad", "n": 1})
        tamanhos = {t["nome"]: t["n"] for t in d["tamanhos"]}
        self.assertEqual(tamanhos, {"10 ml": 2, "Frasco inteiro": 1})
        origens = {o["nome"]: o for o in d["origens"]}
        self.assertEqual(origens["Direto"]["pedidos"], 1)
        self.assertEqual(origens["WhatsApp"]["pedidos"], 0)
        self.assertEqual(len(d["por_dia"]), 7)

    def test_carrinho_recente_ainda_nao_e_abandono(self):
        self.ev({"t": "carrinho", "v": V1, "s": S1, "itens": [{"i": "yara", "n": "Yara", "z": "5", "q": 1, "p": 2290}], "tot": 2290})
        self.assertEqual(self.rel()["abandonados"], 0)
        self.assertEqual(self.rel(agora=time.time() + 31 * 60)["abandonados"], 1)

    def test_carrinho_esvaziado_nao_e_abandono(self):
        antes = time.time() - 3600
        metricas.registrar({"t": "carrinho", "v": V1, "s": S1, "itens": [{"i": "yara", "n": "Yara", "z": "5", "q": 1, "p": 2290}]}, CEL["User-Agent"], "", antes)
        metricas.registrar({"t": "carrinho", "v": V1, "s": S1, "itens": []}, CEL["User-Agent"], "", antes + 10)
        self.assertEqual(self.rel()["abandonados"], 0)

    def test_painel_de_metricas_pede_login(self):
        self.assertEqual(self.c.get("/api/metricas").status_code, 401)
        self.c.post("/entrar", data={"email": EMAIL, "senha": SENHA})
        r = self.c.get("/api/metricas?dias=30")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["dias"], 30)
        self.assertEqual(len(r.json()["por_dia"]), 30)
        self.assertEqual(self.c.get("/api/metricas?dias=999").json()["dias"], 7)
        self.assertEqual(self.c.get("/api/metricas?dias=1").json()["por_dia"][0]["dia"], metricas.hoje())

    def test_guarda_90_dias_e_resumo(self):
        velho = time.time() - 100 * 86400
        metricas.registrar({"t": "visita", "v": V1, "s": S1}, CEL["User-Agent"], "", velho)
        metricas.registrar({"t": "pedido", "v": V1, "s": S1, "itens": [{"i": "yara", "n": "Yara", "z": "5", "q": 1, "p": 2290}]}, CEL["User-Agent"], "", velho)
        self.ev({"t": "visita", "v": V2, "s": S2})
        metricas.limpar()   # a limpeza também roda sozinha a cada 6 horas
        with banco._db() as con:
            resumo = {r["metrica"]: (r["n"], r["valor"]) for r in con.execute("SELECT * FROM resumo_dia")}
            restantes = con.execute("SELECT COUNT(*) FROM eventos").fetchone()[0]
        self.assertEqual(resumo["visitas"], (1, 0))
        self.assertEqual(resumo["pedidos"], (1, 2290))
        self.assertEqual(restantes, 1)

    def test_origens(self):
        o = metricas.origem
        self.assertEqual(o("", "", "", 1, "x"), "Anúncio")
        self.assertEqual(o("https://www.google.com/", "", "", 0, "x"), "Google")
        self.assertEqual(o("https://wa.me/", "", "", 0, "x"), "WhatsApp")
        self.assertEqual(o("https://www.cartaxostore.com.br/", "", "", 0, "www.cartaxostore.com.br"), "Direto")
        self.assertEqual(o("https://blog.exemplo.com/", "", "", 0, "www.cartaxostore.com.br"), "Outros sites")
        self.assertEqual(o("", "panfleto", "", 0, "x"), "Outros: panfleto")


class TestSegurancaLeva02(Base):
    def ev(self, dados, cab=CEL):
        return self.c.post("/api/ev", headers=cab, content=json.dumps(dados))

    def test_nome_e_preco_vem_do_catalogo(self):
        self.ev({"t": "add", "v": V1, "s": S1, "i": "khamrah", "n": "<b>Golpe</b>", "z": "10", "q": 1, "p": 1})
        itens = [{"i": "khamrah", "n": "Falso", "z": "10", "q": 1, "p": 99_999_999}]
        self.ev({"t": "pedido", "v": V1, "s": S1, "itens": itens})
        d = metricas.relatorio(7)
        self.assertEqual(d["top_adicionados"][0], {"nome": "Khamrah", "n": 1})
        self.assertEqual(d["valor_pedidos"], 4490)
        self.assertEqual(d["top_pedidos"][0]["nome"], "Khamrah")

    def test_perfume_fora_do_catalogo_nao_conta(self):
        self.assertEqual(self.ev({"t": "ver", "v": V1, "s": S1, "i": "inventado"}).status_code, 400)
        self.assertEqual(self.ev({"t": "add", "v": V1, "s": S1, "i": "khamrah", "z": "frasco", "q": 1}).status_code, 400)
        self.assertEqual(self.ev({"t": "pedido", "v": V1, "s": S1, "itens": [{"i": "inventado", "z": "5", "q": 1}]}).status_code, 400)

    def test_teto_por_dia(self):
        antigo_max = metricas.MAX_POR_DIA
        try:
            metricas.MAX_POR_DIA = 3
            metricas._cont.update(dia=None, total=None)
            codigos = [self.ev({"t": "visita", "v": V1, "s": S1}).status_code for _ in range(5)]
            self.assertEqual(codigos, [204] * 5, "passar do teto não dá erro para o visitante")
            self.assertEqual(metricas.relatorio(1)["visitas"], 3)
        finally:
            metricas.MAX_POR_DIA = antigo_max
            metricas._cont.update(dia=None, total=None)

    def test_cabecalho_do_cloudflare_falso_nao_engana(self):
        class R:
            client = None
            def __init__(self, h):
                self.headers = h
        self.assertEqual(seguranca.ip_do_cliente(R({"cf-connecting-ip": "1.1.1.1", "x-forwarded-for": "9.9.9.9, 8.8.8.8"})), "8.8.8.8")
        self.assertEqual(seguranca.ip_do_cliente(R({"cf-connecting-ip": "1.1.1.1"})), "1.1.1.1")
        os.environ["ATRAS_DO_CLOUDFLARE"] = "1"
        try:
            self.assertEqual(seguranca.ip_do_cliente(R({"cf-connecting-ip": "1.1.1.1", "x-forwarded-for": "8.8.8.8"})), "1.1.1.1")
        finally:
            del os.environ["ATRAS_DO_CLOUDFLARE"]

    def test_muitos_enderecos_nao_zeram_o_limite(self):
        main._limite_ev.clear()
        agora = time.time()
        for n in range(5001):
            main._limite_ev[f"10.0.{n // 250}.{n % 250}"] = [agora]
        main._limite_ev["200.1.1.1"] = [agora] * main.MAX_EV_MIN
        self.assertTrue(main._muitos_eventos("200.1.1.1"), "o endereço que já passou do limite continua bloqueado")
        self.assertTrue(main._muitos_eventos("200.2.2.2"), "com a lista cheia, endereço novo espera")
        main._limite_ev.clear()

    def test_utm_so_com_letras_simples(self):
        self.ev({"t": "visita", "v": V1, "s": S1, "us": "<script>Panfleto!</script>"})
        origens = [o["nome"] for o in metricas.relatorio(1)["origens"]]
        self.assertEqual(origens, ["Outros: scriptpanfletoscript"])

    def test_migracao_guarda_texto_sem_desconto_e_rascunho(self):
        c = antigo()
        c["texts"]["d.item2"] = "Preço justo, sem desconto escondido"
        rascunho = json.loads(json.dumps(c))
        rascunho["texts"]["c.p1"] = "Texto só do rascunho"
        banco.gravar("site", "publicado", {"content": c, "publishedAt": "2026-11-16T21:01:43Z"})
        banco.gravar("rascunho", "atual", {"content": rascunho, "savedAt": "2026-11-16T22:00:00Z"})
        with banco._db() as con:
            con.execute("DELETE FROM config WHERE chave='migracao_leva02'")
        banco.migrar_leva02()
        self.assertEqual(banco.ler("site", "publicado")["content"]["texts"]["d.item2"], "Preço justo, sem desconto escondido")
        copia = banco.ler("historico", "antes-da-leva-02-rascunho")
        self.assertEqual(copia["content"]["texts"]["c.p1"], "Texto só do rascunho")

    def test_copia_da_leva_nao_some_com_historico_cheio(self):
        banco.gravar("historico", "antes-da-leva-02", {"at": "2000-01-01T00:00:00Z", "content": antigo()})
        self.c.post("/entrar", data={"email": EMAIL, "senha": SENHA})
        for n in range(55):
            r = self.c.put(f"/api/db/historico/v{n:03d}", headers=H, json={"at": f"2026-11-17T10:{n:02d}:00Z", "content": {}})
            self.assertEqual(r.status_code, 200)
        restantes = [d["id"] for d in banco.listar("historico", "at", desc=True, limite=1000)]
        self.assertIn("antes-da-leva-02", restantes)
        self.assertEqual(len(restantes), 51, "50 versões normais + a cópia de segurança da leva")

    def test_textos_sem_a_palavra_anonimas(self):
        html = self.c.get("/").text
        self.assertIn("sem guardar nome, telefone ou endereço IP", html)
        self.assertIn("cx_v_t", html, "o código do visitante é renovado a cada 90 dias")


if __name__ == "__main__":
    unittest.main()
