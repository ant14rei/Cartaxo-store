"""Banco de dados (SQLite) da Cartaxo Store.

Guarda o conteúdo do site (publicado, rascunho e histórico), o registro das fotos
e configurações internas. O arquivo fica na pasta de dados (volume do Railway).
"""
import json
import os
import shutil
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
SEMENTE = RAIZ / "seed"
TIPOS_POR_EXT = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}

_trava = threading.Lock()


def pasta_dados() -> Path:
    p = os.environ.get("DATA_DIR")
    if p:
        return Path(p)
    if Path("/data").is_dir():
        return Path("/data")
    return RAIZ / "dados"


def pasta_fotos() -> Path:
    return pasta_dados() / "fotos"


def agora() -> str:
    return datetime.now(timezone.utc).isoformat()


def conectar() -> sqlite3.Connection:
    con = sqlite3.connect(pasta_dados() / "cartaxo.db", timeout=10, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


@contextmanager
def _db():
    """Abre, grava e fecha a conexão a cada uso."""
    con = conectar()
    try:
        with con:
            yield con
    finally:
        con.close()


def preparar() -> None:
    """Cria as tabelas e, se o banco estiver vazio, carrega o conteúdo inicial."""
    pasta_dados().mkdir(parents=True, exist_ok=True)
    pasta_fotos().mkdir(parents=True, exist_ok=True)
    with _trava, _db() as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("CREATE TABLE IF NOT EXISTS docs (col TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL, atualizado TEXT NOT NULL, PRIMARY KEY (col, id))")
        con.execute("CREATE TABLE IF NOT EXISTS fotos (id TEXT PRIMARY KEY, tipo TEXT NOT NULL, bytes INTEGER NOT NULL, criado TEXT NOT NULL)")
        con.execute("CREATE TABLE IF NOT EXISTS config (chave TEXT PRIMARY KEY, valor TEXT NOT NULL)")
        vazio = con.execute("SELECT 1 FROM docs WHERE col='site' AND id='publicado'").fetchone() is None
    if vazio:
        carregar_semente()


def carregar_semente() -> None:
    arq = SEMENTE / "conteudo.json"
    if not arq.exists():
        return
    dados = json.loads(arq.read_text(encoding="utf-8"))
    for f in sorted((SEMENTE / "fotos").glob("*")):
        tipo = TIPOS_POR_EXT.get(f.suffix.lower())
        if not tipo or len(f.stem) != 32:
            continue
        destino = pasta_fotos() / f.stem
        if not destino.exists():
            shutil.copyfile(f, destino)
        registrar_foto(f.stem, tipo, destino.stat().st_size)
    gravar("site", "publicado", dados)
    gravar("rascunho", "atual", {"content": dados["content"], "savedAt": dados.get("publishedAt") or agora()})


def ler(col: str, id_: str):
    with _db() as con:
        r = con.execute("SELECT data FROM docs WHERE col=? AND id=?", (col, id_)).fetchone()
    return json.loads(r["data"]) if r else None


def gravar(col: str, id_: str, data: dict) -> None:
    texto = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    with _trava, _db() as con:
        con.execute("INSERT INTO docs (col, id, data, atualizado) VALUES (?,?,?,?) ON CONFLICT(col, id) DO UPDATE SET data=excluded.data, atualizado=excluded.atualizado", (col, id_, texto, agora()))


def apagar(col: str, id_: str) -> bool:
    with _trava, _db() as con:
        cur = con.execute("DELETE FROM docs WHERE col=? AND id=?", (col, id_))
        return cur.rowcount > 0


def listar(col: str, campo: str = "", desc: bool = False, limite: int = 100) -> list:
    with _db() as con:
        rows = con.execute("SELECT id, data FROM docs WHERE col=?", (col,)).fetchall()
    docs = [{"id": r["id"], "data": json.loads(r["data"])} for r in rows]
    if campo:
        docs.sort(key=lambda d: str(d["data"].get(campo, "")), reverse=desc)
    return docs[: max(1, min(limite, 1000))]


def registrar_foto(id_: str, tipo: str, tamanho: int) -> None:
    with _trava, _db() as con:
        con.execute("INSERT OR REPLACE INTO fotos (id, tipo, bytes, criado) VALUES (?,?,?,?)", (id_, tipo, tamanho, agora()))


def foto(id_: str):
    with _db() as con:
        r = con.execute("SELECT id, tipo, bytes FROM fotos WHERE id=?", (id_,)).fetchone()
    return dict(r) if r else None


def espaco_fotos() -> int:
    with _db() as con:
        r = con.execute("SELECT COALESCE(SUM(bytes), 0) AS t FROM fotos").fetchone()
    return int(r["t"])


def config(chave: str):
    with _db() as con:
        r = con.execute("SELECT valor FROM config WHERE chave=?", (chave,)).fetchone()
    return r["valor"] if r else None


def definir_config(chave: str, valor: str) -> None:
    with _trava, _db() as con:
        con.execute("INSERT OR REPLACE INTO config (chave, valor) VALUES (?,?)", (chave, valor))


def copia_do_banco(destino: Path) -> None:
    """Cópia consistente do banco, mesmo com o site no ar."""
    with _db() as origem:
        alvo = sqlite3.connect(destino)
        try:
            origem.backup(alvo)
            # A cópia não leva o segredo das sessões nem outras configurações internas.
            alvo.execute("DELETE FROM config")
            alvo.commit()
            alvo.execute("VACUUM")
        finally:
            alvo.close()
