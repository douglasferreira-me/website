# Douglas Ferreira website

Site pessoal em Hugo publicado pelo GitHub Pages em `https://douglasferreira.me/`.

## Desenvolvimento local

Requisitos:

- Hugo Extended `0.152.2` ou compatível.
- Python 3 para as validações locais.

Comandos úteis:

```bash
hugo server
hugo --minify --printPathWarnings --destination /tmp/website-build --cleanDestinationDir
python3 scripts/validate_site.py --site-dir /tmp/website-build
```

O diretório `public/` existe no repositório, mas para testes locais prefira gerar o build em `/tmp/...` para evitar mudanças acidentais em arquivos publicados.

## Criação de posts

Crie posts dentro de `content/en/blog/` ou `content/pt/blog/`. O arquétipo padrão inclui:

```yaml
---
title: ""
date:
draft: true
description: ""
tags: []
categories: []
lang: "pt-BR"
federate: false
social_text: ""
image: ""
---
```

Campos principais:

- `draft`: mantenha `true` enquanto o texto não deve ser publicado.
- `description`: resumo usado por metadados, Microformats2 e RSS.
- `tags`: use `newsletter` para incluir o post no RSS mensal.
- `categories`: categorias públicas do site.
- `lang`: idioma do artigo, por exemplo `pt-BR` ou `en`.
- `post_kind`: use `blogpost` para artigo com link social; use `micropost` para nota curta de até 300 caracteres.
- `federate`: use `true` somente em posts públicos que devem ser enviados via Bridgy Fed.
- `syndicate_bluesky`, `syndicate_mastodon`, `syndicate_linkedin`: republicam o post pelas APIs diretas quando os segredos estiverem configurados.
- `social_intro`: texto opcional antes do resumo/link em blogposts.
- `social_text`: texto curto opcional para divulgação/federação.
- `image`: imagem destacada opcional quando não houver recurso de página.
- `show_image_in_photos`: quando `true`, a imagem do post também aparece na grade de fotografias.
- `photo_caption`: legenda usada na grade de fotografias quando o post envia imagem para lá.

## Sveltia CMS

O painel de edição fica em:

```text
https://douglasferreira.me/admin/
```

O site usa Sveltia CMS via CDN, sem dependências npm. A configuração está em `static/admin/config.yml` e usa o backend GitHub:

- repositório: `douglasferreira-me/website`;
- branch: `main`;
- mídia enviada pelo CMS: `static/uploads`;
- URL pública da mídia: `/uploads`.

Para entrar:

1. Abra `https://douglasferreira.me/admin/`.
2. Clique em “Sign In with Token”.
3. Gere um Personal Access Token no GitHub com permissão de escrita para este repositório.
4. Cole o token no navegador.

O token fica salvo no armazenamento local do navegador. Não coloque tokens, senhas ou chaves em arquivos do repositório.

No CMS é possível editar:

- posts em inglês em `content/en/blog`;
- posts em português em `content/pt/blog`;
- microposts em inglês e português;
- livros em `content/en/books`;
- fotografias em `content/en/photos`;
- páginas `About` em inglês e português;
- página pública da newsletter.

Ao criar posts pelo CMS:

- mantenha `draft` ligado até o texto estar pronto;
- use a tag `newsletter` para incluir o post no RSS mensal;
- deixe `federate` ligado para federar o post público via Bridgy Fed;
- use `post_kind = "micropost"` apenas quando o texto limpo tiver até 300 caracteres;
- ligue `syndicate_bluesky`, `syndicate_mastodon` ou `syndicate_linkedin` para republicar via API direta;
- use `social_intro` e `social_text` para controlar o texto que acompanha links de blogposts;
- desligue `federate` quando o post não deve ser enviado ao Bridgy Fed.

Uma etapa futura opcional é trocar o login por token por OAuth usando o Sveltia CMS Authenticator ou outro cliente OAuth compatível. Isso exige configurar app/cliente externo e credenciais fora deste repositório.

## IndieWeb, Webmentions e Bridgy Fed

O site inclui marcação Microformats2 com `h-card`, `h-feed` e `h-entry`. O `<head>` publica endpoints do Webmention.io:

- `https://webmention.io/douglasferreira.me/webmention`
- `https://webmention.io/douglasferreira.me/xmlrpc`

Para ativar Webmentions, cadastre `douglasferreira.me` no Webmention.io usando um dos links `rel="me"` do site. A exibição no frontend consulta apenas dados públicos e não usa segredos.

Para federar com Bridgy Fed:

1. Cadastre `https://douglasferreira.me/` em `https://fed.brid.gy/`.
2. Verifique a identidade usando os links `rel="me"`.
3. Localize a conta do site no Mastodon/Fediverso após a ativação.
4. Conecte ou represente o site no Bluesky seguindo a documentação do Bridgy Fed.
5. Publique um post com `federate: true`.
6. Envie a primeira Webmention do post para `https://fed.brid.gy/`.

Apenas posts públicos com `federate: true` incluem `u-bridgy-fed`. Páginas institucionais, arquivos, categorias e rascunhos não são federados.

Para desativar a federação de um post, mude `federate` para `false`. Para desativar a exibição de Webmentions, remova o partial `webmentions.html` do template `single.html` ou o script em `extend-head.html`.

## Publicação social direta

Além do Bridgy Fed, o repositório tem automação direta por GitHub Actions em `.github/workflows/social.yml`.

Segredos necessários:

- `BLUESKY_HANDLE`
- `BLUESKY_APP_PASSWORD`
- `BLUESKY_PDS`, opcional; usa `https://bsky.social` quando não existir.
- `MASTODON_INSTANCE`
- `MASTODON_ACCESS_TOKEN`
- `LINKEDIN_ACCESS_TOKEN`
- `LINKEDIN_AUTHOR_URN`
- `LINKEDIN_VERSION`, opcional; usa `202606` quando não existir.

O script `scripts/social_publish.py` publica apenas posts públicos com algum `syndicate_* = true`. Ele grava os links em `data/social/syndication.json` para evitar republicações duplicadas e para que o site mostre links `u-syndication`.

Microposts são enviados como texto direto. Blogposts são enviados como texto + URL canônica. Se um micropost público passar de 300 caracteres, a validação falha.

O script `scripts/social_collect_comments.py` coleta respostas públicas de Mastodon e Bluesky dos posts já sindicados e grava essas respostas no mesmo estado JSON. O site renderiza essas conversas abaixo de cada post, junto com Webmentions. Não há login embutido no site nesta etapa.

Para testar sem postar:

```bash
python3 scripts/social_publish.py --dry-run
python3 scripts/social_collect_comments.py --dry-run
```

## Tradução automática

O workflow `.github/workflows/translate.yml` traduz posts publicados em português para inglês usando a OpenAI Responses API.

Segredos necessários:

- `OPENAI_API_KEY`
- `OPENAI_MODEL`, opcional; usa `gpt-5.6` quando não existir.

Quando um post PT publicado muda, o script `scripts/translate_pt_to_en.py` cria ou atualiza um arquivo em `content/en/blog` ou `content/en/microposts` com:

- `auto_translated = true`;
- `translation_of` apontando para a URL original;
- `draft = false`;
- `federate = false`;
- `syndicate_bluesky = false`;
- `syndicate_mastodon = false`;
- `syndicate_linkedin = false`.

Se já existir um arquivo em inglês que não tenha `auto_translated = true`, o script não sobrescreve. A tradução automática faz commit direto em `main` com marcadores para evitar loops de workflow.

## Livros, fotografias, microposts e updates

Novas áreas públicas:

- `https://douglasferreira.me/microposts/`
- `https://douglasferreira.me/updates/`
- `https://douglasferreira.me/books/`
- `https://douglasferreira.me/photos/`

Livros são cadastrados no CMS com título, autor, capa, status, datas, nota e texto livre. Fotografias são cadastradas com imagem, legenda, tags e opção de aparecer no feed geral.

Posts de blog também podem aparecer na grade de fotos quando `show_image_in_photos = true` e `image` estiver preenchido. O feed `/updates/` reúne blogposts, microposts, fotos e livros em ordem cronológica.

## Newsletter e MailerLite

A página pública da newsletter fica em:

```text
https://douglasferreira.me/newsletter/
```

O RSS exclusivo para campanha mensal fica em:

```text
https://douglasferreira.me/newsletter/index.xml
```

Somente posts publicados com a tag exata `newsletter` entram nesse feed. Rascunhos não entram.

Configuração sugerida no MailerLite:

1. Crie uma campanha do tipo RSS.
2. Use `https://douglasferreira.me/newsletter/index.xml`.
3. Escolha frequência mensal.
4. Configure envio apenas quando houver novos itens.
5. Defina remetente, assunto e grupo de assinantes.
6. Monte um template com imagem, título, resumo, data e botão “Ler no site”.
7. Teste a campanha antes de ativar.

Não há API do MailerLite neste repositório e nenhuma credencial deve ser armazenada aqui. O formulário de inscrição deve ser colado futuramente em `layouts/_partials/newsletter/mailer-form.html`.

Para remover um post do resumo mensal, retire a tag `newsletter`.

## Publicação

O deploy roda pelo workflow `.github/workflows/gh-pages.yml` em pushes para `main`. O workflow:

1. instala Hugo Extended `0.152.2`;
2. executa o build com `hugo --minify --printPathWarnings`;
3. roda `python3 scripts/validate_site.py --site-dir public`;
4. roda `python3 scripts/social_publish.py --dry-run`;
5. publica `public/` na branch `gh-pages`.

Não faça commit ou push sem revisar o build e as mudanças locais.

## Limitações conhecidas

- Webmention.io, Bridgy Fed e MailerLite exigem configuração manual nas respectivas contas.
- Webmentions são carregadas no navegador; uma versão futura pode baixá-las durante o build e renderizar HTML estático.
- `/newsletter/` é a superfície canônica única da newsletter nesta etapa, sem versão separada por idioma.
- LinkedIn pode exigir aprovação e permissões específicas do app para publicar em perfil pessoal.
- Comentários sociais são coletados de respostas públicas; não há comentário privado nem login OAuth dentro do site.
