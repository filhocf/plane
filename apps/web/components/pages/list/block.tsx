/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useRef, useState } from "react";
import { observer } from "mobx-react";
import { useParams, useRouter } from "next/navigation";
import { Logo } from "@plane/blocks/emoji-icon-picker";
import { PagesOutline } from "@makeplane/propel/icons";
import { setToast } from "@plane/blocks/toast";
import { useTranslation } from "@plane/i18n";
// plane imports
import { getPageName } from "@plane/utils";
// components
import { ListItem } from "@/components/core/list";
import { BlockItemAction } from "@/components/pages/list/block-item-action";
// hooks
import { usePlatformOS } from "@/hooks/use-platform-os";
// plane web hooks
import type { EPageStoreType } from "@/hooks/store";
import { usePage, usePageStore } from "@/hooks/store";

type TPageListBlock = {
  pageId: string;
  storeType: EPageStoreType;
  treeEnabled?: boolean;
  depth?: number;
};

export const PageListBlock = observer(function PageListBlock(props: TPageListBlock) {
  const { pageId, storeType, treeEnabled = false, depth = 0 } = props;
  // refs
  const parentRef = useRef(null);
  const [expanded, setExpanded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const { nodeMap, fetchChildren, createPage, canCurrentUserCreatePage } = usePageStore(storeType);
  const { workspaceSlug, projectId } = useParams();
  const router = useRouter();
  const { t } = useTranslation();
  // hooks
  const page = usePage({
    pageId,
    storeType,
  });
  const { isMobile } = usePlatformOS();
  // handle page check
  if (!page) return null;
  if (treeEnabled && page.archived_at) return null;
  // derived values
  const { name, logo_props, getRedirectionLink } = page;
  const title = getPageName(name);
  const loadChildren = async (force = false) => {
    setLoading(true);
    setLoadError(false);
    try {
      await fetchChildren(pageId, force);
    } catch {
      setLoadError(true);
    } finally {
      setLoading(false);
    }
  };
  const createChild = async () => {
    setCreating(true);
    try {
      const created = await createPage({ parent: pageId, access: page.access });
      if (!created?.id) throw new Error("Missing page id");
      setExpanded(true);
      router.push(`/${workspaceSlug}/projects/${projectId}/pages/${created.id}`);
    } catch {
      setToast({ type: "error", title: t("integration.error"), message: t("nested_pages.create_error") });
    } finally {
      setCreating(false);
    }
  };

  return (
    <div data-page-node={pageId} role="treeitem" aria-level={depth + 1}>
      <ListItem
        prependTitleElement={
          <span className="flex shrink-0 items-center gap-1">
            <span className="grid size-6 place-items-center">
              {logo_props?.in_use ? (
                <Logo logo={logo_props} size={16} type="lucide" />
              ) : (
                <PagesOutline className="size-4 text-tertiary" />
              )}
            </span>
          </span>
        }
        title={title}
        quickActionElement={
          treeEnabled && depth <= 4 ? (
            <button
              type="button"
              aria-label={t(expanded ? "nested_pages.collapse" : "nested_pages.expand", { title })}
              aria-expanded={expanded}
              onClick={() => {
                if (!expanded && !nodeMap[pageId]) void loadChildren();
                setExpanded(!expanded);
              }}
            >
              {expanded ? "▾" : "▸"}
            </button>
          ) : undefined
        }
        leftElementClassName="gap-2"
        itemLink={getRedirectionLink()}
        actionableItems={
          <>
            {treeEnabled && depth < 5 && page.canCurrentUserEditPage && canCurrentUserCreatePage && (
              <button
                type="button"
                title={t("nested_pages.create_child", { title })}
                aria-label={t("nested_pages.create_child", { title })}
                disabled={creating}
                onClick={() => void createChild()}
              >
                +
              </button>
            )}
            <BlockItemAction page={page} parentRef={parentRef} storeType={storeType} />
          </>
        }
        isMobile={isMobile}
        parentRef={parentRef}
      />
      {treeEnabled && expanded && (
        <div className="ml-6 border-l border-subtle pl-2" role="group">
          {loading && <span>{t("nested_pages.loading")}</span>}
          {loadError && (
            <span>
              {t("nested_pages.load_error")} {" "}
              <button type="button" onClick={() => void loadChildren(true)}>
                {t("nested_pages.retry")}
              </button>
            </span>
          )}
          {!loading && !loadError && nodeMap[pageId]?.length === 0 && <span>{t("nested_pages.empty")}</span>}
          {!loading &&
            !loadError &&
            nodeMap[pageId]?.map((childId) => (
              <PageListBlock key={childId} pageId={childId} storeType={storeType} treeEnabled depth={depth + 1} />
            ))}
        </div>
      )}
    </div>
  );
});
