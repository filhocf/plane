# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Observable lifecycle and visibility contract of project-scoped v1 Pages."""

import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import close_old_connections
from django.utils import timezone

from plane.db.models import Page, Project, ProjectMember, ProjectPage, User


def _base(workspace, project):
    return f"/api/v1/workspaces/{workspace.slug}/projects/{project.id}/pages/"


def _project(workspace, user, role=20):
    suffix = uuid.uuid4().hex[:8].upper()
    project = Project.objects.create(name=f"Lifecycle {suffix}", identifier=suffix[:5], workspace=workspace)
    ProjectMember.objects.create(workspace=workspace, project=project, member=user, role=role, is_active=True)
    return project


def _page(workspace, project, owner, name="Page", parent=None, access=Page.PUBLIC_ACCESS):
    page = Page.objects.create(
        workspace=workspace, owned_by=owner, created_by=owner, name=name, parent=parent, access=access
    )
    ProjectPage.objects.create(workspace=workspace, project=project, page=page)
    return page


def _archived(page):
    page.archived_at = timezone.now().date()
    page.save(update_fields=["archived_at"])
    return page


def _ids(response):
    pages = response.data if isinstance(response.data, list) else response.data["results"]
    return {str(item["id"]) for item in pages}


@pytest.mark.contract
@pytest.mark.django_db
class TestExternalPageLifecycle:
    def test_archived_list_and_query_find_only_matching_visible_pages(self, api_key_client, workspace, create_user):
        project = _project(workspace, create_user)
        archived = _archived(_page(workspace, project, create_user, "Release notes"))
        unrelated = _archived(_page(workspace, project, create_user, "Roadmap"))
        active = _page(workspace, project, create_user, "Release active")
        other = _project(workspace, create_user)
        foreign = _archived(_page(workspace, other, create_user, "Release foreign"))
        base = _base(workspace, project)

        listed = api_key_client.get(base, {"archived": "true"})
        assert listed.status_code == 200
        assert _ids(listed) == {str(archived.id), str(unrelated.id)}
        searched = api_key_client.get(base, {"archived": "true", "query": "release"})
        assert searched.status_code == 200
        assert _ids(searched) == {str(archived.id)}
        assert str(active.id) not in _ids(searched)
        assert str(foreign.id) not in _ids(searched)

    def test_search_matches_allowed_content_and_paginates_stably(self, api_key_client, workspace, create_user):
        project = _project(workspace, create_user)
        first = _archived(_page(workspace, project, create_user, "Alpha"))
        second = _archived(_page(workspace, project, create_user, "Beta"))
        Page.objects.filter(pk__in=(first.pk, second.pk)).update(description_html="<p>Needle body</p>")
        base = _base(workspace, project)

        page_one = api_key_client.get(base, {"archived": "true", "query": "needle", "per_page": 1})
        assert page_one.status_code == 200
        assert page_one.data["total_results"] == 2
        assert page_one.data["next_page_results"] is True
        assert len(page_one.data["results"]) == 1

        page_two = api_key_client.get(
            base,
            {
                "archived": "true",
                "query": "needle",
                "per_page": 1,
                "cursor": page_one.data["next_cursor"],
            },
        )
        assert page_two.status_code == 200
        assert page_two.data["prev_page_results"] is True
        assert _ids(page_one).isdisjoint(_ids(page_two))
        assert _ids(page_one) | _ids(page_two) == {str(first.id), str(second.id)}

    def test_archived_search_omits_private_pages_and_hidden_ancestors(self, api_key_client, workspace, create_user):
        project = _project(workspace, create_user, role=15)
        owner_key = uuid.uuid4().hex
        other = User.objects.create(email=f"owner-{owner_key}@plane.so", username=f"owner-{owner_key}")
        visible = _archived(_page(workspace, project, create_user, "Needle visible"))
        private = _archived(_page(workspace, project, other, "Needle private", access=Page.PRIVATE_ACCESS))
        ancestor = _archived(_page(workspace, project, other, "Hidden", access=Page.PRIVATE_ACCESS))
        descendant = _archived(_page(workspace, project, other, "Needle descendant", parent=ancestor))

        response = api_key_client.get(_base(workspace, project), {"archived": "true", "query": "needle"})
        assert response.status_code == 200
        assert _ids(response) == {str(visible.id)}
        assert str(private.id) not in _ids(response)
        assert str(descendant.id) not in _ids(response)

    def test_archive_cascades_and_restore_detaches_from_archived_parent(self, api_key_client, workspace, create_user):
        project = _project(workspace, create_user)
        root = _page(workspace, project, create_user, "Root")
        child = _page(workspace, project, create_user, "Child", parent=root)
        grandchild = _page(workspace, project, create_user, "Grandchild", parent=child)
        base = _base(workspace, project)

        archived = api_key_client.post(base + f"{root.id}/archive/")
        assert archived.status_code == 200
        assert set(archived.data["affected_page_ids"]) == {str(root.id), str(child.id), str(grandchild.id)}
        for page in (root, child, grandchild):
            page.refresh_from_db()
            assert page.archived_at is not None
        assert str(root.id) not in _ids(api_key_client.get(base))
        assert str(root.id) in _ids(api_key_client.get(base, {"archived": "true"}))

        restored = api_key_client.delete(base + f"{child.id}/archive/")
        assert restored.status_code == 200
        assert set(restored.data["restored_page_ids"]) == {str(child.id), str(grandchild.id)}
        assert restored.data["parent_id"] is None
        root.refresh_from_db()
        child.refresh_from_db()
        grandchild.refresh_from_db()
        assert root.archived_at is not None
        assert child.archived_at is None and child.parent_id is None
        assert grandchild.archived_at is None and grandchild.parent_id == child.id
        assert str(child.id) in _ids(api_key_client.get(base))

    def test_archive_and_restore_reject_locked_subtree(self, api_key_client, workspace, create_user):
        project = _project(workspace, create_user)
        root = _page(workspace, project, create_user, "Root")
        child = _page(workspace, project, create_user, "Locked", parent=root)
        Page.objects.filter(pk=child.pk).update(is_locked=True)
        base = _base(workspace, project)

        assert api_key_client.post(base + f"{root.id}/archive/").status_code == 409
        root.refresh_from_db()
        child.refresh_from_db()
        assert root.archived_at is None and child.archived_at is None

        Page.objects.filter(pk__in=(root.pk, child.pk)).update(archived_at=timezone.now().date())
        assert api_key_client.delete(base + f"{root.id}/archive/").status_code == 409
        root.refresh_from_db()
        child.refresh_from_db()
        assert root.archived_at is not None and child.archived_at is not None

        archived_parent = _archived(_page(workspace, project, create_user, "Archived parent"))
        Page.objects.filter(pk=root.pk).update(parent=archived_parent)
        assert api_key_client.delete(base + f"{root.id}/archive/").status_code == 409
        root.refresh_from_db()
        assert root.parent_id == archived_parent.id

    def test_public_descendant_below_private_ancestor_is_invisible_everywhere(
        self, api_key_client, workspace, create_user
    ):
        project = _project(workspace, create_user, role=15)
        owner_key = uuid.uuid4().hex
        other = User.objects.create(email=f"owner-{owner_key}@plane.so", username=f"owner-{owner_key}")
        hidden = _page(workspace, project, other, "Hidden", access=Page.PRIVATE_ACCESS)
        descendant = _page(workspace, project, other, "Public descendant", parent=hidden)
        base = _base(workspace, project)

        assert api_key_client.get(base + f"{descendant.id}/").status_code == 404
        assert api_key_client.get(base + f"{hidden.id}/children/").status_code == 404
        created = api_key_client.post(
            base, {"name": "Child", "parent": str(descendant.id)}, format="json"
        )
        assert created.status_code == 400
        visible = _page(workspace, project, create_user, "Visible")
        assert (
            api_key_client.patch(base + f"{visible.id}/", {"parent": str(descendant.id)}, format="json").status_code
            == 400
        )

    def test_delete_requires_archive_and_confirmation_state(self, api_key_client, workspace, create_user):
        project = _project(workspace, create_user)
        page = _page(workspace, project, create_user)
        url = _base(workspace, project) + f"{page.id}/"

        assert api_key_client.delete(url).status_code == 409
        assert Page.objects.filter(pk=page.pk).exists()
        _archived(page)
        planned = api_key_client.delete(url)
        assert planned.status_code == 428
        assert planned.data["delete_token"]
        assert Page.objects.filter(pk=page.pk).exists()

    def test_stale_confirmation_cannot_delete_after_state_changes(self, api_key_client, workspace, create_user):
        project = _project(workspace, create_user)
        page = _archived(_page(workspace, project, create_user))
        url = _base(workspace, project) + f"{page.id}/"
        detail = api_key_client.get(url)
        assert detail.status_code == 200
        # If-Match is derived from this snapshot; another writer changes the deletion state.
        old_state = detail.get("ETag") or detail.data.get("delete_token")
        assert old_state, "GET must expose a confirmation token or ETag for two-phase deletion"
        Page.objects.filter(pk=page.pk).update(parent=_page(workspace, project, create_user, "New parent"))

        rejected = api_key_client.delete(url, HTTP_IF_MATCH=old_state)
        assert rejected.status_code == 409
        assert Page.objects.filter(pk=page.pk).exists()

    def test_confirmed_delete_promotes_direct_children(self, api_key_client, workspace, create_user):
        project = _project(workspace, create_user)
        parent = _archived(_page(workspace, project, create_user, "Parent"))
        child = _archived(_page(workspace, project, create_user, "Child", parent=parent))
        grandchild = _archived(_page(workspace, project, create_user, "Grandchild", parent=child))
        url = _base(workspace, project) + f"{parent.id}/"
        detail = api_key_client.get(url)
        assert detail.status_code == 200
        token = detail.get("ETag") or detail.data.get("delete_token")
        assert token, "GET must expose a confirmation token or ETag for two-phase deletion"

        deleted = api_key_client.delete(url, HTTP_IF_MATCH=token)
        assert deleted.status_code == 204
        assert not Page.objects.filter(pk=parent.pk).exists()
        child.refresh_from_db()
        grandchild.refresh_from_db()
        assert child.parent_id is None and child.archived_at is not None
        assert grandchild.parent_id == child.id

    def test_confirmation_expires_when_direct_child_set_changes(self, api_key_client, workspace, create_user):
        project = _project(workspace, create_user)
        parent = _archived(_page(workspace, project, create_user, "Parent"))
        url = _base(workspace, project) + f"{parent.id}/"
        token = api_key_client.get(url).get("ETag")

        child = _archived(_page(workspace, project, create_user, "Late child", parent=parent))
        rejected = api_key_client.delete(url, HTTP_IF_MATCH=token)

        assert rejected.status_code == 409
        assert Page.objects.filter(pk=parent.pk).exists()
        child.refresh_from_db()
        assert child.parent_id == parent.id

    @pytest.mark.django_db(transaction=True)
    def test_concurrent_confirmed_deletes_have_one_winner(self, api_key_client, workspace, create_user):
        project = _project(workspace, create_user)
        page = _archived(_page(workspace, project, create_user, "Concurrent"))
        url = _base(workspace, project) + f"{page.id}/"
        token = api_key_client.get(url).get("ETag")

        def delete_once():
            close_old_connections()
            try:
                return api_key_client.delete(url, HTTP_IF_MATCH=token).status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(lambda _: delete_once(), range(2)))

        assert sorted(statuses) == [204, 404]
        assert not Page.objects.filter(pk=page.pk).exists()

    def test_guest_and_foreign_page_mutations_are_denied_without_side_effects(
        self, api_key_client, workspace, create_user
    ):
        project = _project(workspace, create_user, role=5)
        own = _archived(_page(workspace, project, create_user, "Owned"))
        foreign_project = _project(workspace, create_user)
        foreign = _archived(_page(workspace, foreign_project, create_user, "Foreign"))
        for page in (own, foreign):
            base = _base(workspace, project) + f"{page.id}/"
            assert api_key_client.post(base + "archive/").status_code in (403, 404)
            assert api_key_client.delete(base + "archive/").status_code in (403, 404)
            assert api_key_client.delete(base, HTTP_IF_MATCH='"untrusted"').status_code in (403, 404)
            page.refresh_from_db()
            assert page.archived_at is not None
