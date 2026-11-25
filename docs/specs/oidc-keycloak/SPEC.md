# SPEC: autenticação OIDC compatível com Keycloak

## Status

Rascunho técnico para aprovação. Esta SPEC precede testes e implementação.

## Escopo técnico

- Backend: Django, PostgreSQL, sessão Plane e `requests`/PyJWT já presentes.
- Frontend: Next.js/React para Web, Space e Admin.
- Protocolo: OpenID Connect Authorization Code Flow com PKCE S256.
- Topologia inicial: um provider e um issuer global por instância.

## Arquitetura

```text
Web/Space
  -> GET /auth/{surface}/oidc
  -> OIDC transaction store (session, indexed by state, TTL)
  -> Keycloak authorization endpoint
  -> GET /auth/{surface}/oidc/callback
  -> discovery + token endpoint + JWKS validation
  -> identity resolver (issuer, sub)
  -> Plane session

Admin
  -> instance configuration API
  -> validate discovery/configuration
  -> encrypted client secret
```

O provider OIDC será separado dos providers OAuth existentes. O adapter OAuth atual poderá fornecer padrões de erro e persistência, mas não será usado como fronteira de validação OIDC.

## Configuração

Chaves da instância:

- `IS_OIDC_ENABLED`, default `0`;
- `OIDC_ISSUER`;
- `OIDC_CLIENT_ID`;
- `OIDC_CLIENT_SECRET`, criptografado e nunca devolvido em claro;
- `OIDC_SCOPES`, default `openid email profile`;
- `OIDC_DISPLAY_NAME`, default `OpenID Connect`;
- `OIDC_REQUIRE_VERIFIED_EMAIL`, default `1`;
- `OIDC_LOGOUT_ENABLED`, default `0`;
- `OIDC_CA_BUNDLE`, opcional e operacionalmente protegido;
- callback base canônica derivada da URL pública configurada da instância, não de `Host` arbitrário.

Somente o issuer será configurável como raiz do protocolo. Authorization, token, userinfo, JWKS e end-session virão do discovery.

## Discovery e rede

O documento será obtido de `{issuer}/.well-known/openid-configuration`, preservando paths de realm Keycloak.

Validações obrigatórias:

1. `metadata.issuer` exatamente igual ao issuer canônico;
2. presença de authorization endpoint, token endpoint e JWKS URI;
3. suporte a `response_type=code`, PKCE S256 e algoritmo assimétrico permitido;
4. HTTPS em produção;
5. sem credenciais embutidas, fragmentos ou URLs relativas;
6. timeouts de conexão/leitura e limite de corpo;
7. token endpoint sem redirects;
8. redirects de discovery/JWKS/userinfo bloqueados ou revalidados a cada hop;
9. bloqueio de loopback, link-local, metadata cloud e redes reservadas, com allowlist explícita para Keycloak privado;
10. cache por issuer, TTL limitado e invalidação ao mudar configuração.

## Tentativa de autenticação

Cada iniciação cria uma entrada de sessão indexada pelo `state` contendo:

- `state` aleatório;
- `nonce` aleatório;
- `code_verifier` aleatório;
- `code_challenge = BASE64URL(SHA256(code_verifier))`;
- superfície `app` ou `space`;
- issuer/config version;
- callback URI exata;
- `next_path` já validado;
- instante e expiração entre 5 e 10 minutos.

Tentativas concorrentes em abas diferentes não se sobrescrevem. A tentativa é consumida atomicamente antes da troca externa e nunca pode ser reutilizada, inclusive após falha do token endpoint.

## Authorization request

Parâmetros obrigatórios:

- `client_id`;
- `response_type=code`;
- scopes contendo `openid`;
- callback URI exata;
- `state`;
- `nonce`;
- `code_challenge`;
- `code_challenge_method=S256`.

O verifier nunca será enviado ao browser como parâmetro da URL.

## Callback e troca de token

Ordem obrigatória:

1. rejeitar combinações ambíguas de `code` e `error`;
2. validar e consumir a tentativa por state;
3. confirmar TTL, superfície, config version e callback;
4. revalidar que o provider continua habilitado;
5. trocar o code com grant, callback e verifier exatos;
6. exigir ID Token;
7. resolver a chave por `kid`, com no máximo um refresh do JWKS;
8. validar assinatura e allowlist de algoritmo;
9. validar `iss`, `aud`, `azp`, `exp`, `iat`, `nbf`, `nonce` e `sub`;
10. obter claims adicionais de userinfo somente se necessário e exigir o mesmo `sub`;
11. resolver identidade e concluir login em transação;
12. redirecionar somente ao path interno armazenado na tentativa.

Clock skew será pequeno, explícito e testado. `alg=none`, confusão HS/RS, chave incompatível ou audience de outro client serão rejeitados.

## Claims

| Uso no Plane | Claim OIDC |
|---|---|
| Identidade externa | `iss` + `sub` |
| E-mail | `email` |
| Confiança do e-mail | `email_verified == true` |
| Primeiro nome | `given_name` |
| Sobrenome | `family_name` |
| Nome exibido | `name`, fallback `preferred_username` |
| Avatar | `picture`, sujeito às proteções de URL existentes |

`preferred_username`, nome, grupos e roles nunca serão usados como identidade.

## Modelo de dados

O modelo `Account` ganhará issuer normalizado para identidades OIDC.

Regra lógica:

```text
UNIQUE(provider, issuer, provider_account_id)
provider = "oidc"
provider_account_id = sub
```

Providers legados continuam válidos sem issuer. A migração deve ser reversível e não alterar contas existentes.

Tokens de acesso e refresh não serão persistidos no MVP. Se o logout federado exigir ID Token hint, seu armazenamento deverá ser mínimo, expirável e protegido; preferencialmente ficará apenas na sessão.

## Linking e criação de usuário

1. Procurar primeiro a conta por `(provider=oidc, issuer, sub)`.
2. Se encontrada, autenticar exclusivamente o usuário vinculado; mudança de e-mail não muda o vínculo.
3. Se não encontrada e o e-mail já pertencer a usuário local, interromper com fluxo de linking explícito; não auto-linkar.
4. Se não existir usuário, obedecer signup, convite e políticas locais existentes.
5. Criar usuário e vínculo em transação única.
6. Conflitos e corridas de unicidade falham fechados, sem sessão parcial.
7. Convites só são processados depois da identidade OIDC estar plenamente validada.

O fluxo exato da UI de linking explícito será detalhado em incremento separado antes de ser implementado. Até lá, colisão com e-mail existente resulta em erro orientativo seguro.

## Rotas

```text
GET /auth/oidc/                         # initiate Web
GET /auth/oidc/callback/                # callback Web
GET /auth/spaces/oidc/                  # initiate Space
GET /auth/spaces/oidc/callback/         # callback Space
POST /auth/oidc/sign-out/               # logout local/federado, se necessário
```

Os nomes finais seguirão o padrão das rotas OAuth existentes. Callbacks App e Space devem compartilhar o núcleo do protocolo para evitar divergência de segurança.

## Logout

- A sessão Plane será encerrada antes de qualquer chamada ou redirect federado.
- Se habilitado e anunciado pelo discovery, usar o `end_session_endpoint` validado.
- `post_logout_redirect_uri` será canônica e allowlisted.
- Nenhum access/refresh token será colocado em URL.
- Ausência ou falha do endpoint federado não restaura a sessão local.

## API e UI administrativas

- Apenas InstanceAdmin pode ler ou alterar a configuração.
- Leitura retorna `client_secret_configured`, nunca o segredo.
- Secret mascarado não pode ser reenviado como novo valor.
- Habilitação exige configuração completa e discovery válido.
- Falha de validação não substitui a configuração funcional anterior.
- Não será possível desabilitar o último método administrativo local.
- Alterações de issuer, client ID, secret ou enable geram auditoria.

Web e Space exibem o botão apenas quando a flag pública segura indicar provider habilitado. Ambos codificam `next_path` e chamam suas rotas corretas.

## Erros e observabilidade

- Respostas públicas usam códigos Plane estáveis e mensagens localizadas.
- `error_description` remoto nunca é refletido como HTML.
- Logs incluem correlation ID, issuer normalizado e fase agregada da falha.
- Logs excluem secret, code, state, nonce, verifier, ID Token, access token e refresh token.
- Métricas distinguem discovery, token, JWKS, claims e linking sem PII.

## Contratos de teste

### Gate 1 — protocolo

- discovery Keycloak válido e issuer divergente;
- metadata incompleta e endpoint inseguro;
- state single-use por sessão/tentativa;
- nonce e PKCE S256;
- callback ambíguo e erro do IdP;
- timeouts e respostas inválidas do token endpoint;
- assinatura/JWKS, rotação e algoritmos proibidos;
- `iss`, `aud`, `azp`, tempos, nonce e subject;
- userinfo subject divergente;
- e-mail ausente ou não verificado.

### Gate 2 — identidade

- login repetido resolve a mesma conta por issuer+subject;
- mesmo subject em issuers diferentes não colide;
- mudança de e-mail não relinka;
- e-mail local existente não auto-linka;
- corrida na criação respeita unicidade e atomicidade;
- provider desabilitado durante tentativa falha antes do token endpoint.

### Gate 3 — perímetro

- SSRF, redirects, DNS/IP reservado e downgrade HTTP;
- callback e post-logout URI controladas pelo Plane;
- autorização da configuração administrativa;
- ausência de segredos em logs/respostas;
- logout local garantido.

### Gate 4 — superfícies e regressão

- paridade App/Space;
- botão e configuração Admin;
- OAuth Google/GitHub/GitLab/Gitea continuam funcionando;
- typecheck, lint, testes backend e build web.

### Gate 5 — Keycloak real

Keycloak descartável com realm dedicado, client confidencial, PKCE S256, redirects exatos e usuário com e-mail verificado deve provar:

1. discovery real;
2. login e criação do vínculo;
3. segundo login na mesma conta;
4. replay de state/nonce rejeitado;
5. rotação de chave;
6. e-mail não verificado rejeitado;
7. logout local e federado quando suportado.

## Rollout

1. Feature permanece desligada por padrão.
2. Validar primeiro em ambiente de laboratório.
3. Preservar login local de emergência.
4. Não habilitar forced OIDC na primeira entrega.
5. Só promover após smoke Keycloak, revisão independente e procedimento de rollback testado.

## Riscos e mitigação

- **Account takeover por e-mail:** identidade issuer+subject e sem auto-link silencioso.
- **Token substitution:** nonce e validação completa do ID Token.
- **Interceptação de code:** PKCE S256 e callback exata.
- **SSRF:** HTTPS, validação de destino, allowlist privada, redirects restritos e egress controlado.
- **DoS pelo IdP:** timeout, limites de corpo e cache limitado.
- **Lockout administrativo:** método local independente e feature desligada por padrão.
- **Conflito com oferta enterprise:** implementação isolada no fork e decisão de produto registrada antes de eventual upstream.

## Decisões pendentes antes da aprovação final

1. Confirmar formalmente que esta capacidade permanecerá específica do fork.
2. Definir allowlist de rede/CA para o Keycloak interno.
3. Confirmar URL pública canônica usada nos callbacks.
4. Definir se logout federado entra no MVP ou fica somente preparado.
5. Especificar posteriormente a UX completa de linking explícito, caso seja necessária já na primeira entrega.
