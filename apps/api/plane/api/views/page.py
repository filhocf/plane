# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Project-scoped Pages endpoints for API-key clients (not the session app API)."""

import base64
import hashlib
import hmac
import json
import requests

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response

from plane.api.views.base import BaseAPIView
from plane.app.permissions.base import ROLE
from plane.app.permissions.page import ProjectPagePermission
from plane.app.serializers.page import PageDetailSerializer, PageSerializer
from plane.app.views.page.base import ParentValidationError, _validate_parent_choice
from plane.bgtasks.page_transaction_task import page_transaction
from plane.db.models import Page, Project, ProjectMember, WorkspaceMember
from plane.utils.url import normalize_url_path
from plane.utils.content_validator import validate_binary_data, validate_html_content


class ExternalProjectPagePermission(ProjectPagePermission):
    """The app allows owners through before its role check; v1 writes do not."""

    def has_permission(self, request, view):
        page_id = view.kwargs.get("page_id")
        if page_id and request.method in ("GET", "HEAD", "OPTIONS"):
            # Object visibility is resolved by the endpoint so unknown, private,
            # and cross-project UUIDs all receive the same 404 response.
            view.kwargs.pop("page_id")
            try:
                allowed = super().has_permission(request, view)
            finally:
                view.kwargs["page_id"] = page_id
        else:
            allowed = super().has_permission(request, view)
        if not allowed:
            return False
        if not Project.objects.filter(
            pk=view.kwargs["project_id"], workspace__slug=view.kwargs["slug"], archived_at__isnull=True
        ).exists():
            return False
        if not WorkspaceMember.objects.filter(
            workspace__slug=view.kwargs["slug"], member=request.user, is_active=True
        ).exists():
            return False
        if request.method in ("POST", "PATCH", "DELETE"):
            return ProjectMember.objects.filter(
                workspace__slug=view.kwargs["slug"],
                project_id=view.kwargs["project_id"],
                member=request.user,
                role__in=(ROLE.ADMIN.value, ROLE.MEMBER.value),
                is_active=True,
            ).exists()
        return True


class ProjectPagesAPIEndpoint(BaseAPIView):
    permission_classes = [ExternalProjectPagePermission]
    serializer_class = PageDetailSerializer
    UNSUPPORTED_INPUTS = frozenset({"labels", "label_ids", "project_ids", "description_binary", "description_json"})

    @staticmethod
    def _document_formats(description_html):
        """Convert sanitized HTML to the binary document consumed by Plane live."""
        if not settings.LIVE_URL:
            return None
        try:
            response = requests.post(
                normalize_url_path(f"{settings.LIVE_URL}/convert-document/"),
                json={"description_html": description_html, "variant": "rich"},
                timeout=(3, 10),
            )
            if response.status_code != 200:
                return None
            formats = response.json()
        except (requests.RequestException, ValueError):
            return None
        if not formats or not formats.get("description_binary"):
            return None
        try:
            binary = base64.b64decode(formats["description_binary"], validate=True)
            if not validate_binary_data(binary)[0]:
                return None
            return {
                "description_json": formats["description_json"],
                "description_binary": binary,
            }
        except (KeyError, TypeError, ValueError):
            return None

    def get_queryset(self):
        return (
            Page.objects.filter(
                workspace__slug=self.kwargs["slug"],
                project_pages__project_id=self.kwargs["project_id"],
                project_pages__deleted_at__isnull=True,
                project_pages__project__archived_at__isnull=True,
            )
            .filter(Q(owned_by=self.request.user) | Q(access=Page.PUBLIC_ACCESS))
            .distinct()
        )

    def _parent(self, parent_id):
        if not parent_id:
            return None
        try:
            return self.get_queryset().get(pk=parent_id)
        except (Page.DoesNotExist, ValueError, TypeError, OverflowError):
            return None

    def _role(self):
        return ProjectMember.objects.filter(
            workspace__slug=self.kwargs["slug"],
            project_id=self.kwargs["project_id"],
            member=self.request.user,
            is_active=True,
        ).values_list("role", flat=True).first()

    def _visible_pages(self):
        pages = self.get_queryset()
        if self._role() == ROLE.GUEST.value and not Project.objects.filter(
            pk=self.kwargs["project_id"], guest_view_all_features=True
        ).exists():
            pages = pages.filter(owned_by=self.request.user)
        hidden_roots = Page.objects.filter(
            project_pages__project_id=self.kwargs["project_id"],
            project_pages__deleted_at__isnull=True,
            access=Page.PRIVATE_ACCESS,
        ).exclude(owned_by_id=self.request.user.id)
        hidden_ids = set()
        for page_id in hidden_roots.values_list("id", flat=True):
            hidden_ids.update(self._descendant_ids(page_id))
        return pages.exclude(id__in=hidden_ids)

    def _project_tree_lock(self):
        """Serialize every v1 hierarchy mutation in one project."""
        Project.objects.select_for_update().get(
            pk=self.kwargs["project_id"],
            workspace__slug=self.kwargs["slug"],
            archived_at__isnull=True,
        )

    @staticmethod
    def _delete_token(page, child_ids=()):
        """Bind destructive confirmation to the current hierarchy state."""
        state = {
            "id": str(page.id),
            "updated_at": page.updated_at.isoformat(),
            "archived_at": page.archived_at.isoformat() if page.archived_at else None,
            "parent_id": str(page.parent_id) if page.parent_id else None,
            "child_ids": sorted(str(child_id) for child_id in child_ids),
        }
        digest = hmac.new(
            settings.SECRET_KEY.encode(),
            json.dumps(state, sort_keys=True).encode(),
            hashlib.sha256,
        ).hexdigest()
        return f'"{digest}"'

    def _descendant_ids(self, page_id):
        ids, stack, seen = [], [page_id], set()
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            ids.append(current)
            stack.extend(
                Page.objects.filter(
                    parent_id=current,
                    project_pages__project_id=self.kwargs["project_id"],
                    project_pages__deleted_at__isnull=True,
                ).values_list("id", flat=True)
            )
        return ids

    def _lock_pages(self, page_ids):
        return list(Page.objects.select_for_update().filter(id__in=page_ids).order_by("id"))

    def _direct_child_ids(self, page_id):
        return Page.objects.filter(
            parent_id=page_id,
            project_pages__project_id=self.kwargs["project_id"],
            project_pages__deleted_at__isnull=True,
        ).values_list("id", flat=True)

    @staticmethod
    def _is_live_document_initialized(page):
        return bool(page.description_binary or page.description_html != "<p></p>")


class PageListCreateAPIEndpoint(ProjectPagesAPIEndpoint):
    def get(self, request, slug, project_id):
        # A guest without guest_view_all_features sees only pages they own.
        archived = request.query_params.get("archived", "false").lower() == "true"
        pages = self._visible_pages().filter(archived_at__isnull=not archived)
        if not archived:
            pages = pages.filter(parent__isnull=True)
        else:
            pages = self._visible_pages().filter(archived_at__isnull=False)
        query = request.query_params.get("query", "").strip()
        if query:
            pages = pages.filter(Q(name__icontains=query) | Q(description_html__icontains=query))
        pages = pages.order_by("created_at", "id")
        return self.paginate(
            request,
            queryset=pages,
            order_by="created_at",
            on_results=lambda results: PageDetailSerializer(results, many=True).data,
            default_per_page=100,
            max_per_page=100,
        )

    def post(self, request, slug, project_id):
        if not isinstance(request.data, dict):
            return Response({"error": "Expected a JSON object"}, status=status.HTTP_400_BAD_REQUEST)
        if self.UNSUPPORTED_INPUTS.intersection(request.data):
            return Response({"error": "Unsupported page field"}, status=status.HTTP_400_BAD_REQUEST)
        serializer = PageSerializer(
            data=request.data,
            context={
                "project_id": project_id,
                "owned_by_id": request.user.id,
                "description_json": {},
                "description_binary": None,
                "description_html": request.data.get("description_html", "<p></p>"),
            },
        )
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        # A nonempty document must not be saved as HTML-only: the editor reads Yjs binary.
        html = serializer.context["description_html"]
        if not isinstance(html, str):
            return Response({"description_html": "Expected a string"}, status=status.HTTP_400_BAD_REQUEST)
        if not html:
            return Response({"description_html": "Expected nonempty HTML"}, status=status.HTTP_400_BAD_REQUEST)
        valid, error, sanitized = validate_html_content(html)
        if not valid:
            return Response({"description_html": error}, status=status.HTTP_400_BAD_REQUEST)
        serializer.context.update(
            {
                "project_id": project_id,
                "owned_by_id": request.user.id,
                "description_json": {},
                "description_binary": None,
                "description_html": sanitized if sanitized is not None else html,
            }
        )
        # The live service can initialise an empty document from HTML on first open.
        has_content = "description_html" in request.data and serializer.context["description_html"] != "<p></p>"
        formats = self._document_formats(serializer.context["description_html"]) if has_content else None
        if has_content and formats is None:
            return Response({"error": "Document conversion unavailable"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        if formats:
            serializer.context.update(formats)
        # The serializer creates exactly one Page and its ProjectPage link.
        with transaction.atomic():
            self._project_tree_lock()
            parent_id = request.data.get("parent")
            if parent_id is not None:
                parent = self._visible_pages().filter(pk=parent_id).first()
                if parent is None:
                    return Response(
                        {"error": "Parent page not found in this project"}, status=status.HTTP_400_BAD_REQUEST
                    )
                try:
                    _validate_parent_choice(parent, None, project_id)
                except ParentValidationError as exc:
                    return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
            page = serializer.save(created_by=request.user, updated_by=request.user)
        page_transaction.delay(
            new_description_html=page.description_html, old_description_html=None, page_id=page.id
        )
        return Response(PageDetailSerializer(page).data, status=status.HTTP_201_CREATED)


class PageDetailAPIEndpoint(ProjectPagesAPIEndpoint):
    def get(self, request, slug, project_id, page_id):
        page = self._visible_pages().filter(pk=page_id).first()
        if page is None:
            return Response({"error": "Page not found"}, status=status.HTTP_404_NOT_FOUND)
        response = Response(PageDetailSerializer(page).data)
        if page.archived_at is not None:
            response["ETag"] = self._delete_token(page, self._direct_child_ids(page.id))
        return response

    def patch(self, request, slug, project_id, page_id):
        if not isinstance(request.data, dict):
            return Response({"error": "Expected a JSON object"}, status=status.HTTP_400_BAD_REQUEST)
        if self.UNSUPPORTED_INPUTS.intersection(request.data):
            return Response({"error": "Unsupported page field"}, status=status.HTTP_400_BAD_REQUEST)
        page = self._visible_pages().filter(pk=page_id).first()
        if page is None:
            return Response({"error": "Page not found"}, status=status.HTTP_404_NOT_FOUND)
        if page.is_locked:
            return Response({"error": "Page is locked"}, status=status.HTTP_400_BAD_REQUEST)
        if "description_html" in request.data and self._is_live_document_initialized(page):
            return Response(
                {"error": "Cannot replace content of an initialized live document"},
                status=status.HTTP_409_CONFLICT,
            )

        if "access" in request.data and page.access != request.data["access"] and page.owned_by_id != request.user.id:
            return Response({"error": "Only the owner can change page access"}, status=status.HTTP_400_BAD_REQUEST)

        serializer = PageDetailSerializer(page, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        previous_html = page.description_html
        formats = None
        has_html = "description_html" in serializer.validated_data
        if has_html:
            formats = self._document_formats(serializer.validated_data["description_html"])
            if formats is None:
                return Response(
                    {"error": "Document conversion unavailable"}, status=status.HTTP_503_SERVICE_UNAVAILABLE
                )
        if has_html:
            # The live writer also updates this row. Recheck under its row lock
            # after conversion, then save all representations in the same write.
            with transaction.atomic():
                self._project_tree_lock()
                current = Page.objects.select_for_update().get(pk=page.pk)
                if current.is_locked:
                    return Response({"error": "Page is locked"}, status=status.HTTP_400_BAD_REQUEST)
                if self._is_live_document_initialized(current):
                    return Response(
                        {"error": "Cannot replace content of an initialized live document"},
                        status=status.HTTP_409_CONFLICT,
                    )
                parent = serializer.validated_data.get("parent")
                if parent is not None:
                    parent = self._visible_pages().filter(pk=parent.pk).first()
                    if parent is None:
                        return Response(
                            {"error": "Parent page not found in this project"},
                            status=status.HTTP_400_BAD_REQUEST,
                        )
                    try:
                        _validate_parent_choice(parent, current, project_id)
                    except ParentValidationError as exc:
                        return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
                serializer.instance = current
                page = serializer.save(updated_by=request.user, **formats)
        else:
            with transaction.atomic():
                self._project_tree_lock()
                current = Page.objects.select_for_update().get(pk=page.pk)
                if current.is_locked:
                    return Response({"error": "Page is locked"}, status=status.HTTP_400_BAD_REQUEST)
                changes = dict(serializer.validated_data)
                parent_id = changes.get("parent")
                if parent_id is not None:
                    parent = self._visible_pages().filter(pk=parent_id.pk).first()
                    if parent is None:
                        return Response(
                            {"error": "Parent page not found in this project"},
                            status=status.HTTP_400_BAD_REQUEST,
                        )
                    try:
                        _validate_parent_choice(parent, current, project_id)
                    except ParentValidationError as exc:
                        return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
                if changes:
                    Page.objects.filter(pk=current.pk).update(**changes, updated_by=request.user)
                    page.refresh_from_db()
        if has_html:
            page_transaction.delay(
                new_description_html=page.description_html, old_description_html=previous_html, page_id=page.id
            )
        return Response(serializer.data)

    def delete(self, request, slug, project_id, page_id):
        with transaction.atomic():
            self._project_tree_lock()
            allowed = self.get_queryset().filter(pk=page_id).values_list("pk", flat=True).first()
            page = Page.objects.select_for_update().filter(pk=allowed).first() if allowed else None
            if page is None:
                return Response({"error": "Page not found"}, status=status.HTTP_404_NOT_FOUND)
            if page.archived_at is None:
                return Response(
                    {"error": "The page should be archived before deleting"},
                    status=status.HTTP_409_CONFLICT,
                )
            child_ids = list(self._direct_child_ids(page.id))
            supplied = request.headers.get("If-Match")
            expected = self._delete_token(page, child_ids)
            if not supplied:
                return Response(
                    {"error": "Deletion confirmation required", "delete_token": expected},
                    status=428,
                )
            if supplied != expected:
                return Response({"error": "Page state changed"}, status=status.HTTP_409_CONFLICT)
            self._lock_pages(child_ids)
            if set(child_ids) != set(self._direct_child_ids(page.id)):
                return Response({"error": "Page state changed"}, status=status.HTTP_409_CONFLICT)
            Page.objects.filter(id__in=child_ids).update(parent=None)
            page.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class PageArchiveAPIEndpoint(ProjectPagesAPIEndpoint):
    def post(self, request, slug, project_id, page_id):
        with transaction.atomic():
            self._project_tree_lock()
            allowed = self.get_queryset().filter(pk=page_id).values_list("pk", flat=True).first()
            page = Page.objects.select_for_update().filter(pk=allowed).first() if allowed else None
            if page is None:
                return Response({"error": "Page not found"}, status=status.HTTP_404_NOT_FOUND)
            ids = self._descendant_ids(page.id)
            self._lock_pages(ids)
            ids = self._descendant_ids(page.id)
            if Page.objects.filter(id__in=ids, is_locked=True).exists():
                return Response({"error": "A page in the subtree is locked"}, status=status.HTTP_409_CONFLICT)
            Page.objects.filter(id__in=ids).update(archived_at=timezone.now().date())
        return Response(
            {"archived_at": str(timezone.now()), "affected_page_ids": [str(page_id) for page_id in ids]},
            status=status.HTTP_200_OK,
        )

    def delete(self, request, slug, project_id, page_id):
        with transaction.atomic():
            self._project_tree_lock()
            allowed = self.get_queryset().filter(pk=page_id).values_list("pk", flat=True).first()
            page = Page.objects.select_for_update().filter(pk=allowed).first() if allowed else None
            if page is None:
                return Response({"error": "Page not found"}, status=status.HTTP_404_NOT_FOUND)
            ids = self._descendant_ids(page.id)
            self._lock_pages(ids)
            ids = self._descendant_ids(page.id)
            if Page.objects.filter(id__in=ids, is_locked=True).exists():
                return Response({"error": "A page in the subtree is locked"}, status=status.HTTP_409_CONFLICT)
            if page.parent_id and page.parent.archived_at:
                page.parent = None
                page.save(update_fields=["parent", "updated_at"])
            Page.objects.filter(id__in=ids).update(archived_at=None)
        return Response(
            {
                "restored_page_ids": [str(page_id) for page_id in ids],
                "parent_id": str(page.parent_id) if page.parent_id else None,
            },
            status=status.HTTP_200_OK,
        )


class PageChildrenAPIEndpoint(ProjectPagesAPIEndpoint):
    def get(self, request, slug, project_id, page_id):
        parent = self.get_queryset().filter(pk=page_id).first()
        if parent is None:
            return Response({"error": "Page not found"}, status=status.HTTP_404_NOT_FOUND)
        pages = self._visible_pages().filter(parent_id=page_id, archived_at__isnull=True)
        return Response(PageDetailSerializer(pages, many=True).data)
