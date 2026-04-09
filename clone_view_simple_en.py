#!/usr/bin/env python3
"""
Clone a Metabase dashboard within the SAME database, swapping the source view/table.

USER INPUT: only the new view name and the new dashboard name.
All other settings (schema, database ID, source dashboard) are hardcoded below.
"""

import copy
import requests
import json
import sys
import io

# Fix Windows console encoding
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except:
        pass

# ============================================================================
# SETTINGS — edit these before running
# ============================================================================

METABASE_URL = "https://metabase.your-company.com/"   # Metabase base URL (no trailing slash)
MB_USERNAME = "your@email.com"            # Login email
MB_PASSWORD = "your-password"                               # Password

DATABASE_ID = 1           # Database ID (same for source and target)
DASHBOARD_ID = 7          # Source dashboard ID to clone

# Source view/table that the current dashboard is built on
SOURCE_VIEW_SCHEMA = "dbo"     # Schema of the source view
SOURCE_VIEW_NAME = "my_view"   # Name of the source view

# Suffix appended to cloned card and model names
NAME_SUFFIX = " (v2)"

# ============================================================================
# Script code
# ============================================================================

def remap_with_models(obj, table_map, field_map, target_db, card_id_map, cloned_models):
    """Recursively remap IDs in MBQL (v0.58+), handling models (source-card)."""
    if obj is None:
        return None
    if isinstance(obj, list):
        if len(obj) >= 2 and obj[0] == "field":
            new = list(obj)
            if len(obj) == 3 and isinstance(obj[1], dict) and isinstance(obj[2], int):
                new[2] = field_map.get(obj[2], obj[2])
            elif isinstance(obj[1], int):
                new[1] = field_map.get(obj[1], obj[1])
            for i in range(len(new)):
                if isinstance(new[i], (dict, list)):
                    new[i] = remap_with_models(new[i], table_map, field_map, target_db,
                                               card_id_map, cloned_models)
            return new
        if len(obj) >= 2 and obj[0] == "dimension":
            new = list(obj)
            new[1] = remap_with_models(new[1], table_map, field_map, target_db,
                                       card_id_map, cloned_models)
            if len(new) > 2 and isinstance(new[2], dict):
                new[2] = remap_with_models(new[2], table_map, field_map, target_db,
                                           card_id_map, cloned_models)
            return new
        return [remap_with_models(x, table_map, field_map, target_db,
                                  card_id_map, cloned_models) for x in obj]
    if isinstance(obj, dict):
        new_obj = {}
        for k, v in obj.items():
            if k == "database":
                new_obj[k] = target_db
            elif k == "source-table" and isinstance(v, int):
                new_obj[k] = table_map.get(v, v)
            elif k == "source-card" and isinstance(v, int):
                new_id = cloned_models.get(v, card_id_map.get(v, v))
                new_obj[k] = new_id
            elif k in ("source-field", "field_id", "field-id") and isinstance(v, int):
                new_obj[k] = field_map.get(v, v)
            elif k == "table_id" and isinstance(v, int):
                new_obj[k] = table_map.get(v, v)
            else:
                new_obj[k] = remap_with_models(v, table_map, field_map, target_db,
                                               card_id_map, cloned_models)
        return new_obj
    return obj


def find_source_cards(dataset_query):
    """Find all source-card (model) references in a dataset_query."""
    cards = set()
    if not isinstance(dataset_query, dict):
        return cards
    for stage in dataset_query.get("stages", []):
        sc = stage.get("source-card")
        if isinstance(sc, int):
            cards.add(sc)
    return cards


def find_table_by_name(session, base_url, db_id, schema_name, table_name):
    """Find a table/view by schema + name in the given database."""
    meta = session.get(f"{base_url}/api/database/{db_id}/metadata?include_hidden=true").json()
    for t in meta["tables"]:
        t_schema = t.get("schema") or ""
        t_name = t.get("name") or ""
        if t_schema == schema_name and t_name == table_name:
            return t
    return None


def build_view_mapping(session, base_url, db_id, src_schema, src_table,
                       tgt_schema, tgt_table):
    """Build table_id and field_id mapping between two views in the same database."""
    print(f"\n  Looking up: {src_schema}.{src_table}...")
    src_t = find_table_by_name(session, base_url, db_id, src_schema, src_table)
    if not src_t:
        print(f"  [FAIL] Source table not found!")
        return None, None
    print(f"  Found: id={src_t['id']}, fields={len(src_t.get('fields', []))}")

    print(f"  Looking up: {tgt_schema}.{tgt_table}...")
    tgt_t = find_table_by_name(session, base_url, db_id, tgt_schema, tgt_table)
    if not tgt_t:
        print(f"  [FAIL] Target table '{tgt_schema}.{tgt_table}' not found!")
        return None, None
    print(f"  Found: id={tgt_t['id']}, fields={len(tgt_t.get('fields', []))}")

    table_map = {src_t["id"]: tgt_t["id"]}
    field_map = {}
    tgt_fields = {f["name"]: f for f in tgt_t.get("fields", [])}
    missing = []

    for src_f in src_t.get("fields", []):
        tgt_f = tgt_fields.get(src_f["name"])
        if tgt_f:
            field_map[src_f["id"]] = tgt_f["id"]
        else:
            missing.append(src_f["name"])

    print(f"  Fields mapped: {len(field_map)}/{len(src_t.get('fields', []))}")
    if missing:
        print(f"  Missing fields: {', '.join(missing[:15])}")

    return table_map, field_map


def clone_model_card(session, base_url, model_id, target_db, table_map, field_map,
                     target_collection_id, name_suffix, cloned_models, card_id_map):
    """Clone a model card, recursively cloning any nested models."""
    if model_id in cloned_models:
        return cloned_models[model_id]

    model = session.get(f"{base_url}/api/card/{model_id}").json()
    print(f"    Model [{model_id}] {model.get('name')}")

    source_cards = find_source_cards(model.get("dataset_query", {}))
    for sc in source_cards:
        clone_model_card(session, base_url, sc, target_db, table_map, field_map,
                        target_collection_id, name_suffix, cloned_models, card_id_map)

    new_model = {
        "name": model.get("name", f"model_{model_id}") + name_suffix,
        "display": model.get("display", "table"),
        "database_id": target_db,
        "dataset_query": remap_with_models(model["dataset_query"], table_map, field_map,
                                          target_db, card_id_map, cloned_models),
        "visualization_settings": model.get("visualization_settings", {}),
        "type": "model",
    }

    if model.get("result_metadata"):
        new_model["result_metadata"] = remap_with_models(
            model["result_metadata"], table_map, field_map, target_db,
            card_id_map, cloned_models)
    if target_collection_id is not None or model.get("collection_id"):
        new_model["collection_id"] = target_collection_id or model.get("collection_id")

    r = session.post(f"{base_url}/api/card", json=new_model).json()
    cloned_models[model_id] = r["id"]
    print(f"      -> [{r['id']}]")
    return r["id"]


def clone_dashboard(session, base_url, dash_id, db_id, table_map, field_map,
                    new_dash_name, target_collection_id, name_suffix):
    """Clone a dashboard with view swap, preserving tabs, models, and parameters."""

    dash = session.get(f"{base_url}/api/dashboard/{dash_id}").json()
    print(f"  Source: [{dash_id}] {dash['name']}")
    print(f"  Cards: {len(dash.get('dashcards', []))}, Tabs: {len(dash.get('tabs', []))}")

    original_tabs = dash.get('tabs', [])
    original_dashcards = [
        dc for dc in dash.get('dashcards', [])
        if name_suffix not in dc.get('card', {}).get('name', '')
    ]

    # Tab mapping
    tab_id_map = {}
    new_tabs = []
    for idx, tab in enumerate(original_tabs):
        new_tab = {"id": -(idx + 100), "name": tab.get("name", ""), "position": tab.get("position", idx)}
        if "entity_id" in tab:
            new_tab["entity_id"] = None
        tab_id_map[tab["id"]] = new_tab["id"]
        new_tabs.append(new_tab)

    # Create dashboard
    new_dash_data = {"name": new_dash_name, "description": dash.get("description")}
    if target_collection_id is not None:
        new_dash_data["collection_id"] = target_collection_id
    elif dash.get("collection_id"):
        new_dash_data["collection_id"] = dash["collection_id"]

    new_dash = session.post(f"{base_url}/api/dashboard", json=new_dash_data).json()
    new_dash_id = new_dash["id"]
    print(f"  Dashboard: [{new_dash_id}] {new_dash_name}")

    # Collect and clone models
    all_models = set()
    for dc in original_dashcards:
        cid = dc.get("card_id")
        if cid:
            card = session.get(f"{base_url}/api/card/{cid}").json()
            all_models.update(find_source_cards(card.get("dataset_query", {})))

    cloned_models = {}
    card_id_map = {}

    if all_models:
        print(f"  Models: {all_models}")
        for mid in sorted(all_models):
            clone_model_card(session, base_url, mid, db_id, table_map, field_map,
                           target_collection_id, name_suffix, cloned_models, card_id_map)

    # Clone cards
    for dc in original_dashcards:
        cid = dc.get("card_id")
        if not cid:
            continue
        card = session.get(f"{base_url}/api/card/{cid}").json()

        dq = remap_with_models(card["dataset_query"], table_map, field_map, db_id,
                               card_id_map, cloned_models)

        new_card = {
            "name": card["name"] + name_suffix,
            "display": card.get("display", "table"),
            "database_id": db_id,
            "dataset_query": dq,
            "visualization_settings": card.get("visualization_settings", {}),
        }

        if card.get("result_metadata"):
            new_card["result_metadata"] = remap_with_models(
                card["result_metadata"], table_map, field_map, db_id,
                card_id_map, cloned_models)
        if target_collection_id is not None or dash.get("collection_id"):
            new_card["collection_id"] = target_collection_id or dash["collection_id"]

        r = session.post(f"{base_url}/api/card", json=new_card).json()
        card_id_map[cid] = r["id"]

    # Build dashcards
    new_dashcards = []
    for dc in original_dashcards:
        cid = dc.get("card_id")
        if not cid or cid not in card_id_map:
            continue

        new_dc = {
            "card_id": card_id_map[cid],
            "row": dc.get("row", 0), "col": dc.get("col", 0),
            "size_x": dc.get("size_x", 12), "size_y": dc.get("size_y", 6),
            "visualization_settings": dc.get("visualization_settings", {}),
            "series": [],
        }

        old_tab = dc.get("dashboard_tab_id")
        if old_tab is not None and old_tab in tab_id_map:
            new_dc["dashboard_tab_id"] = tab_id_map[old_tab]

        if dc.get("parameter_mappings"):
            new_pm = []
            for pm in dc["parameter_mappings"]:
                p = copy.deepcopy(pm)
                if "card_id" in p:
                    p["card_id"] = card_id_map.get(p["card_id"], p["card_id"])
                if "target" in p:
                    p["target"] = remap_with_models(p["target"], table_map, field_map, db_id,
                                                    card_id_map, cloned_models)
                new_pm.append(p)
            new_dc["parameter_mappings"] = new_pm

        new_dashcards.append(new_dc)

    for idx, dc in enumerate(new_dashcards):
        dc["id"] = -(idx + 1)

    # Add cards + tabs to dashboard
    resp = session.put(f"{base_url}/api/dashboard/{new_dash_id}/cards",
                       json={"cards": new_dashcards, "tabs": new_tabs})

    if resp.status_code == 200:
        r = resp.json()
        print(f"  Cards added: {len(r.get('cards', []))}, Tabs: {len(r.get('tabs', []))}")

        params = dash.get("parameters", [])
        if params:
            new_params = remap_with_models(params, table_map, field_map, db_id,
                                           card_id_map, cloned_models)
            rp = session.put(f"{base_url}/api/dashboard/{new_dash_id}",
                             json={"parameters": new_params})
            if rp.status_code == 200:
                print(f"  Parameters: {len(new_params)}")

        print(f"\n  -> {base_url}/dashboard/{new_dash_id}")
        return new_dash_id
    else:
        print(f"  [FAIL] {resp.status_code}: {resp.text[:300]}")
        return None


def main():
    print("=" * 60)
    print("  Dashboard Clone (view swap, single database)")
    print("=" * 60)

    # Authenticate
    session = requests.Session()
    r = session.post(f"{METABASE_URL.rstrip('/')}/api/session",
                     json={"username": MB_USERNAME, "password": MB_PASSWORD})
    if r.status_code != 200:
        print(f"[FAIL] Authentication failed: {r.status_code}")
        sys.exit(1)
    session.headers.update({"X-Metabase-Session": r.json()["id"]})
    print("[OK] Authenticated")

    base_url = METABASE_URL.rstrip('/')

    # Get parent collection of source dashboard
    src_dash = session.get(f"{base_url}/api/dashboard/{DASHBOARD_ID}").json()
    src_coll_id = src_dash.get("collection_id")
    src_parent_id = None
    if src_coll_id:
        try:
            sc = session.get(f"{base_url}/api/collection/{src_coll_id}").json()
            src_parent_id = sc.get("parent_id")
        except Exception:
            pass

    print(f"[OK] Dashboard: [{DASHBOARD_ID}] {src_dash.get('name')}")
    print(f"    Source view: {SOURCE_VIEW_SCHEMA}.{SOURCE_VIEW_NAME}")

    while True:
        print(f"\n{'─' * 60}")

        # Prompt for target view name
        tgt_view = input("\nTarget view name: ").strip()
        if not tgt_view:
            print("  Please enter a view name")
            continue

        # Prompt for new dashboard name
        dash_name = input("New dashboard name: ").strip()
        if not dash_name:
            dash_name = f"{src_dash.get('name')} — {tgt_view}"
            print(f"  Auto-generated name: {dash_name}")

        # Build mapping
        print(f"\n  Mapping {SOURCE_VIEW_SCHEMA}.{SOURCE_VIEW_NAME} -> {SOURCE_VIEW_SCHEMA}.{tgt_view}")
        table_map, field_map = build_view_mapping(
            session, base_url, DATABASE_ID,
            SOURCE_VIEW_SCHEMA, SOURCE_VIEW_NAME,
            SOURCE_VIEW_SCHEMA, tgt_view
        )

        if not table_map:
            retry = input("\n  Try another view? (y/n): ").strip().lower()
            if retry != 'y':
                break
            continue

        # Create collection
        try:
            cd = {"name": dash_name}
            if src_parent_id is not None:
                cd["parent_id"] = src_parent_id
            nc = session.post(f"{base_url}/api/collection", json=cd).json()
            coll_id = nc.get("id")
            print(f"  Collection: [{coll_id}]")
        except Exception as e:
            print(f"  [WARN] Collection error: {e}")
            coll_id = src_coll_id

        # Clone
        print(f"\n  Cloning...")
        new_id = clone_dashboard(session, base_url, DASHBOARD_ID, DATABASE_ID,
                                 table_map, field_map, dash_name, coll_id, NAME_SUFFIX)

        if new_id:
            print(f"\n  Done!")

        print(f"\n{'─' * 60}")
        another = input("Clone another? (y/n): ").strip().lower()
        if another != 'y':
            break

    print(f"\n{'=' * 60}")
    print("  Finished!")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
