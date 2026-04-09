#!/usr/bin/env python3
"""
Interactive Metabase dashboard cloning across databases.

Features:
  1. Creates a new collection named after the new dashboard
  2. Clones the dashboard preserving tabs
  3. All cards and the dashboard land in the new collection
  4. After cloning, prompts to clone to another database

Connection settings are hardcoded in the script.
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
# CONNECTION SETTINGS — edit these before running
# ============================================================================

METABASE_URL = "https://metabase.your-company.com/"   # Metabase base URL
MB_USERNAME = "your@email.com"                  # Login email
MB_PASSWORD = "your-password"                               # Password

SOURCE_DB_ID = 1       # Source database ID
DASHBOARD_ID = 7         # Source dashboard ID to clone
NAME_SUFFIX = " (DB2)"     # Suffix appended to cloned card names

# ============================================================================
# Script code
# ============================================================================

def remap(obj, table_map, field_map, target_db):
    """Recursively remap IDs for MBQL 0.58+."""
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
                    new[i] = remap(new[i], table_map, field_map, target_db)
            return new
        if len(obj) >= 2 and obj[0] == "dimension":
            new = list(obj)
            new[1] = remap(new[1], table_map, field_map, target_db)
            if len(new) > 2 and isinstance(new[2], dict):
                new[2] = remap(new[2], table_map, field_map, target_db)
            return new
        return [remap(x, table_map, field_map, target_db) for x in obj]
    if isinstance(obj, dict):
        return {
            k: (target_db if k == "database" else
                table_map.get(v, v) if k == "source-table" and isinstance(v, int) else
                field_map.get(v, v) if k in ("source-field", "field_id", "field-id") and isinstance(v, int) else
                table_map.get(v, v) if k == "table_id" and isinstance(v, int) else
                remap(v, table_map, field_map, target_db))
            for k, v in obj.items()
        }
    return obj


def build_metadata_mapping(session, base_url, src_db, tgt_db):
    """Build table_id and field_id mapping between two databases."""
    src_meta = session.get(f"{base_url}/api/database/{src_db}/metadata?include_hidden=true").json()
    tgt_meta = session.get(f"{base_url}/api/database/{tgt_db}/metadata?include_hidden=true").json()

    tgt_tables = {}
    for t in tgt_meta["tables"]:
        key = (t.get("schema"), t["name"])
        tgt_tables[key] = t

    table_map = {}
    field_map = {}
    missing_tables = []

    for src_table in src_meta["tables"]:
        key = (src_table.get("schema"), src_table["name"])
        tgt_table = tgt_tables.get(key)
        if not tgt_table:
            missing_tables.append(f"{key[0]}.{key[1]}")
            continue

        table_map[src_table["id"]] = tgt_table["id"]
        tgt_fields = {f["name"]: f for f in tgt_table.get("fields", [])}
        for src_field in src_table.get("fields", []):
            tgt_field = tgt_fields.get(src_field["name"])
            if tgt_field:
                field_map[src_field["id"]] = tgt_field["id"]

    return table_map, field_map, missing_tables


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


def clone_model_card(session, base_url, model_id, target_db, table_map, field_map,
                     target_collection_id, name_suffix, cloned_models):
    """Clone a model card, recursively cloning any nested models."""
    if model_id in cloned_models:
        return cloned_models[model_id]

    model = session.get(f"{base_url}/api/card/{model_id}").json()
    print(f"    Model [{model_id}] {model.get('name')} (type={model.get('type')})")

    source_cards = find_source_cards(model.get("dataset_query", {}))
    for sc in source_cards:
        clone_model_card(session, base_url, sc, target_db, table_map, field_map,
                        target_collection_id, name_suffix, cloned_models)

    new_model_data = {
        "name": model.get("name", f"model_{model_id}") + name_suffix,
        "display": model.get("display", "table"),
        "database_id": target_db,
        "dataset_query": remap(model["dataset_query"], table_map, field_map, target_db),
        "visualization_settings": model.get("visualization_settings", {}),
        "type": "model",
    }

    if model.get("result_metadata"):
        new_model_data["result_metadata"] = remap(model["result_metadata"], table_map, field_map, target_db)
    if target_collection_id is not None or model.get("collection_id"):
        new_model_data["collection_id"] = target_collection_id or model.get("collection_id")

    new_model = session.post(f"{base_url}/api/card", json=new_model_data).json()
    new_id = new_model["id"]
    cloned_models[model_id] = new_id
    print(f"      -> [{new_id}]")
    return new_id


def remap_with_models(obj, table_map, field_map, target_db, card_id_map, cloned_models):
    """Recursively remap IDs, handling model references (source-card)."""
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


def clone_dashboard(session, base_url, dash_id, source_db, target_db,
                    table_map, field_map, new_dash_name, target_collection_id, name_suffix):
    """Clone a dashboard with cards, tabs, and parameters."""

    dash = session.get(f"{base_url}/api/dashboard/{dash_id}").json()
    print(f"  Source: [{dash_id}] {dash['name']}")
    print(f"  Cards: {len(dash.get('dashcards', []))}")

    # Tabs
    original_tabs = dash.get('tabs', [])
    print(f"  Tabs: {len(original_tabs)}")
    for t in original_tabs:
        print(f"    [{t.get('id')}] {t.get('name')}")

    # Filter out already-cloned cards (those with the suffix)
    original_dashcards = [
        dc for dc in dash.get('dashcards', [])
        if name_suffix not in dc.get('card', {}).get('name', '')
    ]
    print(f"  Original cards: {len(original_dashcards)}")

    # Tab mapping
    tab_id_map = {}
    new_tabs = []
    for idx, tab in enumerate(original_tabs):
        new_tab = {
            "id": -(idx + 100),
            "name": tab.get("name", ""),
            "position": tab.get("position", idx),
        }
        if "entity_id" in tab:
            new_tab["entity_id"] = None
        tab_id_map[tab["id"]] = new_tab["id"]
        new_tabs.append(new_tab)

    # 1. Create new dashboard
    new_dash_data = {
        "name": new_dash_name,
        "description": dash.get("description"),
    }
    if target_collection_id is not None:
        new_dash_data["collection_id"] = target_collection_id
    elif dash.get("collection_id"):
        new_dash_data["collection_id"] = dash["collection_id"]

    new_dash = session.post(f"{base_url}/api/dashboard", json=new_dash_data).json()
    new_dash_id = new_dash["id"]
    print(f"  Created dashboard: [{new_dash_id}] {new_dash_name}")

    # 2. Collect all models (source-card) from all cards
    all_models = set()
    for dc in original_dashcards:
        old_card_id = dc.get("card_id")
        if not old_card_id:
            continue
        card = session.get(f"{base_url}/api/card/{old_card_id}").json()
        model_ids = find_source_cards(card.get("dataset_query", {}))
        all_models.update(model_ids)

    # Clone models
    cloned_models = {}
    card_id_map = {}

    if all_models:
        print(f"\n  Models found: {all_models}")
        for model_id in sorted(all_models):
            clone_model_card(session, base_url, model_id, target_db, table_map, field_map,
                           target_collection_id, name_suffix, cloned_models)
        print(f"  Models cloned: {cloned_models}")

    # 3. Clone cards
    for dc in original_dashcards:
        old_card_id = dc.get("card_id")
        if not old_card_id:
            continue

        card = session.get(f"{base_url}/api/card/{old_card_id}").json()

        dataset_query = remap_with_models(
            card["dataset_query"], table_map, field_map, target_db,
            card_id_map, cloned_models
        )

        new_card_data = {
            "name": card["name"] + name_suffix,
            "display": card.get("display", "table"),
            "database_id": target_db,
            "dataset_query": dataset_query,
            "visualization_settings": card.get("visualization_settings", {}),
        }

        if card.get("result_metadata"):
            new_card_data["result_metadata"] = remap_with_models(
                card["result_metadata"], table_map, field_map, target_db,
                card_id_map, cloned_models
            )
        if target_collection_id is not None or dash.get("collection_id"):
            new_card_data["collection_id"] = target_collection_id or dash["collection_id"]

        new_card = session.post(f"{base_url}/api/card", json=new_card_data).json()
        card_id_map[old_card_id] = new_card["id"]

    # 4. Build dashcards with correct tab_id
    new_dashcards = []

    for dc in original_dashcards:
        old_card_id = dc.get("card_id")
        if not old_card_id or old_card_id not in card_id_map:
            continue

        new_dc = {
            "card_id": card_id_map[old_card_id],
            "row": dc.get("row", 0),
            "col": dc.get("col", 0),
            "size_x": dc.get("size_x", 12),
            "size_y": dc.get("size_y", 6),
            "visualization_settings": dc.get("visualization_settings", {}),
            "series": [],
        }

        old_tab_id = dc.get("dashboard_tab_id")
        if old_tab_id is not None and old_tab_id in tab_id_map:
            new_dc["dashboard_tab_id"] = tab_id_map[old_tab_id]

        if dc.get("parameter_mappings"):
            new_pm = []
            for pm in dc["parameter_mappings"]:
                p = copy.deepcopy(pm)
                if "card_id" in p:
                    p["card_id"] = card_id_map.get(p["card_id"], p["card_id"])
                if "target" in p:
                    p["target"] = remap(p["target"], table_map, field_map, target_db)
                new_pm.append(p)
            new_dc["parameter_mappings"] = new_pm

        new_dashcards.append(new_dc)

    # 5. Add tabs + cards in one request
    for idx, dc in enumerate(new_dashcards):
        if "id" not in dc:
            dc["id"] = -(idx + 1)

    payload = {
        "cards": new_dashcards,
        "tabs": new_tabs,
    }

    resp = session.put(
        f"{base_url}/api/dashboard/{new_dash_id}/cards",
        json=payload
    )

    if resp.status_code == 200:
        result = resp.json()
        added_cards = len(result.get('cards', []))
        added_tabs = len(result.get('tabs', []))
        print(f"  Cards added: {added_cards}")
        print(f"  Tabs created: {added_tabs}")

        # Dashboard parameters (filters)
        dash_parameters = dash.get("parameters", [])
        if dash_parameters:
            new_parameters = remap(dash_parameters, table_map, field_map, target_db)
            resp_p = session.put(
                f"{base_url}/api/dashboard/{new_dash_id}",
                json={"parameters": new_parameters}
            )
            if resp_p.status_code == 200:
                print(f"  Parameters set: {len(new_parameters)}")
            else:
                print(f"  [WARN] Parameter error: {resp_p.status_code}")

        print(f"\n  Link: {base_url}/dashboard/{new_dash_id}")
        return new_dash_id
    else:
        print(f"  [FAIL] Error: {resp.status_code}")
        print(f"  Response: {resp.text[:400]}")
        return None


def main():
    print("=" * 60)
    print("  Metabase Dashboard Cloner")
    print("=" * 60)

    # Authenticate
    session = requests.Session()
    resp = session.post(
        f"{METABASE_URL.rstrip('/')}/api/session",
        json={"username": MB_USERNAME, "password": MB_PASSWORD},
    )
    if resp.status_code != 200:
        print(f"[FAIL] Authentication error: {resp.status_code}")
        sys.exit(1)
    session.headers.update({"X-Metabase-Session": resp.json()["id"]})
    print("[OK] Authenticated")

    base_url = METABASE_URL.rstrip('/')

    # Load source database metadata once
    src_meta = session.get(f"{base_url}/api/database/{SOURCE_DB_ID}/metadata?include_hidden=true").json()
    src_tables_count = len(src_meta.get("tables", []))
    print(f"[OK] Source DB {SOURCE_DB_ID}: {src_tables_count} tables")

    # Get source dashboard info
    src_dash = session.get(f"{base_url}/api/dashboard/{DASHBOARD_ID}").json()
    src_collection_id = src_dash.get("collection_id")
    src_collection_name = "Root"
    src_parent_id = None

    if src_collection_id:
        try:
            src_coll = session.get(f"{base_url}/api/collection/{src_collection_id}").json()
            src_collection_name = src_coll.get("name", str(src_collection_id))
            src_parent_id = src_coll.get("parent_id")
        except Exception:
            pass

    print(f"[OK] Source dashboard: [{DASHBOARD_ID}] {src_dash.get('name')}")
    print(f"  Collection: {src_collection_name} (id={src_collection_id})")

    clone_count = 0

    while True:
        print(f"\n{'─' * 60}")
        print(f"  Clone #{clone_count + 1}")
        print(f"{'─' * 60}")

        # Prompt for target database ID
        while True:
            try:
                target_db_str = input("\nTarget database ID: ").strip()
                if not target_db_str:
                    print("  Please enter a number")
                    continue
                target_db = int(target_db_str)
                break
            except ValueError:
                print("  Please enter a valid number")

        # Check if database exists
        try:
            db_info = session.get(f"{base_url}/api/database/{target_db}").json()
            print(f"  Found database: {db_info.get('name')}")
        except Exception:
            print(f"  [WARN] Database {target_db} not found, continuing anyway")

        # Prompt for new dashboard name
        new_dash_name = input("New dashboard name: ").strip()
        if not new_dash_name:
            new_dash_name = f"Clone {DASHBOARD_ID} -> DB {target_db}"
            print(f"  Auto-generated name: {new_dash_name}")

        # Create collection
        print(f"\n  Creating collection: {new_dash_name}")
        try:
            coll_data = {"name": new_dash_name}
            if src_parent_id is not None:
                coll_data["parent_id"] = src_parent_id
                print(f"  Parent: source collection (id={src_parent_id})")
            else:
                print(f"  Parent: root")

            new_collection = session.post(f"{base_url}/api/collection", json=coll_data).json()
            target_collection_id = new_collection.get("id")
            print(f"  Collection created: [{target_collection_id}]")
        except Exception as e:
            print(f"  [WARN] Collection error: {e}")
            print(f"  Using source collection")
            target_collection_id = src_collection_id

        # Build mapping
        print(f"\n  Mapping DB {SOURCE_DB_ID} -> DB {target_db}...")
        try:
            table_map, field_map, missing_tables = build_metadata_mapping(
                session, base_url, SOURCE_DB_ID, target_db
            )
            print(f"  Tables: {len(table_map)}")
            print(f"  Fields: {len(field_map)}")
            if missing_tables:
                print(f"  Missing tables: {len(missing_tables)}")
        except Exception as e:
            print(f"  [FAIL] Mapping error: {e}")
            retry = input("  Continue? (y/n): ").strip().lower()
            if retry != 'y':
                break
            continue

        if not table_map:
            print(f"  [FAIL] Mapping is empty.")
            retry = input("  Try another database? (y/n): ").strip().lower()
            if retry != 'y':
                break
            continue

        # Clone
        print(f"\n  Cloning dashboard...")
        new_dash_id = clone_dashboard(
            session=session,
            base_url=base_url,
            dash_id=DASHBOARD_ID,
            source_db=SOURCE_DB_ID,
            target_db=target_db,
            table_map=table_map,
            field_map=field_map,
            new_dash_name=new_dash_name,
            target_collection_id=target_collection_id,
            name_suffix=NAME_SUFFIX,
        )

        if new_dash_id:
            clone_count += 1

        # Prompt for another clone
        print(f"\n{'─' * 60}")
        another = input("Clone to another database? (y/n): ").strip().lower()
        if another != 'y':
            break

    print(f"\n{'=' * 60}")
    print(f"  Done! Dashboards cloned: {clone_count}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
