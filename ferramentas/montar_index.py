"""Monta static/index.html a partir de fonte/pagina.html (a mesma página da prévia)
e de fonte/servidor.js (o adaptador do servidor). Rode depois de mudar a fonte:

    python3 ferramentas/montar_index.py
"""
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
MARCA = '<svg width="0" height="0"'
ICONE = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Crect width='100' height='100' rx='22' fill='%230A0B0D'/%3E"
         "%3Cpath fill='%23fff' d='M79 16 L40 16 C30 16 26 20 24 29 L15 70 C13 80 17 84 27 84 L76 84 L79 70 L38 70 C35 70 34 68 35 65 L41 35 C42 31 44 30 47 30 L76 30 Z'/%3E"
         "%3Ccircle cx='84' cy='18' r='12' fill='%231D9BF0'/%3E%3C/svg%3E")
RESET = (":root{color-scheme:light;padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}"
         "body{margin:0;font:14px system-ui,-apple-system,sans-serif}img{max-width:100%}[hidden]{display:none!important}")


def montar() -> str:
    fonte = (RAIZ / "fonte" / "pagina.html").read_text(encoding="utf-8")
    adaptador = (RAIZ / "fonte" / "servidor.js").read_text(encoding="utf-8")
    corte = fonte.index(MARCA)
    cabeca, corpo = fonte[:corte], fonte[corte:]
    cabeca = cabeca.replace("<title>Cartaxo Store Decants</title>", "<title>Cartaxo Store · Decants e perfumes</title>", 1)
    return (
        '<!doctype html>\n<html lang="pt-BR">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">\n'
        '<meta name="description" content="Decants de perfumes árabes, de nicho e de grife em 5, 10 e 15 ml, e frascos inteiros. Monte seu pedido e finalize pelo WhatsApp.">\n'
        '<meta name="theme-color" content="#0A0B0D">\n'
        f'<link rel="icon" href="{ICONE}">\n'
        f"<style>{RESET}</style>\n"
        + cabeca
        + "<script>\n" + adaptador + "</script>\n<!--CARTAXO_INICIAL-->\n"
        + "</head>\n<body>\n" + corpo.rstrip() + "\n</body>\n</html>\n"
    )


if __name__ == "__main__":
    (RAIZ / "static" / "index.html").write_text(montar(), encoding="utf-8")
    print("static/index.html montado")
