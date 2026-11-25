# DAIA Plane fork: maintenance and upstream updates

This repository is the DAIA-maintained fork of Plane. Product documentation and
DAIA-specific patches must always be committed and published to the fork, never
directly to `makeplane/plane`.

## Remotes and protected direction

```text
origin    https://github.com/filhocf/plane.git    # DAIA fork, push target
upstream  https://github.com/makeplane/plane.git  # canonical Plane, fetch only in practice
```

The working branch for the nested Pages and agent API work is:

```text
fork/nested-pages
```

Never push DAIA changes to `upstream`. Before every push, verify:

```bash
git remote -v
git branch -vv
git push origin fork/nested-pages
```

## DAIA patch stack

The branch currently starts from upstream `v1.5.1-rc1` / `preview` commit
`c72e9d4` and contains these committed layers:

1. `a85ad51` — nested Pages become first-class for create/retrieve/validation.
2. `a637db3` — authenticated external API v1 for Pages, including search,
   pagination, archive/restore, safe two-phase deletion, authorization and
   concurrency controls.

The remaining nested-Pages app API, UI, store and publisher layer is maintained
on the same branch. Inspect `git status` before an update and never discard
local work.

Primary files for this layer:

```text
apps/api/plane/app/views/page/base.py
apps/api/plane/app/urls/page.py
apps/web/components/pages/list/
apps/web/components/pages/editor/page-root.tsx
apps/web/store/pages/project-page.store.ts
apps/web/services/page/project-page.service.ts
tools/publish_nested_pages.py
```

## External Pages API added by DAIA

Routes under:

```text
/api/v1/workspaces/{slug}/projects/{project_id}/pages/
```

The fork adds API-key-authenticated list/search/detail/tree/create/update,
archive/restore and deletion. Important invariants:

- project and ancestor visibility must not leak private Pages;
- hierarchy mutations are serialized by a project row lock;
- parent choice reuses shared cycle/depth validation;
- archive/restore operates on a locked subtree and rejects locked Pages;
- delete requires an archived Page and a state-bound HMAC `If-Match` token;
- direct children are promoted before soft deletion;
- initialized Plane Live content is never blindly overwritten.

Primary files:

```text
apps/api/plane/api/urls/__init__.py
apps/api/plane/api/urls/page.py
apps/api/plane/api/views/page.py
apps/api/plane/tests/contract/api/test_page_api_key.py
apps/api/plane/tests/contract/api/test_page_lifecycle_api_key.py
```

## Updating from upstream

Do updates on a temporary integration branch. Do not rewrite the published
feature branch before validation.

```bash
git status --short
git fetch upstream --tags --prune
git fetch origin --prune
git switch fork/nested-pages
git switch -c maintenance/plane-upstream-YYYYMMDD
git merge --no-ff upstream/preview
```

Resolve conflicts by preserving DAIA invariants, not by mechanically accepting
either side. Pay special attention to:

- Page model, serializer and permission changes;
- app Page views and shared parent validation;
- external API URL aggregation;
- soft-delete behavior and background deletion tasks;
- paginator cursor semantics;
- Plane Live/Yjs content conversion;
- migrations touching Page hierarchy or archive fields.

After resolving, run at least:

```bash
cd apps/api
uv sync
uv run ruff check \
  plane/api/views/page.py \
  plane/api/urls/page.py \
  plane/tests/contract/api/test_page_api_key.py \
  plane/tests/contract/api/test_page_lifecycle_api_key.py

# Use the repository's isolated test stack when available.
cd ../..
docker compose -f docker-compose-test.yml run --rm --build api-tests \
  pytest -q \
  plane/tests/contract/api/test_page_api_key.py \
  plane/tests/contract/api/test_page_lifecycle_api_key.py

git diff --check
```

Also execute the nested-Pages backend, frontend, i18n, build and browser E2E
gates before promotion. Obtain an independent G5 review after every upstream
merge because permissions, hierarchy and soft deletion are security-sensitive.

The focused nested-Pages gates are:

```bash
podman exec -e DJANGO_SETTINGS_MODULE=plane.settings.test -w /code plane-api \
  python -m pytest -q \
  plane/tests/contract/app/test_page_nested_pages_app.py \
  plane/tests/contract/app/test_page_tree_endpoints_app.py \
  plane/tests/contract/api/test_page_api_key.py \
  plane/tests/contract/api/test_page_lifecycle_api_key.py

PYTHONPATH=tools python3 -m unittest -v tools/test_publish_nested_pages.py
python3 -m py_compile tools/publish_nested_pages.py tools/test_publish_nested_pages.py
corepack pnpm --filter=web check:types
corepack pnpm --filter=web check:lint
corepack pnpm --filter=@plane/types check:types
corepack pnpm --filter=@plane/i18n sync:check --ci
corepack pnpm --filter=web build
git diff --check
```

When green, publish the integration branch to the fork and merge it into
`fork/nested-pages` through the fork's normal review flow.

## Baseline evidence for `a637db3`

The API slice was accepted after:

- 40 focused contract tests passed;
- Ruff 0.9.7 passed using the repository-pinned uv 0.12.21;
- `git diff --check` passed;
- independent G5 review returned `APPROVE`, with no HIGH, MEDIUM or LOW finding.

The test environment emitted only an unregistered local `contract` marker and a
missing collected-static directory warning; neither changed test outcomes.

## Rollback

Before deployment, record the exact fork revision and image tag. Roll back by
redeploying the previous fork image/revision. Do not revert database migrations
without checking upstream migration dependencies. For source-only rollback of a
new patch, create a revert commit in the fork instead of rewriting published
history.
