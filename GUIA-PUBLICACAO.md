# Guia de publicação – cartaxostore.com.br

Siga na ordem. O Claude faz as partes marcadas com **(Claude)**; o Antonio faz as marcadas com **(Antonio)**.

## 1. Código no GitHub (Antonio, pela aba Code)
1. Crie um repositório **privado** no GitHub chamado `cartaxo-store` (pode pedir ao Claude Code).
2. Use o texto padrão da leva para descompactar o zip, rodar os testes e fazer o push na `main`.

## 2. Projeto no Railway (Claude)
1. Projeto novo `cartaxo-store`, com um serviço ligado ao repositório `cartaxo-store` (branch `main`).
2. Volume montado em `/data` (guarda o banco e as fotos).
3. Domínio provisório `*.up.railway.app` para o primeiro teste.
4. Variável `ADMIN_EMAIL` = `cartaxostore@outlook.com`.

## 3. Senha do painel (Antonio, direto no Railway)
1. Railway → projeto `cartaxo-store` → serviço → **Variables** → **New Variable**.
2. Nome: `ADMIN_SENHA`. Valor: uma senha forte, com no mínimo 12 caracteres, que não seja usada em outro lugar.
3. Salve. O serviço reinicia sozinho. **Não mande a senha no chat.**

## 4. Plano do Railway (Antonio)
- Use o plano **Hobby** (pago). O plano de teste vence e o site sai do ar, e ele só aceita 1 domínio; o site usa 2 (com e sem www).
- Confira a data de renovação e coloque um limite de gastos.

## 5. Cloudflare grátis (Antonio, com o passo a passo do Claude)
1. Crie uma conta em cloudflare.com → **Add a domain** → `cartaxostore.com.br` → plano **Free**.
2. O Cloudflare mostra **2 servidores DNS** (algo como `nome.ns.cloudflare.com`). Anote os dois.
3. No **registro.br**: entre → domínio `cartaxostore.com.br` → **DNS** → **Alterar servidores DNS** → cole os dois servidores do Cloudflare → salve.
4. Aguarde o Cloudflare mostrar o domínio como **Active** (de minutos a algumas horas).

## 6. Domínio no Railway e registros no Cloudflare (Claude cria no Railway; Antonio copia no Cloudflare)
1. **(Claude)** adiciona os domínios `cartaxostore.com.br` e `www.cartaxostore.com.br` no Railway. O Railway gera um `CNAME` e um `TXT` para cada um. O Claude passa os valores exatos.
2. **(Antonio)** no Cloudflare → **DNS** → **Records**:
   - `CNAME` | Nome `@` | Destino: o valor do Railway | **Proxy ligado** (nuvem laranja).
   - `TXT` | Nome e valor exatamente como o Railway mostrar (para o domínio principal).
   - `CNAME` | Nome `www` | Destino `@` | **Proxy ligado**.
   - `TXT` do `www`, se o Railway pedir.
3. **(Antonio)** no Cloudflare → **SSL/TLS** → **Overview** → **Full** (não use "Full (Strict)").
4. **(Antonio)** no Cloudflare → **SSL/TLS** → **Edge Certificates** → **Universal SSL** ligado.
5. **(Antonio)** opcional: em **Bulk Redirects**, criar o redirecionamento de `https://www.cartaxostore.com.br` para `https://cartaxostore.com.br` (301).

## 7. Conferência final (Claude)
- O Railway mostra **SUCCESS**, "Banco pronto" nos registros e nenhum Traceback.
- `https://cartaxostore.com.br` abre com o cadeado, o pedido abre o WhatsApp certo e o painel entra em `/entrar`.
- Teste em celular e computador, nos modos claro e escuro.

## Depois de publicado
- **Cópia de segurança:** Painel → Contato e logo → **Baixar cópia de segurança**. Faça toda semana e guarde fora do Railway (computador ou Google Drive).
- **Trocar a senha:** troque `ADMIN_SENHA` no Railway; todas as sessões abertas caem na hora.
