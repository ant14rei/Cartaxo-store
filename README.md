# Cartaxo Store – site de decants e perfumes

Site da Cartaxo Store (cartaxostore.com.br) com o decant em 3D, a loja de frascos, o pedido pelo WhatsApp e o painel do dono.

## Como funciona
- **Site:** `/` mostra a página com o conteúdo publicado no painel.
- **Painel:** `/entrar` → e-mail e senha → o painel abre ao lado do site (espelho). Rascunho salvo sozinho, botão Publicar, histórico das últimas versões, fotos (até 5 por perfume da loja), cópia de segurança.
- **Dados:** SQLite e fotos na pasta `/data` (volume do Railway). Na primeira vez, o site carrega o conteúdo de `seed/`.

## Variáveis no Railway (o Antonio cadastra direto no Railway)
| Variável | Para que serve |
|---|---|
| `ADMIN_EMAIL` | E-mail de entrada no painel |
| `ADMIN_SENHA` | Senha do painel (mínimo de 8 caracteres). Trocar a senha encerra as sessões abertas |
| `DATA_DIR` | Opcional. Pasta dos dados; o padrão é `/data` |

Nunca coloque a senha no código, no chat ou no GitHub.

## Testes
```
python3 -m unittest discover -s tests -q
```

## Mudar a página
A página é montada a partir de `fonte/pagina.html` (a mesma da prévia) e `fonte/servidor.js`:
```
python3 ferramentas/montar_index.py
```
Um teste confere se `static/index.html` está em dia com a fonte.

## Rodar no computador (modo teste local)
```
pip install -r requirements.txt
ADMIN_EMAIL=teste@exemplo.com ADMIN_SENHA=senha-de-teste-123 DATA_DIR=./dados uvicorn app.main:app --port 8000
```
Abra http://127.0.0.1:8000 (use só dados de teste).
