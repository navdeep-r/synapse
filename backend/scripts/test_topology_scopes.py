"""Verify graph topology endpoint with single, multiple, and all scope version IDs."""
import asyncio
import sys
sys.path.insert(0, ".")
from app.config import get_settings
from app.state import AppState
from kg_acl.models import Principal, Sensitivity
from kg_acl.authorized_graph import AuthorizedGraphRepository
from kg_acl.resolver import resolve_effective_scopes
from app.routers.graph import topology


async def main() -> None:
    settings = get_settings()
    state = await AppState.create(settings)
    await state.graph.start()

    user_id = "316be433-abae-4ac6-8d30-add2f0a3c998"
    role_ids = ["role-ed421dec-46a4-4b73-b553-ce01a6facaa8"]

    scope_ids, version_ids = await resolve_effective_scopes(state.db, user_id, role_ids, False)
    principal = Principal(
        user_id=user_id,
        email="navdeeprajini27@gmail.com",
        display_name="Navdeep Rajani",
        role_ids=role_ids,
        clearance=Sensitivity.SENIOR,
        is_org_supervisor=False,
        active_scope_ids=scope_ids,
        active_scope_version_ids=version_ids
    )

    auth_repo = AuthorizedGraphRepository(state.graph, state.db, principal)

    print("=== Test 1: No scope_version_id (All Assigned Scopes) ===")
    res1 = await topology(state=state, graph=auth_repo, scope_version_id=None)
    print(f"Nodes count: {len(res1['nodes'])}, Links count: {len(res1['links'])}")

    print("\n=== Test 2: scope_version_id='all' ===")
    res2 = await topology(state=state, graph=auth_repo, scope_version_id="all")
    print(f"Nodes count: {len(res2['nodes'])}, Links count: {len(res2['links'])}")

    print(f"\n=== Test 3: Single scope_version_id ({version_ids[0]}) ===")
    res3 = await topology(state=state, graph=auth_repo, scope_version_id=version_ids[0])
    print(f"Nodes count: {len(res3['nodes'])}, Links count: {len(res3['links'])}")

    print(f"\n=== Test 4: Single scope_version_id ({version_ids[1]}) ===")
    res4 = await topology(state=state, graph=auth_repo, scope_version_id=version_ids[1])
    print(f"Nodes count: {len(res4['nodes'])}, Links count: {len(res4['links'])}")

    comma_ids = f"{version_ids[0]},{version_ids[1]}"
    print(f"\n=== Test 5: Multiple comma-separated scope_version_id ({comma_ids}) ===")
    res5 = await topology(state=state, graph=auth_repo, scope_version_id=comma_ids)
    print(f"Nodes count: {len(res5['nodes'])}, Links count: {len(res5['links'])}")

    await state.close()


if __name__ == "__main__":
    asyncio.run(main())
