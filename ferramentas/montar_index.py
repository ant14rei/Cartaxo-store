"""Monta static/index.html a partir de fonte/pagina.html (a mesma página da prévia)
e de fonte/servidor.js (o adaptador do servidor). Rode depois de mudar a fonte:

    python3 ferramentas/montar_index.py
"""
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
MARCA = '<svg width="0" height="0"'
ICONES = ('<link rel="icon" href="/favicon.ico" sizes="any">\n'
          '<link rel="icon" href="/favicon.svg" type="image/svg+xml">\n'
          '<link rel="apple-touch-icon" href="/apple-touch-icon.png">\n'
          '<link rel="manifest" href="/site.webmanifest">\n'
          '<meta name="apple-mobile-web-app-title" content="Cartaxo Store">\n')
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
        + ICONES
        + f"<style>{RESET}</style>\n"
        + cabeca
        + "<script>\n" + adaptador + "</script>\n<!--CARTAXO_INICIAL-->\n"
        + "</head>\n<body>\n" + corpo.rstrip() + "\n</body>\n</html>\n"
    )


if __name__ == "__main__":
    (RAIZ / "static" / "index.html").write_text(montar(), encoding="utf-8")
    print("static/index.html montado")
