# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""External Pages API: project-scoped, API-key-authenticated contract."""

import base64
import uuid
from unittest.mock import patch

import pytest
import requests
from django.utils import timezone
from rest_framework import status

from plane.db.models import Page, Project, ProjectMember, ProjectPage, User, WorkspaceMember


def _url(workspace, project):
    return f"/api/v1/workspaces/{workspace.slug}/projects/{project.id}/pages/"


def _project(workspace, user, role=20):
    suffix = uuid.uuid4().hex[:8].upper()
    project = Project.objects.create(name=f"Pages API {suffix}", identifier=suffix[:5], workspace=workspace)
    ProjectMember.objects.create(workspace=workspace, project=project, member=user, role=role, is_active=True)
    return project


def _page(workspace, project, user, name="Root", parent=None, access=0):
    page = Page.objects.create(
        workspace=workspace, owned_by=user, created_by=user, name=name, parent=parent, access=access
    )
    ProjectPage.objects.create(workspace=workspace, project=project, page=page)
    return page


def _other_user():
    return User.objects.create(email=f"other-{uuid.uuid4().hex[:8]}@plane.so", username=f"other-{uuid.uuid4().hex[:8]}")


@pytest.mark.contract
@pytest.mark.django_db
class TestExternalPages:
    def test_post_with_html_persists_editor_binary(self, api_key_client, workspace, create_user):
        project = _project(workspace, create_user)
        binary = base64.b64encode(b"yjs-document").decode()
        with patch("plane.api.views.page.settings.LIVE_URL", "http://live/live/"), patch(
            "plane.api.views.page.requests.post"
        ) as convert:
            convert.return_value.status_code = 200
            convert.return_value.json.return_value = {"description_json": {"type": "doc"}, "description_binary": binary}
            response = api_key_client.post(_url(workspace, project), {
                "name": "With content", "description_html": "<p>safe</p><script>bad()</script>",
            }, format="json")
        assert response.status_code == 201
        page = Page.objects.get(pk=response.data["id"])
        assert page.description_html == "<p>safe</p>"
        assert page.description_binary == b"yjs-document"
        assert page.description_json == {"type": "doc"}
        convert.assert_called_once_with(
            "http://live/live/convert-document/",
            json={"description_html": "<p>safe</p>", "variant": "rich"}, timeout=(3, 10),
        )

    def test_html_patch_converts_atomically_or_returns_503(self, api_key_client, workspace, create_user):
        project = _project(workspace, create_user)
        page = _page(workspace, project, create_user)
        url = _url(workspace, project) + f"{page.id}/"
        with patch("plane.api.views.page.settings.LIVE_URL", "http://live/live/"), patch(
            "plane.api.views.page.requests.post", side_effect=requests.Timeout
        ):
            failed = api_key_client.patch(url, {"name": "Changed", "description_html": "<p>new</p>"}, format="json")
        assert failed.status_code == 503
        page.refresh_from_db()
        assert page.name == "Root"
        with patch("plane.api.views.page.ProjectPagesAPIEndpoint._document_formats", return_value={
            "description_json": {"type": "doc"}, "description_binary": b"yjs-patch",
        }):
            updated = api_key_client.patch(url, {"description_html": "<p>new</p>"}, format="json")
        assert updated.status_code == 200
        page.refresh_from_db()
        assert page.description_html == "<p>new</p>"
        assert page.description_binary == b"yjs-patch"

    def test_html_patch_rejects_initialized_live_document(self, api_key_client, workspace, create_user):
        project = _project(workspace, create_user)
        page = _page(workspace, project, create_user)
        page.description_html = "<p>old</p>"
        page.description_binary = b"already-live"
        page.save(update_fields=["description_html", "description_binary"])
        with patch("plane.api.views.page.ProjectPagesAPIEndpoint._document_formats") as convert:
            response = api_key_client.patch(
                _url(workspace, project) + f"{page.id}/",
                {"name": "New name", "description_html": "<p>new</p>"}, format="json",
            )
        assert response.status_code == 409
        convert.assert_not_called()
        page.refresh_from_db()
        assert page.name == "Root"
        assert page.description_html == "<p>old</p>"
        assert page.description_binary == b"already-live"

    def test_direct_binary_and_json_inputs_are_rejected(self, api_key_client, workspace, create_user):
        project = _project(workspace, create_user)
        url = _url(workspace, project)
        assert api_key_client.post(
            url, {"name": "Bad", "description_binary": "arbitrary"}, format="json"
        ).status_code == 400
        assert not Page.objects.filter(name="Bad").exists()
        page = _page(workspace, project, create_user)
        assert api_key_client.patch(
            url + f"{page.id}/", {"description_json": {"evil": True}}, format="json"
        ).status_code == 400
        page.refresh_from_db()
        assert page.description_json == {}

    def test_archived_project_cannot_publish_or_read(self, api_key_client, workspace, create_user):
        project = _project(workspace, create_user)
        page = _page(workspace, project, create_user)
        project.archived_at = timezone.now()
        project.save(update_fields=["archived_at"])
        base = _url(workspace, project)
        assert api_key_client.get(base).status_code in (403, 404)
        assert api_key_client.get(base + f"{page.id}/").status_code in (403, 404)
        assert api_key_client.post(base, {"name": "Archived write"}, format="json").status_code in (403, 404)
        assert api_key_client.patch(base + f"{page.id}/", {"name": "Changed"}, format="json").status_code in (403, 404)
        assert not Page.objects.filter(name="Archived write").exists()

    def test_archived_pages_not_reused_by_listing(self, api_key_client, workspace, create_user):
        project = _project(workspace, create_user)
        root = _page(workspace, project, create_user)
        hidden = _page(workspace, project, create_user, "Archived", root)
        hidden.archived_at = timezone.now().date()
        hidden.save(update_fields=["archived_at"])
        assert not any(p["name"] == "Archived" for p in api_key_client.get(
            _url(workspace, project) + f"{root.id}/children/"
        ).data)

    def test_reject_foreign_labels_and_malformed_bodies(self, api_key_client, workspace, create_user):
        project = _project(workspace, create_user)
        base = _url(workspace, project)
        assert api_key_client.post(
            base, {"name": "Bad", "labels": [str(uuid.uuid4())]}, format="json"
        ).status_code == 400
        assert api_key_client.post(base, {"name": "Bad", "description_html": 123}, format="json").status_code == 400
        assert api_key_client.post(base, {"name": "Bad", "description_html": ""}, format="json").status_code == 400
        assert api_key_client.post(
            base, {"name": "Bad", "project_ids": [str(project.id)]}, format="json"
        ).status_code == 400
        assert api_key_client.post(base, {"name": "Bad", "label_ids": []}, format="json").status_code == 400
        assert api_key_client.post(base, [{"name": "Bad"}], format="json").status_code == 400
        page = _page(workspace, project, create_user)
        assert api_key_client.patch(
            base + f"{page.id}/", {"labels": [str(uuid.uuid4())]}, format="json"
        ).status_code == 400
        assert api_key_client.patch(
            base + f"{page.id}/", {"project_ids": [str(project.id)]}, format="json"
        ).status_code == 400
        assert api_key_client.patch(base + f"{page.id}/", {"label_ids": []}, format="json").status_code == 400
        assert api_key_client.patch(base + f"{page.id}/", [{"name": "Bad"}], format="json").status_code == 400
        assert not Page.objects.filter(name="Bad").exists()

    def test_live_initialization_between_conversion_and_write_returns_409(self, api_key_client, workspace, create_user):
        project = _project(workspace, create_user)
        page = _page(workspace, project, create_user)

        def live_converts_then_initializes(_html):
            Page.objects.filter(pk=page.pk).update(description_html="<p>live</p>", description_binary=b"from-live")
            return {"description_json": {"type": "doc"}, "description_binary": b"from-api"}

        with patch(
            "plane.api.views.page.ProjectPagesAPIEndpoint._document_formats",
            side_effect=live_converts_then_initializes,
        ):
            response = api_key_client.patch(
                _url(workspace, project) + f"{page.id}/",
                {"name": "Not updated", "description_html": "<p>api</p>"}, format="json",
            )
        assert response.status_code == 409
        page.refresh_from_db()
        assert (page.name, page.description_html, page.description_binary) == ("Root", "<p>live</p>", b"from-live")

    def test_metadata_patch_does_not_rewrite_live_binary(self, api_key_client, workspace, create_user):
        project = _project(workspace, create_user)
        page = _page(workspace, project, create_user)
        from django.db.models.query import QuerySet

        original_update = QuerySet.update

        def concurrent_live_write(queryset, **kwargs):
            if queryset.model is Page and kwargs.get("name") == "Renamed":
                original_update(Page.objects.filter(pk=page.pk), description_binary=b"new-from-live")
            return original_update(queryset, **kwargs)

        with patch.object(QuerySet, "update", concurrent_live_write):
            response = api_key_client.patch(
                _url(workspace, project) + f"{page.id}/", {"name": "Renamed"}, format="json"
            )
        assert response.status_code == 200
        page.refresh_from_db()
        assert page.name == "Renamed"
        assert page.description_binary == b"new-from-live"

    def test_key_creates_child_and_reads_node_roots_and_children(self, api_key_client, workspace, create_user):
        project = _project(workspace, create_user)
        root = api_key_client.post(_url(workspace, project), {"name": "Root"}, format="json")
        assert root.status_code == status.HTTP_201_CREATED
        child = api_key_client.post(
            _url(workspace, project), {"name": "Child", "parent": str(root.data["id"])}, format="json"
        )
        assert child.status_code == status.HTTP_201_CREATED
        assert str(child.data["parent"]) == str(root.data["id"])
        detail = api_key_client.get(_url(workspace, project) + f'{child.data["id"]}/')
        assert detail.status_code == status.HTTP_200_OK
        roots = api_key_client.get(_url(workspace, project)).data["results"]
        assert [str(p["id"]) for p in roots] == [str(root.data["id"])]
        children = api_key_client.get(_url(workspace, project) + f'{root.data["id"]}/children/')
        assert children.status_code == status.HTTP_200_OK
        assert [str(p["id"]) for p in children.data] == [str(child.data["id"])]

    def test_no_key_cannot_create(self, api_client, workspace, create_user):
        project = _project(workspace, create_user)
        response = api_client.post(_url(workspace, project), {"name": "No key"}, format="json")
        assert response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)
        assert not Page.objects.filter(name="No key").exists()

    def test_invalid_key_and_inactive_user_cannot_read(self, api_client, api_token, workspace, create_user):
        project = _project(workspace, create_user)
        root = _page(workspace, project, create_user)
        url = _url(workspace, project) + f"{root.id}/"
        api_client.credentials(HTTP_X_API_KEY="invalid-key")
        assert api_client.get(url).status_code in (401, 403)
        api_client.credentials(HTTP_X_API_KEY=api_token.token)
        create_user.is_active = False
        create_user.save(update_fields=["is_active"])
        assert api_client.get(url).status_code in (401, 403)

    def test_guest_cannot_write_but_can_read_owned_page(self, api_key_client, workspace, create_user):
        project = _project(workspace, create_user, role=5)
        root = _page(workspace, project, create_user)
        base = _url(workspace, project)
        assert api_key_client.get(base + f"{root.id}/").status_code == 200
        assert api_key_client.post(base, {"name": "Guest write"}, format="json").status_code == 403
        assert api_key_client.patch(base + f"{root.id}/", {"name": "Guest rename"}, format="json").status_code == 403
        root.refresh_from_db()
        assert root.name == "Root"

    def test_other_members_private_page_not_listed_or_read(self, api_key_client, workspace, create_user):
        owner = _other_user()
        project = _project(workspace, create_user, role=15)
        private = _page(workspace, project, owner, "Secret", access=Page.PRIVATE_ACCESS)
        public = _page(workspace, project, owner, "Visible")
        base = _url(workspace, project)
        response = api_key_client.get(base)
        assert response.status_code == 200
        pages = response.data if isinstance(response.data, list) else response.data["results"]
        assert str(public.id) in {str(p["id"]) for p in pages}
        assert str(private.id) not in {str(p["id"]) for p in pages}
        assert api_key_client.get(base + f"{private.id}/").status_code in (403, 404)

    def test_other_member_cannot_change_visibility(self, api_key_client, workspace, create_user):
        owner = _other_user()
        project = _project(workspace, create_user, role=15)
        public = _page(workspace, project, owner)
        response = api_key_client.patch(
            _url(workspace, project) + f"{public.id}/", {"access": Page.PRIVATE_ACCESS}, format="json"
        )
        assert response.status_code == 400
        public.refresh_from_db()
        assert public.access == Page.PUBLIC_ACCESS

    def test_parent_from_another_project_rejected_before_write(self, api_key_client, workspace, create_user):
        source = _project(workspace, create_user)
        target = _project(workspace, create_user)
        other = _page(workspace, source, create_user)
        response = api_key_client.post(
            _url(workspace, target), {"name": "Escaped", "parent": str(other.id)}, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not Page.objects.filter(name="Escaped").exists()

    def test_unknown_parent_rejected_before_write(self, api_key_client, workspace, create_user):
        project = _project(workspace, create_user)
        response = api_key_client.post(
            _url(workspace, project), {"name": "Orphan", "parent": str(uuid.uuid4())}, format="json"
        )
        assert response.status_code == 400
        assert not Page.objects.filter(name="Orphan").exists()

    def test_post_rejects_depth_six(self, api_key_client, workspace, create_user):
        project = _project(workspace, create_user)
        node = _page(workspace, project, create_user, "L0")
        for i in range(1, 6):
            node = _page(workspace, project, create_user, f"L{i}", parent=node)
        response = api_key_client.post(
            _url(workspace, project), {"name": "Too deep", "parent": str(node.id)}, format="json"
        )
        assert response.status_code == 400
        assert not Page.objects.filter(name="Too deep").exists()

    def test_locked_page_cannot_be_updated(self, api_key_client, workspace, create_user):
        project = _project(workspace, create_user)
        root = _page(workspace, project, create_user)
        root.is_locked = True
        root.save(update_fields=["is_locked"])
        response = api_key_client.patch(_url(workspace, project) + f"{root.id}/", {"name": "Changed"}, format="json")
        assert response.status_code == 400
        root.refresh_from_db()
        assert root.name == "Root"

    def test_patch_title_and_sanitized_description_persist(self, api_key_client, workspace, create_user):
        project = _project(workspace, create_user)
        response = api_key_client.post(_url(workspace, project), {"name": "Initial"}, format="json")
        assert response.status_code == 201
        page_id = response.data["id"]
        with patch("plane.api.views.page.ProjectPagesAPIEndpoint._document_formats", return_value={
            "description_json": {"type": "doc"}, "description_binary": b"yjs-document",
        }):
            result = api_key_client.patch(
                _url(workspace, project) + f"{page_id}/",
                {"name": "Published", "description_html": '<p onclick="alert(1)">Updated</p>'}, format="json",
            )
        assert result.status_code == 200
        persisted = Page.objects.get(pk=page_id)
        assert persisted.name == "Published"
        assert "Updated" in persisted.description_html
        assert "onclick" not in persisted.description_html
        assert "<script" not in persisted.description_html

    def test_self_parent_and_cycle_rejected(self, api_key_client, workspace, create_user):
        project = _project(workspace, create_user)
        root = _page(workspace, project, create_user)
        child = _page(workspace, project, create_user, "Child", root)
        base = _url(workspace, project)
        assert api_key_client.patch(base + f"{root.id}/", {"parent": str(root.id)}, format="json").status_code == 400
        assert api_key_client.patch(base + f"{root.id}/", {"parent": str(child.id)}, format="json").status_code == 400
        root.refresh_from_db()
        assert root.parent_id is None

    def test_inactive_project_member_cannot_read_or_write(self, api_key_client, workspace, create_user):
        project = _project(workspace, create_user)
        root = _page(workspace, project, create_user)
        ProjectMember.objects.filter(project=project, member=create_user).update(is_active=False)
        base = _url(workspace, project)
        assert api_key_client.get(base + f"{root.id}/").status_code in (403, 404)
        assert api_key_client.post(base, {"name": "Forbidden"}, format="json").status_code in (403, 404)
        assert not Page.objects.filter(name="Forbidden").exists()

    def test_inactive_workspace_member_cannot_read(self, api_key_client, workspace, create_user):
        project = _project(workspace, create_user)
        root = _page(workspace, project, create_user)
        WorkspaceMember.objects.filter(workspace=workspace, member=create_user).update(is_active=False)
        assert api_key_client.get(_url(workspace, project) + f"{root.id}/").status_code in (403, 404)

    def test_cross_project_page_cannot_be_read_or_patched(self, api_key_client, workspace, create_user):
        source = _project(workspace, create_user)
        target = _project(workspace, create_user)
        root = _page(workspace, source, create_user)
        url = _url(workspace, target) + f"{root.id}/"
        assert api_key_client.get(url).status_code in (403, 404)
        assert api_key_client.patch(url, {"name": "Cross project"}, format="json").status_code in (403, 404)
        root.refresh_from_db()
        assert root.name == "Root"

    def test_guest_cannot_list_other_members_public_pages(self, api_key_client, workspace, create_user):
        project = _project(workspace, create_user, role=5)
        other = _other_user()
        root = _page(workspace, project, other, "Other member page")
        response = api_key_client.get(_url(workspace, project))
        assert response.status_code == 200
        pages = response.data if isinstance(response.data, list) else response.data["results"]
        assert str(root.id) not in {str(p["id"]) for p in pages}

    def test_children_hide_private_siblings(self, api_key_client, workspace, create_user):
        project = _project(workspace, create_user)
        root = _page(workspace, project, create_user)
        private = _page(workspace, project, _other_user(), "Hidden", parent=root, access=Page.PRIVATE_ACCESS)
        public = _page(workspace, project, _other_user(), "Shown", parent=root)
        response = api_key_client.get(_url(workspace, project) + f"{root.id}/children/")
        assert response.status_code == 200
        assert {str(item["id"]) for item in response.data} == {str(public.id)}
        assert str(private.id) not in {str(item["id"]) for item in response.data}

    def test_soft_deleted_project_link_cannot_read(self, api_key_client, workspace, create_user):
        project = _project(workspace, create_user)
        root = _page(workspace, project, create_user)
        ProjectPage.objects.filter(page=root, project=project).update(deleted_at=timezone.now())
        assert api_key_client.get(_url(workspace, project) + f"{root.id}/").status_code in (403, 404)

    def test_api_key_does_not_authorize_session_only_app_route(self, api_key_client, workspace, create_user):
        project = _project(workspace, create_user)
        response = api_key_client.get(f"/api/workspaces/{workspace.slug}/projects/{project.id}/pages/")
        assert response.status_code in (401, 403)
