# SPEC: protótipo HTML autocontido em Plane Pages

## Status

Rascunho técnico para aprovação. Esta SPEC precede testes e implementação.

## Objetivo

Permitir que uma spec de negócio mantida em uma Plane Page tenha um protótipo de baixa fidelidade em
HTML autocontido, diretamente associado à Page, com duas ações distintas:

- **Baixar HTML**, sempre como arquivo;
- **Abrir protótipo**, em visualizador isolado do domínio e da sessão do Plane.

O cliente deve partir da spec para experimentar o protótipo sem expor cookies, tokens, armazenamento
local ou APIs autenticadas do Plane ao conteúdo ativo enviado pelo usuário.

## Escopo técnico

- Frontend: React/Next.js e editor TipTap/ProseMirror existentes.
- Backend: Django/DRF e modelo `FileAsset` existente.
- Storage: S3 compatível por upload e leitura com URLs assinadas.
- Entidade do asset: `PAGE_DESCRIPTION`, vinculada à Page e ao projeto.
- Formato inicial: um arquivo `.html`, MIME canônico `text/html`, autocontido.
- Tamanho: `FILE_SIZE_LIMIT` da instância, sem limite paralelo específico.

## Fora de escopo

- Hospedar sites com múltiplos arquivos ou aceitar ZIP como protótipo executável.
- Resolver caminhos relativos para CSS, JavaScript, imagens ou fontes externas ao arquivo.
- Converter Figma, PDF, Markdown ou imagens em HTML.
- Editar o HTML dentro do Plane.
- Permitir acesso anônimo ou público ao protótipo.
- Executar HTML na origem do Plane, na origem do storage ou em URL assinada direta.
- Conceder ao protótipo acesso às APIs, sessão, cookies ou dados da Page.
- Preservar estado ou dados produzidos pelo protótipo entre aberturas.
- Tornar `text/html` inline nos endpoints genéricos de assets.

## Requisitos EARS

### Upload e vínculo

- **R1 — Event-driven:** Quando um editor autorizado selecionar um arquivo `.html` válido no card de
  protótipo de uma Page, o sistema deverá armazená-lo como `PAGE_DESCRIPTION` e vinculá-lo à Page e ao
  projeto atuais.
- **R2 — Ubiquitous:** O sistema deverá aceitar como protótipo somente um arquivo cujo nome termine em
  `.html` e cujo MIME normalizado seja `text/html`.
- **R3 — Unwanted:** Se extensão, MIME, tamanho ou assinatura mínima do conteúdo forem inválidos, o
  sistema deverá rejeitar o upload antes de disponibilizar o protótipo e retornar erro localizado.
- **R4 — State-driven:** Enquanto uma versão do protótipo estiver ativa, a Page deverá exibir nome,
  tamanho, data de atualização e ações permitidas ao usuário.
- **R5 — Event-driven:** Quando o editor substituir o protótipo, o sistema deverá publicar a nova
  versão atomicamente e retirar a versão anterior do vínculo ativo sem deixar a Page sem a versão
  anterior caso o novo upload falhe.
- **R6 — Event-driven:** Quando o editor remover o protótipo, o sistema deverá remover o vínculo ativo e
  aplicar ao asset o ciclo de retenção/exclusão já adotado pelo Plane.

### Leitura e isolamento

- **R7 — Event-driven:** Quando um usuário com acesso à Page solicitar “Baixar HTML”, o sistema deverá
  responder por endpoint autenticado com `Content-Disposition: attachment`.
- **R8 — Event-driven:** Quando um usuário com acesso à Page solicitar “Abrir protótipo”, o sistema
  deverá criar uma concessão opaca, de uso único e vida curta, e abrir o visualizador em origem
  separada da origem do Plane.
- **R9 — Ubiquitous:** O HTML do protótipo jamais deverá ser servido inline pela origem do Plane nem por
  endpoint que receba cookies ou credenciais do Plane.
- **R10 — State-driven:** Enquanto o visualizador renderizar o protótipo, o documento deverá estar em
  iframe sandbox sem `allow-same-origin`, formulários, pop-ups, downloads, modais ou navegação do topo.
- **R11 — Ubiquitous:** A resposta do documento deverá aplicar CSP que bloqueie rede, navegação,
  submissão, objetos, plugins, frames filhos e base URL; CSS, imagens e fontes somente poderão vir do
  próprio arquivo por `data:` ou `blob:` quando explicitamente permitidos.
- **R12 — Unwanted:** Se a concessão estiver expirada, já consumida, adulterada ou associada a usuário,
  workspace, projeto, Page ou asset diferente, o visualizador deverá falhar fechado sem revelar o
  conteúdo ou metadados privados.
- **R13 — Event-driven:** Quando o acesso do usuário ao projeto ou à Page for revogado, novas concessões
  deverão ser negadas; concessões ainda não consumidas deverão ser revalidadas no consumo.

### Compatibilidade e operação

- **R14 — Ubiquitous:** Uploads de imagens e downloads de assets existentes deverão manter o
  comportamento atual.
- **R15 — Ubiquitous:** A allowlist inline global deverá continuar excluindo `text/html` e
  `image/svg+xml`.
- **R16 — State-driven:** Enquanto a origem isolada não estiver configurada ou saudável, o card deverá
  manter “Baixar HTML” e indisponibilizar “Abrir protótipo” com mensagem segura.
- **R17 — Ubiquitous:** Logs e métricas não deverão registrar conteúdo HTML, URL assinada do storage,
  token de concessão ou dados do protótipo.
- **R18 — Optional:** Onde a instância tiver protótipos desabilitados, o sistema não deverá exibir os
  controles de upload ou preview e deverá rejeitar as rotas específicas.

## Arquitetura

```text
Editor da Page
  -> API Plane: criar asset PAGE_DESCRIPTION/HTML
  -> URL assinada de upload
  -> storage S3 compatível
  -> API Plane: confirmar e ativar asset na Page

Leitor da Page
  -> Download: API Plane -> URL assinada com disposition=attachment
  -> Preview: API Plane cria concessão opaca de uso único
       -> navegador abre origem isolada
       -> visualizador consome concessão na API interna
       -> visualizador obtém bytes do storage no servidor
       -> resposta CSP + iframe sandbox sem credenciais Plane
```

O visualizador é uma fronteira de segurança, não apenas um componente visual. Sua origem deverá ser
distinta por esquema/host/porta e não poderá compartilhar cookies com o Plane. Para o laboratório, a
forma recomendada é um host dedicado, por exemplo `plane-prototypes.<domínio>`, sem `Domain=` comum.

## Modelo de dados

### Asset

Reutilizar `FileAsset`:

- `entity_type = PAGE_DESCRIPTION`;
- `page_id = Page.id`;
- `project_id = Page.project_id`;
- `attributes.name` sanitizado;
- `attributes.type = text/html`;
- `attributes.size` validado no pedido e confirmado pelo metadata do objeto;
- `is_uploaded = true` somente após confirmação bem-sucedida.

### Vínculo ativo

A Page precisa apontar de forma explícita para o protótipo ativo, sem inferir “o último asset”. Adicionar
campo anulável `prototype_asset` em `Page`, com `SET_NULL`, ou entidade equivalente se a revisão de
modelo demonstrar necessidade de histórico independente.

Invariantes:

1. o asset ativo pertence à mesma Page, projeto e workspace;
2. o asset está confirmado, não excluído e tem tipo de entidade `PAGE_DESCRIPTION`;
3. só um protótipo está ativo por Page no MVP;
4. troca do vínculo ocorre em transação após confirmação do novo objeto;
5. exclusão da Page segue a política existente para seus assets.

### Concessão de preview

Usar store server-side efêmero, preferencialmente Redis já operado pela instalação. Registrar somente:

- hash do token opaco aleatório;
- usuário solicitante;
- workspace, projeto, Page e asset;
- criação e expiração;
- estado `unused` ou `consumed`.

TTL inicial: 60 segundos para consumo. O consumo é atômico e de uso único. Após consumido, o navegador
recebe a página do visualizador; recarregar exige nova concessão. Não colocar IDs privados nem URL do
storage no token.

## API

Os nomes finais devem seguir o padrão de rotas de Pages do fork.

```text
POST   /api/workspaces/{slug}/projects/{project_id}/pages/{page_id}/prototype/
       cria metadata/URL de upload para text/html

POST   /api/workspaces/{slug}/projects/{project_id}/pages/{page_id}/prototype/{asset_id}/confirm/
       confirma objeto, valida metadata e ativa atomicamente

GET    /api/workspaces/{slug}/projects/{project_id}/pages/{page_id}/prototype/download/
       redireciona para URL assinada com attachment

POST   /api/workspaces/{slug}/projects/{project_id}/pages/{page_id}/prototype/preview-grant/
       cria concessão opaca curta

DELETE /api/workspaces/{slug}/projects/{project_id}/pages/{page_id}/prototype/
       remove vínculo ativo

GET    https://<prototype-origin>/view/{opaque_token}
       consome concessão e entrega o shell isolado
```

O serviço isolado deverá buscar os bytes do asset pelo backend/storage, sem redirecionar o navegador
para a URL S3. Assim, o browser nunca executa conteúdo no domínio do bucket e nenhum token Plane é
encaminhado ao visualizador.

## Validação do arquivo

O backend é a autoridade. Validação mínima:

1. nome sanitizado terminado em `.html`, sem depender de caixa;
2. MIME declarado exatamente `text/html` após normalização de parâmetros;
3. tamanho entre 1 byte e `FILE_SIZE_LIMIT`;
4. metadata do objeto após upload compatível com o pedido;
5. leitura inicial limitada para rejeitar binário evidente, NUL e conteúdo sem marcador HTML mínimo;
6. nenhuma descompactação, transpilação ou execução no backend.

A inspeção mínima não torna o HTML confiável; ela só evita erro acidental. O isolamento continua sendo a
proteção obrigatória contra conteúdo hostil.

## Política do visualizador

### Headers do shell e do documento

Aplicar, no mínimo:

```text
Content-Security-Policy:
  default-src 'none';
  script-src 'unsafe-inline';
  style-src 'unsafe-inline';
  img-src data: blob:;
  font-src data:;
  connect-src 'none';
  media-src data: blob:;
  object-src 'none';
  frame-src 'none';
  worker-src 'none';
  form-action 'none';
  base-uri 'none';
  frame-ancestors 'self';
  navigate-to 'none';
  sandbox allow-scripts;
```

Também:

- `Referrer-Policy: no-referrer`;
- `X-Content-Type-Options: nosniff`;
- `Cache-Control: no-store`;
- `Permissions-Policy` negando câmera, microfone, geolocalização, clipboard, USB e sensores;
- nenhum `Access-Control-Allow-Credentials`;
- nenhum cookie de sessão ou autenticação na origem isolada.

O iframe deverá usar `sandbox="allow-scripts"` e não adicionar `allow-same-origin`. A CSP final será
validada no navegador alvo; diretivas sem suporte não substituem o sandbox nem a separação de origem.

### Autocontenção

Referências `http:`, `https:`, `//`, caminhos absolutos ou relativos podem permanecer nos bytes, mas não
devem carregar devido à CSP. A UI deverá avisar que protótipos dependentes de recursos externos podem
aparecer incompletos. `data:` e `blob:` são os mecanismos suportados no MVP.

## Autorização

- Leitura, download e preview exigem acesso atual à Page/projeto.
- Criar, substituir e remover exigem a mesma capacidade usada para editar a Page.
- O backend deve carregar Page e asset já filtrados por workspace e projeto da rota.
- IDs fornecidos pelo cliente nunca autorizam vínculo cruzado.
- A origem isolada não autentica usuários por cookie; ela aceita somente a concessão opaca e consulta o
  backend por canal interno autenticado.

## UX do card na Page

### Sem protótipo

- título “Protótipo HTML”;
- explicação curta: “Anexe um HTML autocontido para validar a experiência desta spec.”;
- botão “Anexar HTML” para quem pode editar;
- seletor com `accept=".html,text/html"`.

### Com protótipo

- nome, tamanho e data da atualização;
- botão primário “Abrir protótipo”;
- botão “Baixar HTML”;
- menu “Substituir” e “Remover” para editores;
- indicador e bloqueio das ações durante upload/confirmação.

### Erros

- formato inválido;
- arquivo vazio ou acima do limite;
- upload interrompido;
- confirmação falhou, mantendo a versão anterior;
- preview indisponível, preservando download;
- acesso revogado ou concessão expirada.

Todas as strings entram no namespace de i18n correspondente; inglês é a fonte e todos os locales devem
manter paridade.

## Migração e compatibilidade

1. Migração adiciona somente o vínculo anulável; Pages existentes continuam válidas.
2. Não alterar a semântica global de `PAGE_DESCRIPTION` para imagens.
3. Não adicionar `text/html` à allowlist genérica inline.
4. Criar rota de upload específica para protótipo, em vez de ampliar indiscriminadamente os endpoints
   V2 hoje usados pelo editor.
5. Assets órfãos de upload abortado devem ser limpos pelo mecanismo existente ou por job idempotente.
6. Rollback da aplicação deve tolerar o campo novo; rollback destrutivo da migration só ocorre depois de
   remover vínculos ativos.

## Feature flags e configuração

- `ENABLE_PAGE_HTML_PROTOTYPES`, default `0` até validação do laboratório;
- `PAGE_PROTOTYPE_ORIGIN`, URL canônica da origem isolada;
- `PAGE_PROTOTYPE_GRANT_TTL_SECONDS`, default `60`, com faixa segura validada;
- chave/credencial interna do visualizador armazenada como segredo, nunca exposta no frontend.

Habilitar preview exige origem HTTPS válida em produção, distinta da origem do Plane e sem domínio de
cookie compartilhado. Upload e download podem ser habilitados separadamente do preview durante rollout.

## Observabilidade

Métricas agregadas:

- upload iniciado, confirmado, rejeitado e abortado;
- substituição e remoção;
- concessão criada, consumida, expirada e negada;
- falha de leitura do storage e indisponibilidade do visualizador.

Logs incluem correlation ID, IDs internos necessários e fase da operação. Excluem conteúdo, token,
URL assinada e nome original quando a política de PII não permitir.

## Contratos de teste

### Gate 1 — backend e modelo

- criação válida para Page/projeto/workspace corretos;
- rejeição de extensão, MIME, tamanho, NUL, vazio e metadata divergente;
- confirmação só após objeto existente;
- vínculo cruzado e IDOR rejeitados;
- substituição atômica mantém versão anterior em falha;
- remoção e exclusão da Page respeitam lifecycle;
- migration e rollback não destrutivo.

### Gate 2 — download e preview

- download sempre usa `attachment` e nome sanitizado;
- HTML e SVG continuam fora da allowlist inline;
- concessão aleatória, curta, single-use e consumo atômico;
- concessão expirada, repetida, adulterada ou de outro escopo falha fechada;
- revalidação de acesso no consumo;
- navegador não recebe URL assinada do storage.

### Gate 3 — sandbox adversarial

Executar protótipos de teste que tentem:

- ler cookies, `localStorage`, `sessionStorage` e DOM do Plane;
- chamar API Plane com e sem `fetch`;
- carregar script, imagem, CSS, iframe e WebSocket externos;
- submeter formulário, abrir popup, iniciar download e navegar `top`;
- usar service worker, worker, câmera, microfone, clipboard e geolocalização;
- explorar `base`, redirect, `javascript:` e `data:` navegável.

O teste E2E deverá demonstrar bloqueio no navegador e ausência de requisição autenticada ao Plane.

### Gate 4 — frontend

- estados vazio, upload, ativo, substituição, remoção e erro;
- permissões de leitor e editor;
- preview indisponível preserva download;
- acessibilidade por teclado, foco, nome acessível e anúncio de progresso/erro;
- paridade i18n, lint, typecheck e build.

### Gate 5 — laboratório

1. upload de HTML autocontido real;
2. abertura a partir da Page por cliente autorizado;
3. isolamento comprovado pelas DevTools/rede;
4. substituição sem janela quebrada;
5. revogação de acesso;
6. indisponibilidade simulada do visualizador;
7. rollback para imagem anterior preservando banco, assets e Pages.

## Rollout

1. Implementar testes RED antes do código.
2. Entregar upload/download atrás da flag, mantendo preview desligado.
3. Implantar a origem isolada e executar a suíte adversarial.
4. Habilitar preview somente no laboratório.
5. Fazer revisão independente de segurança e código.
6. Validar cliente real em uma spec de negócio não sensível.
7. Promover mantendo imagem anterior e procedimento de rollback.

## Riscos e mitigação

- **XSS e roubo de sessão:** origem separada, sem cookies, CSP e iframe sandbox sem same-origin.
- **Exfiltração por rede:** `connect-src`, `img-src`, `media-src`, `font-src` e `frame-src` restritivos.
- **Phishing dentro do protótipo:** banner permanente do visualizador identificando conteúdo de
  protótipo, sem barra/endereço semelhante ao Plane e sem navegação de topo.
- **Bypass por URL assinada:** bytes intermediados pelo serviço isolado; URL do bucket não vai ao browser.
- **IDOR:** Page e asset carregados por workspace/projeto e concessão vinculada a todo o escopo.
- **Corrida na substituição:** confirmação e troca do vínculo em transação.
- **Incompatibilidade de protótipo:** contrato explícito de arquivo único com recursos `data:`/`blob:`.
- **Indisponibilidade adicional:** download continua funcional quando o visualizador falha.

## Decisões pendentes antes da implementação

1. Definir hostname e certificado da origem isolada no laboratório HCSO.
2. Confirmar se o visualizador será um serviço mínimo separado ou uma superfície sem sessão do Web,
   mantendo em ambos os casos deploy e origem independentes.
3. Validar no navegador alvo o suporte efetivo às diretivas CSP, especialmente `navigate-to`, e manter
   testes adversariais para os controles complementares.

## Fontes

- **F1 — código:** `apps/web/app/(all)/[workspaceSlug]/(projects)/projects/(detail)/[projectId]/pages/(detail)/[pageId]/page.tsx` — Page configura assets como `PAGE_DESCRIPTION`.
- **F1 — código:** `apps/api/plane/db/models/asset.py` — vínculo `FileAsset.page`, URL e tipos de entidade.
- **F1 — código:** `apps/api/plane/app/views/asset/v2.py` — upload atual restrito a imagens e downloads com `attachment`.
- **F1 — código:** `apps/api/plane/settings/common.py` — MIME types aceitos e allowlist inline, que exclui HTML e SVG.
- **F1 — código:** `apps/api/plane/settings/storage.py` — conteúdo fora da allowlist é forçado a download.
- **F1 — código:** `packages/editor/src/constants/config.ts` e `packages/editor/src/plugins/drop.ts` — HTML não é aceito e inserção de attachment não está implementada.
- **F1 — padrão:** W3C Content Security Policy Level 3 e HTML Living Standard, atributos de sandbox de iframe.
- **F3 — medição:** inspeção do fork `fork/nested-pages`, SHA base `60e0f19656886c8b780ef7fb717ba8e20c742b70`, em 25/nov/2026.
