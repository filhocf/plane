# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only

"""Fatia 2: session-only page tree endpoints, scoped per project and visibility."""

import uuid

import pytest
from rest_framework import status

from plane.db.models import Page, Project, ProjectMember, ProjectPage, User, UserFavorite


def _project(workspace, member, role=15):
    code = uuid.uuid4().hex[:8].upper()
    project = Project.objects.create(workspace=workspace, name=f"Tree {code}", identifier=code[:5])
    ProjectMember.objects.create(workspace=workspace, project=project, member=member, role=role, is_active=True)
    return project


def _page(workspace, project, owner, name, parent=None, access=0):
    page = Page.objects.create(
        workspace=workspace, owned_by=owner, created_by=owner, name=name, parent=parent, access=access
    )
    ProjectPage.objects.create(workspace=workspace, project=project, page=page)
    return page


def _other_user():
    code = uuid.uuid4().hex[:10]
    return User.objects.create(username=f"owner-{code}", email=f"owner-{code}@plane.so")


def _url(workspace, project, page, action):
    return f"/api/workspaces/{workspace.slug}/projects/{project.id}/pages/{page.id}/{action}/"


@pytest.mark.contract
@pytest.mark.django_db
class TestPageTreeEndpoints:
    def test_children_are_direct_and_ancestors_root_to_parent(self, session_client, workspace, create_user):
        project = _project(workspace, create_user)
        root = _page(workspace, project, create_user, "Root")
        child = _page(workspace, project, create_user, "Child", root)
        grandchild = _page(workspace, project, create_user, "Grandchild", child)
        UserFavorite.objects.create(
            workspace=workspace, project=project, user=create_user, entity_type="page", entity_identifier=child.id
        )
        siblings = session_client.get(_url(workspace, project, root, "children"))
        assert siblings.status_code == status.HTTP_200_OK
        assert [str(node["id"]) for node in siblings.data] == [str(child.id)]
        assert siblings.data[0]["project_ids"] == [str(project.id)]
        assert siblings.data[0]["is_favorite"] is True
        assert "description_html" not in siblings.data[0]
        chain = session_client.get(_url(workspace, project, grandchild, "ancestors"))
        assert chain.status_code == status.HTTP_200_OK
        assert [str(node["id"]) for node in chain.data] == [str(root.id), str(child.id)]
        assert all(node["project_ids"] == [str(project.id)] for node in chain.data)
        assert session_client.get(_url(workspace, project, root, "ancestors")).data == []

    def test_private_children_hidden_and_invisible_ancestor_cuts_chain(
        self, session_client, workspace, create_user
    ):
        project = _project(workspace, create_user)
        owner = _other_user()
        root = _page(workspace, project, owner, "Root")
        private = _page(workspace, project, owner, "Private", root, Page.PRIVATE_ACCESS)
        visible = _page(workspace, project, owner, "Visible", private)
        _page(workspace, project, owner, "Other private", root, Page.PRIVATE_ACCESS)
        children = session_client.get(_url(workspace, project, root, "children"))
        assert children.status_code == status.HTTP_200_OK
        assert children.data == []
        chain = session_client.get(_url(workspace, project, visible, "ancestors"))
        assert chain.status_code == status.HTTP_200_OK
        assert chain.data == []  # the private parent cuts the chain, including the root beyond it
        assert "Private" not in str(chain.data)

    def test_guest_only_sees_own_children_without_feature_flag(self, session_client, workspace, create_user):
        project = _project(workspace, create_user, role=5)
        owner = _other_user()
        root = _page(workspace, project, create_user, "Mine")
        _page(workspace, project, owner, "Theirs", root)
        mine = _page(workspace, project, create_user, "My child", root)
        response = session_client.get(_url(workspace, project, root, "children"))
        assert response.status_code == status.HTTP_200_OK
        assert [str(node["id"]) for node in response.data] == [str(mine.id)]

    def test_other_project_page_and_inactive_link_are_denied(self, session_client, workspace, create_user):
        project = _project(workspace, create_user)
        other = _project(workspace, create_user)
        foreign = _page(workspace, other, create_user, "Foreign")
        assert session_client.get(_url(workspace, project, foreign, "children")).status_code in (403, 404)
        root = _page(workspace, project, create_user, "Root")
        ProjectPage.objects.filter(page=root, project=project).update(deleted_at="2026-11-24T12:00:00Z")
        assert session_client.get(_url(workspace, project, root, "ancestors")).status_code in (403, 404)

    def test_api_key_cannot_use_app_tree_route(self, api_key_client, workspace, create_user):
        project = _project(workspace, create_user)
        root = _page(workspace, project, create_user, "Root")
        assert api_key_client.get(_url(workspace, project, root, "children")).status_code in (401, 403)
