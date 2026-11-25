# PRD: autenticação OIDC compatível com Keycloak

## Status

Rascunho para aprovação. Nenhuma implementação OIDC pode começar antes da aprovação deste PRD e da SPEC técnica associada.

## Problema

O Plane comunitário deste fork possui provedores OAuth específicos, mas não oferece um provedor OIDC genérico e seguro para autenticação por um realm Keycloak. Adaptar apenas o provedor Gitea não é suficiente: o fluxo atual não implementa discovery, PKCE, nonce nem validação criptográfica completa do ID Token.

## Objetivo

Permitir autenticação interativa no Plane por um provedor OIDC global da instância, inicialmente validado contra Keycloak, sem enfraquecer os métodos locais existentes e sem tratar e-mail como identidade federada.

## Personas

- **Administrador da instância:** configura e valida a integração com o realm Keycloak.
- **Usuário final:** entra no Plane Web ou Space pelo provedor corporativo.
- **Operador:** implanta, monitora, desabilita e recupera a integração sem perder o acesso administrativo.

## Funcionalidades

- **F1 — Configuração:** issuer, client ID, client secret, scopes, nome de exibição e habilitação global.
- **F2 — Login:** Authorization Code Flow com PKCE S256 em Web e Space.
- **F3 — Validação:** discovery, JWKS, assinatura e claims obrigatórias do ID Token.
- **F4 — Identidade:** vínculo estável pelo par `(issuer, subject)`.
- **F5 — Segurança operacional:** HTTPS, timeouts, proteção SSRF, redirects restritos e ausência de segredos em logs/respostas.
- **F6 — Logout:** término local sempre garantido e logout federado opcional quando anunciado pelo provedor.
- **F7 — Administração segura:** segredo mascarado, configuração validada antes da habilitação e método local de emergência preservado.

## Critérios de aceitação (EARS)

- **Ubiquitous:** O sistema deve manter o provedor OIDC desabilitado por padrão.
- **Ubiquitous:** O sistema deve identificar uma conta federada por issuer e subject validados criptograficamente, nunca apenas por e-mail.
- **Ubiquitous:** O sistema não deve expor client secret, authorization code, tokens, nonce ou PKCE verifier em logs, URLs finais ou respostas públicas.
- **Event-driven:** Quando o administrador habilitar OIDC, o sistema deve validar a configuração e o discovery antes de tornar o método disponível.
- **Event-driven:** Quando o usuário iniciar o login, o sistema deve criar uma tentativa curta e single-use contendo state, nonce e PKCE verifier independentes.
- **Event-driven:** Quando o callback for recebido, o sistema deve consumir a tentativa, trocar o code com PKCE e validar assinatura, issuer, audience, authorized party, tempo, nonce e subject.
- **Event-driven:** Quando userinfo for utilizado, o sistema deve exigir que seu subject seja igual ao subject do ID Token.
- **State-driven:** Enquanto o provedor estiver desabilitado, seus botões não devem aparecer e os endpoints devem falhar antes de qualquer chamada ao IdP.
- **State-driven:** Enquanto a instalação estiver em produção, issuer e endpoints OIDC devem usar HTTPS.
- **Unwanted:** Se o e-mail estiver ausente, inválido ou não verificado, o sistema deve rejeitar a autenticação sem criar usuário, conta externa ou sessão.
- **Unwanted:** Se já existir usuário local com o mesmo e-mail e não houver vínculo federado, o sistema não deve fazer auto-link silencioso.
- **Unwanted:** Se discovery, JWKS, token ou userinfo falharem ou forem inconsistentes, o sistema deve falhar fechado e atomicamente.
- **Unwanted:** Se o logout federado falhar, o sistema ainda deve encerrar a sessão local.

## Requisitos operacionais

- Deve existir smoke test contra Keycloak descartável antes da liberação.
- A configuração deve suportar CA privada sem permitir `verify=false` em produção.
- Deve permanecer disponível um método administrativo local de emergência.
- A implantação deve documentar callback URI, post-logout URI, rotação de secret e rotação de chaves.

## Fora de escopo

- OIDC diferente por workspace ou domínio.
- Mais de um issuer OIDC por instância.
- Provisionamento de permissões por grupos ou roles do Keycloak.
- SCIM.
- SAML.
- Uso de JWT Keycloak como bearer token da API Plane.
- SSO do console administrativo.
- Back-channel logout na primeira versão.

## Gates de produto

1. PRD aprovado.
2. SPEC aprovada.
3. Testes RED dos contratos críticos.
4. Implementação e regressão dos OAuth existentes.
5. Smoke real com Keycloak.
6. Revisão independente de segurança.
7. Documentação operacional e rollout com feature desabilitada por padrão.
