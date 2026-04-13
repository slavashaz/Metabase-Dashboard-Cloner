#!/usr/bin/env python3
"""
Clone a Metabase dashboard from one Metabase server to another.

Features:
  1. Authenticates to both source and target Metabase instances
  2. Builds table/field mapping between source and target databases
  3. Clones the dashboard with cards, models, tabs, and parameters
  4. Full visualization settings transfer (card refs, column names, sanitization)
  5. Creates a new collection on the target server
  6. Loops to allow cloning multiple dashboards

Settings are loaded from config_cross.json.
"""

import copy
import requests
import json
import sys
import io
import os
import warnings

try:
    import certifi
    _CERTIFI_PATH = certifi.where()
except ImportError:
    _CERTIFI_PATH = None

# Fix Windows console encoding
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except:
        pass

# ============================================================================
# Load settings from config_cross.json
# ============================================================================

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config_cross.json")
if not os.path.exists(CONFIG_FILE):
    print(f"[FAIL] Config file not found: {CONFIG_FILE}")
    sys.exit(1)

with open(CONFIG_FILE, "r", encoding="utf-8") as f:
    CFG = json.load(f)

SRC_METABASE_URL = CFG["source"]["metabase_url"]
SRC_API_KEY = CFG["source"].get("api_key", "")
SRC_USERNAME = CFG["source"].get("username", "")
SRC_PASSWORD = CFG["source"].get("password", "")
SRC_SSL_VERIFY = CFG["source"].get("ssl_verify", True)
SRC_DB_ID = CFG["source"]["database_id"]
SRC_DASHBOARD_ID = CFG["source"]["dashboard_id"]

TGT_METABASE_URL = CFG["target"]["metabase_url"]
TGT_API_KEY = CFG["target"].get("api_key", "")
TGT_USERNAME = CFG["target"].get("username", "")
TGT_PASSWORD = CFG["target"].get("password", "")
TGT_SSL_VERIFY = CFG["target"].get("ssl_verify", True)

NAME_SUFFIX = CFG.get("name_suffix", " (cloned)")

# ============================================================================
# Script code
# ============================================================================

def _resolve_ssl(ssl_verify):
    """
    Resolve ssl_verify value for requests:
      True  -> use certifi bundle if available, otherwise requests default
      False -> disable verification (suppress warning)
      str   -> path to a custom CA bundle
    """
    if ssl_verify is False:
        warnings.filterwarnings("ignore", message="Unverified HTTPS request")
        try:
            from urllib3.exceptions import InsecureRequestWarning
            warnings.simplefilter("ignore", InsecureRequestWarning)
        except ImportError:
            pass
        return False
    if isinstance(ssl_verify, str):
        return ssl_verify
    # True — use certifi if installed
    return _CERTIFI_PATH if _CERTIFI_PATH else True


class MetabaseSession:
    """Thin wrapper around a requests session with Metabase auth.

    Authentication priority:
      1. API key (api_key)  — sets X-API-KEY header, no session call needed
      2. Username + password — calls /api/session, sets X-Metabase-Session

    SSL:
      ssl_verify=True   — use certifi CA bundle if available
      ssl_verify=False  — disable certificate verification
      ssl_verify="path" — use custom CA bundle file
    """

    def __init__(self, base_url, api_key="", username="", password="", ssl_verify=True):
        self.base_url = base_url.rstrip('/')
        self.verify = _resolve_ssl(ssl_verify)
        self.session = requests.Session()

        if api_key:
            self.session.headers.update({"X-API-KEY": api_key})
        else:
            resp = self.session.post(
                f"{self.base_url}/api/session",
                json={"username": username, "password": password},
                verify=self.verify,
            )
            if resp.status_code != 200:
                raise RuntimeError(f"Auth failed: {resp.status_code} {resp.text[:200]}")
            token = resp.json()["id"]
            self.session.headers.update({"X-Metabase-Session": token})

    def get(self, path):
        resp = self.session.get(f"{self.base_url}/api{path}", verify=self.verify)
        resp.raise_for_status()
        return resp.json()

    def post(self, path, data):
        resp = self.session.post(f"{self.base_url}/api{path}", json=data, verify=self.verify)
        resp.raise_for_status()
        return resp.json()

    def put(self, path, data):
        resp = self.session.put(f"{self.base_url}/api{path}", json=data, verify=self.verify)
        resp.raise_for_status()
        return resp.json()


# ============================================================================
# Visualization helpers (from clone_view_final)
# ============================================================================

def fix_card_references(viz_json, old_card_id, new_card_id):
    """
    Replace card ID references inside visualization_settings.
    Metabase stores 'sourceId': 'card:752' — replace with 'card:NEW_ID'.
    """
    if not viz_json:
        return {}

    viz_str = json.dumps(viz_json)
    old_ref = f'"card:{old_card_id}"'
    new_ref = f'"card:{new_card_id}"'
    viz_str = viz_str.replace(old_ref, new_ref)

    try:
        return json.loads(viz_str)
    except json.JSONDecodeError:
        print(f"    [WARN] JSON parse error after card ref replacement")
        return viz_json


def map_viz_names(viz, old_meta, new_meta):
    """Remap old column names to new ones in visualization settings."""
    if not viz or not old_meta or not new_meta:
        return viz

    name_map = {}
    for i in range(min(len(old_meta), len(new_meta))):
        old_n = old_meta[i].get("name")
        new_n = new_meta[i].get("name")
        if old_n and new_n:
            name_map[old_n] = new_n

    if not name_map:
        return viz

    def repl(val):
        if isinstance(val, str):
            return name_map.get(val, val)
        return val

    new_viz = json.loads(json.dumps(viz))

    for k in ["graph.dimensions", "graph.metrics", "graph.tooltip_columns"]:
        if k in new_viz and isinstance(new_viz[k], list):
            new_viz[k] = [repl(v) for v in new_viz[k]]

    for k in ["table.pivot_column", "table.cell_column"]:
        if k in new_viz:
            new_viz[k] = repl(new_viz[k])

    if "column_settings" in new_viz:
        new_viz["column_settings"] = {repl(k): v for k, v in new_viz["column_settings"].items()}

    return new_viz


def sanitize_viz(viz, valid_names):
    """Remove references to columns that don't exist in the new metadata."""
    if not viz:
        return {}
    new_viz = json.loads(json.dumps(viz))

    def is_valid(val):
        if isinstance(val, str):
            return val in valid_names
        return True

    def clean_list(lst):
        return [v for v in lst if is_valid(v)]

    for k in ["graph.dimensions", "graph.metrics", "graph.tooltip_columns"]:
        if k in new_viz and isinstance(new_viz[k], list):
            new_viz[k] = clean_list(new_viz[k])
            if not new_viz[k]:
                del new_viz[k]

    for k in ["table.pivot_column", "table.cell_column"]:
        if k in new_viz and not is_valid(new_viz[k]):
            del new_viz[k]

    if "column_settings" in new_viz:
        new_viz["column_settings"] = {k: v for k, v in new_viz["column_settings"].items() if is_valid(k)}

    return new_viz


# ============================================================================
# Core logic
# ============================================================================

def remap_with_models(obj, table_map, field_map, target_db, card_id_map, cloned_models):
    """Recursively remap IDs for MBQL 0.58+, handling models (source-card)."""
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


def build_metadata_mapping(src: MetabaseSession, tgt: MetabaseSession,
                           src_db_id: int, tgt_db_id: int):
    """Build table_id and field_id mapping between two databases on different servers."""
    src_meta = src.get(f"/database/{src_db_id}/metadata?include_hidden=true")
    tgt_meta = tgt.get(f"/database/{tgt_db_id}/metadata?include_hidden=true")

    # Index target tables by (schema, name)
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


def clone_model_card(src: MetabaseSession, tgt: MetabaseSession,
                     model_id: int, target_db: int, table_map: dict, field_map: dict,
                     target_collection_id, name_suffix: str, cloned_models: dict):
    """Clone a model card from source to target, recursively cloning nested models."""
    if model_id in cloned_models:
        return cloned_models[model_id]

    model = src.get(f"/card/{model_id}")
    print(f"    Model [{model_id}] {model.get('name')} (type={model.get('type')})")

    # Recursively clone nested models first
    source_cards = find_source_cards(model.get("dataset_query", {}))
    for sc in source_cards:
        clone_model_card(src, tgt, sc, target_db, table_map, field_map,
                        target_collection_id, name_suffix, cloned_models)

    new_model = {
        "name": model.get("name", f"model_{model_id}") + name_suffix,
        "display": model.get("display", "table"),
        "database_id": target_db,
        "dataset_query": remap_with_models(model["dataset_query"], table_map, field_map,
                                          target_db, {}, cloned_models),
        "visualization_settings": model.get("visualization_settings", {}),
        "type": "model",
    }

    if model.get("result_metadata"):
        new_model["result_metadata"] = remap_with_models(
            model["result_metadata"], table_map, field_map, target_db, {}, cloned_models)
    if target_collection_id is not None:
        new_model["collection_id"] = target_collection_id

    r = tgt.post("/card", new_model)
    cloned_models[model_id] = r["id"]
    print(f"      -> [{r['id']}]")
    return r["id"]


def clone_dashboard(src: MetabaseSession, tgt: MetabaseSession,
                    dash_id: int, src_db_id: int, tgt_db_id: int,
                    table_map: dict, field_map: dict,
                    new_dash_name: str, target_collection_id, name_suffix: str):
    """Clone a dashboard from source to target Metabase."""

    dash = src.get(f"/dashboard/{dash_id}")
    print(f"  Source: [{dash_id}] {dash['name']}")
    print(f"  Cards: {len(dash.get('dashcards', []))}, Tabs: {len(dash.get('tabs', []))}")

    # Filter out already-cloned cards
    original_dashcards = [
        dc for dc in dash.get('dashcards', [])
        if name_suffix not in dc.get('card', {}).get('name', '')
    ]
    original_tabs = dash.get('tabs', [])

    # Tab mapping
    tab_id_map = {}
    new_tabs = []
    for idx, tab in enumerate(original_tabs):
        new_tab = {"id": -(idx + 100), "name": tab.get("name", ""), "position": tab.get("position", idx)}
        if "entity_id" in tab:
            new_tab["entity_id"] = None
        tab_id_map[tab["id"]] = new_tab["id"]
        new_tabs.append(new_tab)

    # Create dashboard on target with parameters
    dash_params = dash.get("parameters", [])
    new_params = remap_with_models(dash_params, table_map, field_map, tgt_db_id,
                                   {}, {}) if dash_params else []

    new_dash_data = {
        "name": new_dash_name,
        "description": dash.get("description"),
        "parameters": new_params,
    }
    if target_collection_id is not None:
        new_dash_data["collection_id"] = target_collection_id

    new_dash = tgt.post("/dashboard", new_dash_data)
    new_dash_id = new_dash["id"]
    print(f"  Created dashboard: [{new_dash_id}] {new_dash_name}")

    # Collect and clone models
    all_models = set()
    for dc in original_dashcards:
        cid = dc.get("card_id")
        if cid:
            card = dc.get("card") or src.get(f"/card/{cid}")
            all_models.update(find_source_cards(card.get("dataset_query", {})))

    cloned_models = {}
    card_id_map = {}
    card_meta_map = {}  # old_card_id -> (old_meta, new_meta)

    if all_models:
        print(f"  Models found: {all_models}")
        for mid in sorted(all_models):
            clone_model_card(src, tgt, mid, tgt_db_id, table_map, field_map,
                           target_collection_id, name_suffix, cloned_models)

    # Clone cards
    print(f"\n  Cloning cards...")
    for dc in original_dashcards:
        cid = dc.get("card_id")
        if not cid:
            continue
        card = dc.get("card") or src.get(f"/card/{cid}")

        dq = remap_with_models(card["dataset_query"], table_map, field_map, tgt_db_id,
                               card_id_map, cloned_models)

        new_card = {
            "name": card["name"] + name_suffix,
            "display": card.get("display", "table"),
            "database_id": tgt_db_id,
            "dataset_query": dq,
            "visualization_settings": card.get("visualization_settings", {}),
        }

        if card.get("result_metadata"):
            old_meta = card["result_metadata"]
            new_meta = remap_with_models(old_meta, table_map, field_map, tgt_db_id,
                                         card_id_map, cloned_models)
            new_card["result_metadata"] = new_meta
            card_meta_map[cid] = (old_meta, new_meta)

        if target_collection_id is not None:
            new_card["collection_id"] = target_collection_id

        r = tgt.post("/card", new_card)
        card_id_map[cid] = r["id"]
        print(f"    [{cid}] -> [{r['id']}]")

    if not card_id_map:
        print("\n  [WARN] No cards were cloned!")

    # Build dashcards with full visualization transfer
    new_dashcards = []
    for dc in original_dashcards:
        cid = dc.get("card_id")
        if not cid or cid not in card_id_map:
            continue

        new_card_id = card_id_map[cid]
        src_viz = dc.get("visualization_settings", {})

        # 1. Fix card ID references in viz settings (e.g. "card:752" -> "card:1031")
        new_viz = fix_card_references(src_viz, cid, new_card_id)

        # 2. Remap column names in viz settings
        old_meta, new_meta = card_meta_map.get(cid, ([], []))
        new_viz = map_viz_names(new_viz, old_meta, new_meta)

        # 3. Remap field/table IDs inside viz settings
        new_viz = remap_with_models(new_viz, table_map, field_map, tgt_db_id,
                                    card_id_map, cloned_models)

        # 4. Sanitize: remove references to columns that don't exist
        valid_names = {col.get("name") for col in new_meta if col.get("name")}
        if valid_names:
            new_viz = sanitize_viz(new_viz, valid_names)

        new_dc = {
            "card_id": new_card_id,
            "row": dc.get("row", 0), "col": dc.get("col", 0),
            "size_x": dc.get("size_x", 12), "size_y": dc.get("size_y", 6),
            "visualization_settings": new_viz,
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
                    p["target"] = remap_with_models(
                        p["target"], table_map, field_map, tgt_db_id,
                        card_id_map, cloned_models)
                new_pm.append(p)
            new_dc["parameter_mappings"] = new_pm

        new_dashcards.append(new_dc)

    for idx, dc in enumerate(new_dashcards):
        dc["id"] = -(idx + 1)

    # Add cards + tabs
    print(f"\n  Adding {len(new_dashcards)} cards to dashboard (with viz fix)...")
    resp = tgt.put(f"/dashboard/{new_dash_id}/cards",
                   {"cards": new_dashcards, "tabs": new_tabs})

    if resp.get("cards") is not None:
        print(f"  Cards added: {len(resp.get('cards', []))}, Tabs: {len(resp.get('tabs', []))}")

        # Update parameters with correct card_id mappings
        if dash_params:
            final_params = remap_with_models(dash_params, table_map, field_map, tgt_db_id,
                                             card_id_map, cloned_models)
            tgt.put(f"/dashboard/{new_dash_id}", {"parameters": final_params})
            print(f"  Parameters: {len(final_params)}")

        print(f"\n  -> {tgt.base_url}/dashboard/{new_dash_id}")
        return new_dash_id
    else:
        print(f"  [FAIL] {resp}")
        return None


def main():
    print("=" * 60)
    print("  Cross-Metabase Dashboard Cloner")
    print("=" * 60)

    # Connect to source
    print(f"\n[1/2] Connecting to source: {SRC_METABASE_URL}")
    try:
        src = MetabaseSession(SRC_METABASE_URL, SRC_API_KEY, SRC_USERNAME, SRC_PASSWORD, SRC_SSL_VERIFY)
    except Exception as e:
        print(f"[FAIL] Source auth: {e}")
        sys.exit(1)
    print("  [OK] Source connected")

    # Get source dashboard info
    src_dash = src.get(f"/dashboard/{SRC_DASHBOARD_ID}")
    print(f"  Dashboard: [{SRC_DASHBOARD_ID}] {src_dash.get('name')}")

    # Connect to target
    print(f"\n[2/2] Connecting to target: {TGT_METABASE_URL}")
    try:
        tgt = MetabaseSession(TGT_METABASE_URL, TGT_API_KEY, TGT_USERNAME, TGT_PASSWORD, TGT_SSL_VERIFY)
    except Exception as e:
        print(f"[FAIL] Target auth: {e}")
        sys.exit(1)
    print("  [OK] Target connected")

    clone_count = 0

    while True:
        print(f"\n{'─' * 60}")
        print(f"  Clone #{clone_count + 1}")
        print(f"{'─' * 60}")

        # Prompt for target database ID
        while True:
            try:
                tgt_db_str = input("\nTarget database ID (on target Metabase): ").strip()
                if not tgt_db_str:
                    print("  Please enter a number")
                    continue
                tgt_db_id = int(tgt_db_str)
                break
            except ValueError:
                print("  Please enter a valid number")

        # Verify target DB exists
        try:
            db_info = tgt.get(f"/database/{tgt_db_id}")
            print(f"  Found database: {db_info.get('name')}")
        except Exception:
            print(f"  [WARN] Database {tgt_db_id} not found on target, continuing anyway")

        # Prompt for new dashboard name
        new_dash_name = input("New dashboard name: ").strip()
        if not new_dash_name:
            new_dash_name = f"{src_dash.get('name')} (cross-clone)"
            print(f"  Auto name: {new_dash_name}")

        # Create collection on target
        try:
            coll = tgt.post("/collection", {"name": new_dash_name})
            coll_id = coll.get("id")
            print(f"  Collection created: [{coll_id}]")
        except Exception as e:
            print(f"  [WARN] Collection error: {e}")
            coll_id = None

        # Build mapping
        print(f"\n  Mapping DB {SRC_DB_ID} (source) -> DB {tgt_db_id} (target)...")
        try:
            table_map, field_map, missing = build_metadata_mapping(
                src, tgt, SRC_DB_ID, tgt_db_id)
            print(f"  Tables: {len(table_map)}, Fields: {len(field_map)}")
            if missing:
                print(f"  Missing tables: {len(missing)}")
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
        print(f"\n  Cloning...")
        new_id = clone_dashboard(src, tgt, SRC_DASHBOARD_ID, SRC_DB_ID, tgt_db_id,
                                 table_map, field_map, new_dash_name, coll_id, NAME_SUFFIX)

        if new_id:
            clone_count += 1
            print(f"\n  Done!")

        print(f"\n{'─' * 60}")
        another = input("Clone another dashboard? (y/n): ").strip().lower()
        if another != 'y':
            break

    print(f"\n{'=' * 60}")
    print(f"  Finished! Dashboards cloned: {clone_count}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
