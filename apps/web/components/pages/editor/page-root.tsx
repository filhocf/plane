/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { observer } from "mobx-react";
// plane imports
import type { CollaborationState, EditorRefApi } from "@plane/editor";
import { useTranslation } from "@plane/i18n";
import type { TDocumentPayload, TPage, TPageVersion, TWebhookConnectionQueryParams } from "@plane/types";
import { ControlLink } from "@plane/blocks/layout";
// hooks
import { usePageFallback } from "@/hooks/use-page-fallback";
import type { PageUpdateHandler, TCustomEventHandlers } from "@/hooks/use-realtime-page-events";
import { usePagesPaneExtensions, useExtendedEditorProps } from "@/hooks/pages";
import { EPageStoreType, usePageStore } from "@/hooks/store";
// store
import type { TPageInstance } from "@/store/pages/base-page";
// ui
import { Banner } from "@makeplane/propel/components/banner";
// local imports
import { PageNavigationPaneRoot } from "../navigation-pane";
import { PageVersionsOverlay } from "../version";
import { PagesVersionEditor } from "../version/editor";
import { PageEditorBody } from "./editor-body";
import type { TEditorBodyConfig, TEditorBodyHandlers } from "./editor-body";
import { PageEditorToolbarRoot } from "./toolbar";

export type TPageRootHandlers = {
  create: (payload: Partial<TPage>) => Promise<Partial<TPage> | undefined>;
  fetchAllVersions: (pageId: string) => Promise<TPageVersion[] | undefined>;
  fetchDescriptionBinary: () => Promise<ArrayBuffer>;
  fetchVersionDetails: (pageId: string, versionId: string) => Promise<TPageVersion | undefined>;
  restoreVersion: (pageId: string, versionId: string) => Promise<void>;
  updateDescription: (document: TDocumentPayload) => Promise<void>;
} & TEditorBodyHandlers;

export type TPageRootConfig = TEditorBodyConfig;

type TPageRootProps = {
  config: TPageRootConfig;
  handlers: TPageRootHandlers;
  page: TPageInstance;
  storeType: EPageStoreType;
  webhookConnectionParams: TWebhookConnectionQueryParams;
  projectId?: string;
  workspaceSlug: string;
  customRealtimeEventHandlers?: TCustomEventHandlers;
};

export const PageRoot = observer(function PageRoot(props: TPageRootProps) {
  const {
    config,
    handlers,
    page,
    projectId,
    storeType,
    webhookConnectionParams,
    workspaceSlug,
    customRealtimeEventHandlers,
  } = props;
  // states
  const [editorReady, setEditorReady] = useState(false);
  const [collaborationState, setCollaborationState] = useState<CollaborationState | null>(null);
  const [showContentTooLargeBanner, setShowContentTooLargeBanner] = useState(false);
  const [ancestors, setAncestors] = useState<TPage[]>([]);
  const { fetchAncestors } = usePageStore(storeType);
  useEffect(() => {
    if (!projectId || storeType !== EPageStoreType.PROJECT || !page.id) return;
    let active = true;
    setAncestors([]);
    void fetchAncestors(page.id)
      .then((nodes) => {
        if (active) setAncestors(nodes);
        return nodes;
      })
      .catch(() => {
        if (active) setAncestors([]);
      });
    return () => {
      active = false;
    };
  }, [fetchAncestors, page.id, projectId, storeType]);
  // translation
  const { t } = useTranslation();
  // refs
  const editorRef = useRef<EditorRefApi>(null);
  // derived values
  const {
    isContentEditable,
    editor: { setEditorRef },
  } = page;
  // page fallback
  const { isFetchingFallbackBinary } = usePageFallback({
    editorRef,
    fetchPageDescription: handlers.fetchDescriptionBinary,
    page,
    collaborationState,
    updatePageDescription: handlers.updateDescription,
  });

  const handleEditorReady = useCallback(
    (status: boolean) => {
      setEditorReady(status);
      if (editorRef.current && !page.editor.editorRef) {
        setEditorRef(editorRef.current);
      }
    },
    [page.editor.editorRef, setEditorRef]
  );

  useEffect(() => {
    const timer = setTimeout(() => setEditorRef(editorRef.current), 0);
    return () => clearTimeout(timer);
  }, [isContentEditable, setEditorRef]);

  // Get extensions and navigation logic from hook
  const {
    editorExtensionHandlers,
    navigationPaneExtensions,
    handleOpenNavigationPane,
    handleCloseNavigationPane,
    isNavigationPaneOpen,
  } = usePagesPaneExtensions({
    page,
    editorRef,
  });

  // Type-safe error handler for content too large errors
  const errorHandler: PageUpdateHandler<"error"> = (params) => {
    const { data } = params;

    // Check if it's content too large error
    if (data.error_code === "content_too_large") {
      setShowContentTooLargeBanner(true);
    }

    // Call original error handler if exists
    customRealtimeEventHandlers?.error?.(params);
  };

  const mergedCustomEventHandlers: TCustomEventHandlers = {
    ...customRealtimeEventHandlers,
    error: errorHandler,
  };

  // Get extended editor extensions configuration
  const extendedEditorProps = useExtendedEditorProps({
    workspaceSlug,
    page,
    storeType,
    fetchEntity: handlers.fetchEntity,
    getRedirectionLink: handlers.getRedirectionLink,
    extensionHandlers: editorExtensionHandlers,
    projectId,
  });

  const handleRestoreVersion = useCallback(
    async (descriptionHTML: string) => {
      editorRef.current?.clearEditor();
      editorRef.current?.setEditorValue(descriptionHTML);
    },
    [editorRef]
  );

  // reset editor ref on unmount
  useEffect(
    () => () => {
      setEditorRef(null);
    },
    [setEditorRef]
  );

  return (
    <div className="relative flex size-full overflow-hidden transition-all duration-300 ease-in-out">
      <div className="flex size-full flex-col overflow-hidden">
        <PageVersionsOverlay
          editorComponent={PagesVersionEditor}
          fetchVersionDetails={handlers.fetchVersionDetails}
          handleRestore={handleRestoreVersion}
          pageId={page.id ?? ""}
          restoreEnabled={isContentEditable}
          storeType={storeType}
        />
        {showContentTooLargeBanner && (
          <Banner placement="page" variant="warning" title={t("page_content_limit_banner.message")} />
        )}
        {projectId && storeType === EPageStoreType.PROJECT && (
          <nav
            aria-label={t("nested_pages.breadcrumb")}
            className="flex min-w-0 items-center gap-2 overflow-hidden px-page-x py-2 text-body-xs-regular"
          >
            <ControlLink href={`/${workspaceSlug}/projects/${projectId}/pages`}>
              {t("nested_pages.pages")}
            </ControlLink>
            {ancestors.map((ancestor) => (
              <span key={ancestor.id} className="flex min-w-0 items-center gap-2">
                <span aria-hidden="true">/</span>
                <ControlLink
                  className="truncate"
                  href={`/${workspaceSlug}/projects/${projectId}/pages/${ancestor.id}`}
                >
                  {ancestor.name}
                </ControlLink>
              </span>
            ))}
            <span aria-hidden="true">/</span>
            <span aria-current="page" className="truncate">
              {page.name}
            </span>
          </nav>
        )}
        {/* positioning context for the floating navigation pane button when the toolbar is hidden */}
        <div className="relative flex min-h-0 w-full flex-1 flex-col overflow-hidden">
          <PageEditorToolbarRoot
            handleOpenNavigationPane={handleOpenNavigationPane}
            isNavigationPaneOpen={isNavigationPaneOpen}
            page={page}
          />
          <PageEditorBody
            config={config}
            customRealtimeEventHandlers={mergedCustomEventHandlers}
            editorReady={editorReady}
            editorForwardRef={editorRef}
            handleEditorReady={handleEditorReady}
            handleOpenNavigationPane={handleOpenNavigationPane}
            handlers={handlers}
            isNavigationPaneOpen={isNavigationPaneOpen}
            page={page}
            projectId={projectId}
            storeType={storeType}
            webhookConnectionParams={webhookConnectionParams}
            workspaceSlug={workspaceSlug}
            extendedEditorProps={extendedEditorProps}
            isFetchingFallbackBinary={isFetchingFallbackBinary}
            onCollaborationStateChange={setCollaborationState}
          />
        </div>
      </div>
      <PageNavigationPaneRoot
        storeType={storeType}
        handleClose={handleCloseNavigationPane}
        isNavigationPaneOpen={isNavigationPaneOpen}
        page={page}
        versionHistory={{
          fetchAllVersions: handlers.fetchAllVersions,
          fetchVersionDetails: handlers.fetchVersionDetails,
        }}
        extensions={navigationPaneExtensions}
      />
    </div>
  );
});
