# Estado: protótipo HTML autocontido em Plane Pages

## Status

SPEC_DRAFT — aguardando aprovação.

## Fases

- [x] Investigação do fluxo atual de assets de Pages
- [x] Análise de MIME, download e isolamento
- [x] Decisão de produto: download mais preview em sandbox de origem separada
- [x] SPEC técnica criada
- [ ] SPEC aprovada
- [ ] Hostname e topologia do visualizador definidos
- [ ] Tasks geradas
- [ ] Testes RED
- [ ] Implementação
- [ ] Suíte adversarial no navegador
- [ ] Revisão independente
- [ ] Deploy no laboratório HCSO
- [ ] Validação com spec de negócio real

## Regra de execução

Nenhum teste ou código desta feature será criado antes da aprovação desta SPEC.

## Decisões

- 25/nov/2026: cada Page poderá ter um protótipo HTML autocontido ativo.
- 25/nov/2026: download continua forçado como attachment na origem do Plane.
- 25/nov/2026: preview executável somente em origem isolada, sem cookies Plane, com CSP e iframe sandbox.
- 25/nov/2026: HTML não será adicionado à allowlist global de conteúdo inline.
- 25/nov/2026: MVP aceita arquivo único `.html`; pacote ZIP/site multifile fica fora do escopo.

## Próximo passo

1. Aprovar ou ajustar a SPEC.
2. Definir hostname/certificado e forma de deploy do visualizador no HCSO.
3. Gerar tarefas e contratos RED.
4. Implementar e validar localmente antes de promover a imagem.
