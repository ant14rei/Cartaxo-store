"""Testes do servidor da Cartaxo Store. Rode: python3 -m unittest discover -s tests -q

Usam só dados de teste: e-mail e senha falsos e uma pasta temporária.
Nunca tocam no site no ar.
"""
import io
import json
import os
import struct
import sys
import tempfile
import time
import unittest
import zipfile
import zlib
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

EMAIL = "teste@exemplo.com"
SENHA = "senha-de-teste-123"
os.environ["ADMIN_EMAIL"] = EMAIL
os.environ["ADMIN_SENHA"] = SENHA

from starlette.testclient import TestClient  # noqa: E402

from app import main, seguranca  # noqa: E402

H = {"X-Cartaxo": "1"}


def png_pequeno() -> bytes:
    def bloco(tipo, dados):
        return struct.pack(">I", len(dados)) + tipo + dados + struct.pack(">I", zlib.crc32(tipo + dados) & 0xFFFFFFFF)
    cru = b"\x00\xff\xff\xff"
    return b"\x89PNG\r\n\x1a\n" + bloco(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) + bloco(b"IDAT", zlib.compress(cru)) + bloco(b"IEND", b"")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["DATA_DIR"] = self.tmp.name
        os.environ["ADMIN_EMAIL"] = EMAIL
        os.environ["ADMIN_SENHA"] = SENHA
        seguranca.zerar_bloqueios()
        main._paginas.clear()
        self.c = TestClient(main.app)
        self.c.__enter__()

    def tearDown(self):
        self.c.__exit__(None, None, None)
        self.tmp.cleanup()

    def entrar(self, email=EMAIL, senha=SENHA):
        return self.c.post("/entrar", data={"email": email, "senha": senha}, follow_redirects=False)

    def conteudo(self):
        return json.loads((RAIZ / "seed" / "conteudo.json").read_text(encoding="utf-8"))["content"]


class TestPaginas(Base):
    def test_inicio_com_conteudo_publicado_e_protecoes(self):
        r = self.c.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Cartaxo", r.text)
        self.assertIn("window.CARTAXO_INICIAL={", r.text)
        self.assertIn("5583981857161", r.text)
        self.assertEqual(r.headers["x-content-type-options"], "nosniff")
        self.assertEqual(r.headers["x-frame-options"], "DENY")
        self.assertIn("frame-ancestors 'none'", r.headers["content-security-policy"])

    def test_texto_com_codigo_nao_quebra_a_pagina(self):
        self.entrar()
        c = self.conteudo()
        c["texts"]["g.aviso"] = "</script><script>alert(1)</script>"
        self.assertEqual(self.c.put("/api/db/site/publicado", headers=H, json={"content": c, "publishedAt": "x"}).status_code, 200)
        r = self.c.get("/")
        self.assertNotIn("</script><script>alert(1)", r.text)
        self.assertIn("\\u003c/script\\u003e", r.text)

    def test_saude(self):
        self.assertEqual(self.c.get("/saude").json(), {"ok": True})

    def test_index_montado_em_dia_com_a_fonte(self):
        sys.path.insert(0, str(RAIZ / "ferramentas"))
        import montar_index
        self.assertEqual(montar_index.montar(), (RAIZ / "static" / "index.html").read_text(encoding="utf-8"),
                         "Rode python3 ferramentas/montar_index.py depois de mudar a fonte.")


class TestLogin(Base):
    def test_login_certo_abre_o_painel(self):
        r = self.entrar()
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["location"], "/#admin")
        cookie = r.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=strict", cookie)
        self.assertTrue(self.c.get("/api/eu").json()["admin"])

    def test_email_com_maiusculas_funciona(self):
        self.assertEqual(self.entrar(email="  TESTE@Exemplo.com ").status_code, 303)

    def test_senha_errada(self):
        r = self.entrar(senha="errada-123456")
        self.assertEqual(r.status_code, 401)
        self.assertIn("incorretos", r.text)
        self.assertFalse(self.c.get("/api/eu").json()["admin"])

    def test_bloqueio_depois_de_5_erros(self):
        for _ in range(5):
            self.entrar(senha="errada-123456")
        r = self.entrar()
        self.assertEqual(r.status_code, 429)
        self.assertFalse(self.c.get("/api/eu").json()["admin"])

    def test_muitos_erros_de_varios_enderecos_deixam_lento_sem_trancar_o_dono(self):
        for i in range(21):
            self.c.post("/entrar", data={"email": EMAIL, "senha": "x" * 9}, headers={"CF-Connecting-IP": f"10.0.0.{i}"})
        self.assertGreater(seguranca.atraso(), 0)
        seguranca.MUITOS_ERROS, antigo = 1000, seguranca.MUITOS_ERROS   # não esperar de verdade no teste
        try:
            self.assertEqual(self.entrar().status_code, 303)
        finally:
            seguranca.MUITOS_ERROS = antigo

    def test_x_real_ip_falso_nao_engana_o_bloqueio(self):
        for i in range(5):
            self.c.post("/entrar", data={"email": EMAIL, "senha": "x" * 9}, headers={"X-Real-IP": f"9.9.9.{i}", "X-Forwarded-For": f"8.8.8.{i}, 200.9.9.9"})
        r = self.c.post("/entrar", data={"email": EMAIL, "senha": SENHA}, headers={"X-Real-IP": "1.1.1.1", "X-Forwarded-For": "7.7.7.7, 200.9.9.9"}, follow_redirects=False)
        self.assertEqual(r.status_code, 429)

    def test_sair_derruba_copias_do_cookie(self):
        self.entrar()
        cookie = self.c.cookies.get(seguranca.COOKIE)
        self.c.post("/sair")
        self.c.cookies.set(seguranca.COOKIE, cookie)
        self.assertFalse(self.c.get("/api/eu").json()["admin"])

    def test_bloqueio_usa_o_endereco_real_atras_do_cloudflare(self):
        for _ in range(5):
            self.c.post("/entrar", data={"email": EMAIL, "senha": "x" * 9}, headers={"CF-Connecting-IP": "200.1.1.1", "X-Real-IP": "172.68.0.1"})
        self.assertEqual(self.c.post("/entrar", data={"email": EMAIL, "senha": SENHA}, headers={"CF-Connecting-IP": "200.1.1.1"}, follow_redirects=False).status_code, 429)
        self.assertEqual(self.c.post("/entrar", data={"email": EMAIL, "senha": SENHA}, headers={"CF-Connecting-IP": "200.2.2.2"}, follow_redirects=False).status_code, 303)

    def test_sem_configuracao_nao_entra(self):
        del os.environ["ADMIN_SENHA"]
        r = self.c.get("/entrar")
        self.assertIn("ainda não foi configurado", r.text)
        self.assertEqual(self.entrar().status_code, 401)

    def test_senha_curta_no_railway_nao_vale(self):
        os.environ["ADMIN_SENHA"] = "1234567"
        self.assertEqual(self.c.post("/entrar", data={"email": EMAIL, "senha": "1234567"}, follow_redirects=False).status_code, 401)

    def test_trocar_a_senha_derruba_a_sessao(self):
        self.entrar()
        self.assertTrue(self.c.get("/api/eu").json()["admin"])
        os.environ["ADMIN_SENHA"] = "outra-senha-de-teste"
        self.assertFalse(self.c.get("/api/eu").json()["admin"])

    def test_token_adulterado_e_vencido(self):
        self.assertFalse(seguranca.token_valido("abc.def"))
        t = seguranca.criar_token()
        corpo, ass = t.split(".")
        self.assertFalse(seguranca.token_valido(corpo + "x." + ass))
        vencido = seguranca._b64(json.dumps({"e": int(time.time()) - 5, "n": "a"}).encode())
        import hashlib
        import hmac
        assinatura = seguranca._b64(hmac.new(seguranca._segredo(), vencido.encode(), hashlib.sha256).digest())
        self.assertFalse(seguranca.token_valido(vencido + "." + assinatura))

    def test_login_vindo_de_outro_site_e_recusado(self):
        r = self.c.post("/entrar", data={"email": EMAIL, "senha": SENHA}, headers={"Origin": "https://site-falso.com"}, follow_redirects=False)
        self.assertEqual(r.status_code, 403)

    def test_sair(self):
        self.entrar()
        r = self.c.post("/sair", follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertFalse(self.c.get("/api/eu").json()["admin"])


class TestConteudo(Base):
    def test_visitante_le_so_o_publicado(self):
        r = self.c.get("/api/db/site/publicado")
        self.assertEqual(r.status_code, 200)
        self.assertIn("decants", r.json()["data"]["content"])
        self.assertEqual(self.c.get("/api/db/rascunho/atual").status_code, 401)
        self.assertEqual(self.c.get("/api/db/historico").status_code, 401)

    def test_visitante_nao_grava(self):
        r = self.c.put("/api/db/site/publicado", headers=H, json={"content": {}})
        self.assertEqual(r.status_code, 401)

    def test_caminhos_desconhecidos(self):
        self.entrar()
        for url in ("/api/db/site/outro", "/api/db/config/segredo_sessao", "/api/db/historico/../x", "/api/db/docs"):
            self.assertIn(self.c.get(url).status_code, (404,), url)

    def test_pedido_sem_marca_ou_de_outro_site_e_recusado(self):
        self.entrar()
        corpo = {"content": self.conteudo(), "savedAt": "x"}
        self.assertEqual(self.c.put("/api/db/rascunho/atual", json=corpo).status_code, 403)
        self.assertEqual(self.c.put("/api/db/rascunho/atual", json=corpo, headers={**H, "Origin": "https://site-falso.com"}).status_code, 403)

    def test_rascunho_publicar_e_historico(self):
        self.entrar()
        c = self.conteudo()
        c["texts"]["g.aviso"] = "Nova chamada"
        self.assertEqual(self.c.put("/api/db/rascunho/atual", headers=H, json={"content": c, "savedAt": "2026-11-03T20:00:00Z"}).status_code, 200)
        self.assertEqual(self.c.get("/api/db/rascunho/atual").json()["data"]["content"]["texts"]["g.aviso"], "Nova chamada")
        antigo = self.c.get("/api/db/site/publicado").json()["data"]
        self.assertEqual(self.c.put("/api/db/historico/v1", headers=H, json={"at": "2026-11-03T19:00:00Z", "content": antigo["content"]}).status_code, 200)
        self.assertEqual(self.c.put("/api/db/site/publicado", headers=H, json={"content": c, "publishedAt": "2026-11-03T20:01:00Z"}).status_code, 200)
        self.assertIn("Nova chamada", self.c.get("/").text)
        lista = self.c.get("/api/db/historico?ordem=at&dir=desc&limite=20").json()["docs"]
        self.assertEqual([d["id"] for d in lista], ["v1"])
        self.assertEqual(self.c.delete("/api/db/historico/v1", headers=H).status_code, 200)
        self.assertEqual(self.c.get("/api/db/historico/v1").status_code, 404)

    def test_nao_apaga_o_site_publicado(self):
        self.entrar()
        self.assertEqual(self.c.delete("/api/db/site/publicado", headers=H).status_code, 405)

    def test_historico_guarda_no_maximo_50(self):
        self.entrar()
        c = self.conteudo()
        for i in range(55):
            self.c.put(f"/api/db/historico/h{i:02d}", headers=H, json={"at": f"2026-11-03T10:{i:02d}:00Z", "content": c})
        docs = self.c.get("/api/db/historico?ordem=at&dir=desc&limite=1000").json()["docs"]
        self.assertEqual(len(docs), 50)
        self.assertEqual(docs[0]["id"], "h54")

    def test_conteudo_invalido_e_grande_demais(self):
        self.entrar()
        self.assertEqual(self.c.put("/api/db/rascunho/atual", headers={**H, "Content-Type": "application/json"}, content=b"{nao e json").status_code, 400)
        self.assertEqual(self.c.put("/api/db/rascunho/atual", headers=H, json={"semConteudo": 1}).status_code, 400)
        self.assertEqual(self.c.put("/api/db/historico/x", headers=H, json={"content": {}}).status_code, 400)
        grande = {"content": {"texts": {"g.aviso": "x" * (700 * 1024)}}}
        self.assertEqual(self.c.put("/api/db/rascunho/atual", headers=H, json=grande).status_code, 413)


class TestFotos(Base):
    def enviar(self, dados, nome="f.png", tipo="image/png", cab=H):
        return self.c.post("/api/fotos", headers=cab, files={"foto": (nome, dados, tipo)})

    def test_enviar_e_ver_foto(self):
        self.entrar()
        r = self.enviar(png_pequeno())
        self.assertEqual(r.status_code, 200)
        id_ = r.json()["id"]
        self.assertRegex(id_, r"^[0-9a-f]{32}$")
        v = self.c.get("/_blob/" + id_)
        self.assertEqual(v.status_code, 200)
        self.assertEqual(v.headers["content-type"], "image/png")
        self.assertEqual(v.headers["x-content-type-options"], "nosniff")
        self.assertIn("immutable", v.headers["cache-control"])

    def test_visitante_nao_envia(self):
        self.assertEqual(self.enviar(png_pequeno()).status_code, 401)

    def test_envio_sem_marca_e_recusado(self):
        self.entrar()
        self.assertEqual(self.enviar(png_pequeno(), cab={}).status_code, 403)

    def test_arquivo_que_nao_e_foto(self):
        self.entrar()
        self.assertEqual(self.enviar(b"<html><script>alert(1)</script></html>", "x.png").status_code, 415)
        self.assertEqual(self.enviar(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>", "x.svg", "image/svg+xml").status_code, 415)

    def test_cota_de_fotos(self):
        self.entrar()
        main.COTA_FOTOS, antiga = 1, main.COTA_FOTOS
        try:
            self.assertEqual(self.enviar(png_pequeno()).status_code, 507)
        finally:
            main.COTA_FOTOS = antiga

    def test_foto_grande_demais(self):
        self.entrar()
        self.assertEqual(self.enviar(b"\x89PNG\r\n\x1a\n" + b"0" * (13 * 1024 * 1024)).status_code, 413)

    def test_caminho_invalido_nao_vaza_arquivo(self):
        for url in ("/_blob/..%2Fcartaxo.db", "/_blob/cartaxo.db", "/_blob/" + "0" * 32):
            self.assertEqual(self.c.get(url).status_code, 404, url)

    def test_fotos_iniciais_disponiveis(self):
        for f in (RAIZ / "seed" / "fotos").iterdir():
            self.assertEqual(self.c.get("/_blob/" + f.stem).status_code, 200, f.name)


class TestBackup(Base):
    def test_backup_pede_login(self):
        r = self.c.get("/api/backup", follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["location"], "/entrar")

    def test_backup_tem_banco_e_fotos(self):
        self.entrar()
        r = self.c.get("/api/backup")
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r.headers["content-disposition"])
        z = zipfile.ZipFile(io.BytesIO(r.content))
        nomes = z.namelist()
        self.assertIn("cartaxo.db", nomes)
        import sqlite3
        with tempfile.TemporaryDirectory() as tmp:
            z.extract("cartaxo.db", tmp)
            con = sqlite3.connect(Path(tmp) / "cartaxo.db")
            self.assertEqual(con.execute("SELECT COUNT(*) FROM config").fetchone()[0], 0, "o segredo das sessões não pode ir no backup")
            self.assertGreater(con.execute("SELECT COUNT(*) FROM docs").fetchone()[0], 0)
            con.close()
        self.assertEqual(len([n for n in nomes if n.startswith("fotos/")]), len(list((RAIZ / "seed" / "fotos").iterdir())))


if __name__ == "__main__":
    unittest.main()
