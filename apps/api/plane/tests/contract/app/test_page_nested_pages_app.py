# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Nested pages contract: a child page must be creatable, retrievable, updateable
and archivable like any root page. Parent must reject self-parenting, cycles,
cross-project adoption and nesting beyond the depth limit. Deletion promotes
direct children to roots (decided semantics; contradicts the on_delete=CASCADE
default and is intentional here).
"""

import uuid

import pytest
from rest_framework import status

from plane.db.models import Page, Project, ProjectMember, ProjectPage


def _urls(slug, project_id):
    return f"/api/workspaces/{slug}/projects/{project_id}/pages/"


def _make_page(workspace, project, owner, name="Page", parent=None):
    page = Page.objects.create(workspace=workspace, created_by=owner, owned_by=owner, name=name, parent=parent)
    ProjectPage.objects.create(workspace=workspace, project=project, page=page)
    return page


def _member(workspace, project, user, role=20):
    ProjectMember.objects.get_or_create(
        workspace=workspace, project=project, member=user, defaults={"role": role, "is_active": True}
    )


@pytest.mark.contract
class TestPageNestedCreate:
    @pytest.mark.django_db
    def test_create_root(self, session_client, workspace, create_user):
        project = Project.objects.create(name="P", identifier="NPG", workspace=workspace)
        _member(workspace, project, create_user)
        url = _urls(workspace.slug, project.id)

        response = session_client.post(url, {"name": "Root"}, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["parent"] is None

    @pytest.mark.django_db
    def test_create_child_returns_201_and_persists(self, session_client, workspace, create_user):
        """POST of a child must return 201 (the current build returns 404 after
        persisting the row because create() reuses the roots-only queryset)."""
        project = Project.objects.create(name="P", identifier="NPC", workspace=workspace)
        _member(workspace, project, create_user)
        root = _make_page(workspace, project, create_user, "Root")
        url = _urls(workspace.slug, project.id)

        response = session_client.post(url, {"name": "Child", "parent": str(root.id)}, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["parent"] == root.id
        child = Page.objects.get(name="Child", workspace=workspace)
        assert child.parent_id == root.id

    @pytest.mark.django_db
    def test_create_child_rejects_unknown_parent(self, session_client, workspace, create_user):
        project = Project.objects.create(name="P", identifier="NPX", workspace=workspace)
        _member(workspace, project, create_user)
        url = _urls(workspace.slug, project.id)

        response = session_client.post(url, {"name": "Child", "parent": str(uuid.uuid4())}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not Page.objects.filter(name="Child", workspace=workspace).exists()

    @pytest.mark.django_db
    def test_create_child_rejects_parent_other_project(self, session_client, workspace, create_user):
        """Parent must live in the same project: cross-project adoption leaks
        pages across project boundaries."""
        project_a = Project.objects.create(name="A", identifier="NAA", workspace=workspace)
        project_b = Project.objects.create(name="B", identifier="NBB", workspace=workspace)
        _member(workspace, project_a, create_user)
        _member(workspace, project_b, create_user)
        root_b = _make_page(workspace, project_b, create_user, "Root B")
        url = _urls(workspace.slug, project_a.id)

        response = session_client.post(url, {"name": "Child", "parent": str(root_b.id)}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not Page.objects.filter(name="Child", workspace=workspace).exists()


@pytest.mark.contract
class TestPageNestedRetrieve:
    @pytest.mark.django_db
    def test_retrieve_child(self, session_client, workspace, create_user):
        """A child must be retrievable by id (the roots-only queryset hides it)."""
        project = Project.objects.create(name="P", identifier="NRR", workspace=workspace)
        _member(workspace, project, create_user)
        root = _make_page(workspace, project, create_user, "Root")
        child = _make_page(workspace, project, create_user, "Child", parent=root)

        response = session_client.get(f"{_urls(workspace.slug, project.id)}{child.id}/?track_visit=false")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["parent"] == root.id

    @pytest.mark.django_db
    def test_list_returns_roots_only(self, session_client, workspace, create_user):
        """The list endpoint stays roots-only: children surface through the tree
        endpoint, not the flat list."""
        project = Project.objects.create(name="P", identifier="NLR", workspace=workspace)
        _member(workspace, project, create_user)
        root = _make_page(workspace, project, create_user, "Root")
        _make_page(workspace, project, create_user, "Child", parent=root)
        url = _urls(workspace.slug, project.id)

        response = session_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        # The view returns a plain list (not a paginated dict); ids come back as
        # UUID instances via the test client
        pages = response.data if isinstance(response.data, list) else response.data["results"]
        ids = [str(p["id"]) for p in pages]
        assert str(root.id) in ids
        assert all(p["parent"] is None for p in pages)


@pytest.mark.contract
class TestPageNestedUpdate:
    @pytest.mark.django_db
    def test_patch_rejects_self_parent(self, session_client, workspace, create_user):
        project = Project.objects.create(name="P", identifier="NSP", workspace=workspace)
        _member(workspace, project, create_user)
        root = _make_page(workspace, project, create_user, "Root")

        response = session_client.patch(
            f"{_urls(workspace.slug, project.id)}{root.id}/", {"parent": str(root.id)}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        root.refresh_from_db()
        assert root.parent_id is None

    @pytest.mark.django_db
    def test_patch_rejects_cycle(self, session_client, workspace, create_user):
        project = Project.objects.create(name="P", identifier="NCY", workspace=workspace)
        _member(workspace, project, create_user)
        root = _make_page(workspace, project, create_user, "Root")
        child = _make_page(workspace, project, create_user, "Child", parent=root)

        response = session_client.patch(
            f"{_urls(workspace.slug, project.id)}{root.id}/", {"parent": str(child.id)}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        root.refresh_from_db()
        assert root.parent_id is None  # unchanged

    @pytest.mark.django_db
    def test_patch_rejects_nesting_beyond_depth_limit(self, session_client, workspace, create_user):
        """Depth guard: MAX_PAGE_DEPTH bounds the deepest node. A page may sit at
        depth 5 (= MAX_PAGE_DEPTH, five ancestors); depth 6 is rejected."""
        project = Project.objects.create(name="P", identifier="NDP", workspace=workspace)
        _member(workspace, project, create_user)
        node = _make_page(workspace, project, create_user, "L0")
        for i in range(1, 5):  # L1..L4; L4 sits at depth 4
            node = _make_page(workspace, project, create_user, f"L{i}", parent=node)
        l4 = Page.objects.get(name="L4", workspace=workspace)
        l5 = _make_page(workspace, project, create_user, "L5", parent=l4)  # depth 5
        fresh_root = _make_page(workspace, project, create_user, "Fresh")
        url = f"{_urls(workspace.slug, project.id)}{fresh_root.id}/"

        # Fresh under L4: depth 5 = MAX_PAGE_DEPTH → accepted
        ok = session_client.patch(url, {"parent": str(l4.id)}, format="json")
        # Fresh under L5: depth 6 → rejected
        response = session_client.patch(url, {"parent": str(l5.id)}, format="json")

        assert ok.status_code == status.HTTP_200_OK
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.contract
class TestPageNestedArchive:
    @pytest.mark.django_db
    def test_archive_cascades_to_descendants(self, session_client, workspace, create_user):
        project = Project.objects.create(name="P", identifier="NAC", workspace=workspace)
        _member(workspace, project, create_user)
        root = _make_page(workspace, project, create_user, "Root")
        child = _make_page(workspace, project, create_user, "Child", parent=root)

        response = session_client.post(f"{_urls(workspace.slug, project.id)}{root.id}/archive/")

        assert response.status_code == status.HTTP_200_OK
        child.refresh_from_db()
        assert child.archived_at is not None

    @pytest.mark.django_db
    def test_archive_reaches_grandchildren(self, session_client, workspace, create_user):
        project = Project.objects.create(name="P", identifier="NAD", workspace=workspace)
        _member(workspace, project, create_user)
        root = _make_page(workspace, project, create_user, "Root")
        child = _make_page(workspace, project, create_user, "Child", parent=root)
        grandchild = _make_page(workspace, project, create_user, "Grandchild", parent=child)

        response = session_client.post(f"{_urls(workspace.slug, project.id)}{root.id}/archive/")

        assert response.status_code == status.HTTP_200_OK
        grandchild.refresh_from_db()
        assert grandchild.archived_at is not None


@pytest.mark.contract
class TestPageNestedDelete:
    @pytest.mark.django_db
    def test_delete_promotes_children_to_roots(self, session_client, workspace, create_user):
        project = Project.objects.create(name="P", identifier="NDC", workspace=workspace)
        _member(workspace, project, create_user)
        root = _make_page(workspace, project, create_user, "Root")
        child = _make_page(workspace, project, create_user, "Child", parent=root)

        # archive first (required by the endpoint)
        session_client.post(f"{_urls(workspace.slug, project.id)}{root.id}/archive/")
        response = session_client.delete(f"{_urls(workspace.slug, project.id)}{root.id}/")

        assert response.status_code == status.HTTP_204_NO_CONTENT
        child.refresh_from_db()
        assert child.parent_id is None
        assert not Page.objects.filter(pk=root.id).exists()
