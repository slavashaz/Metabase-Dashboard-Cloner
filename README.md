# Metabase Dashboard Cloner

Clone Metabase dashboards across databases or swap source views within the same database. Preserves tabs, models, dashboard filters, and card layout.

## Scripts

| File | Use case |
|------|----------|
| `clone_dashboard_interactive_en.py` | Clone a dashboard to a **different database** (same schema, different DB) on the **same server** |
| `clone_view_simple_en.py` | Clone a dashboard within the **same database**, replacing the source view/table with another |
| `clone_cross_metabase_en.py` | Clone a dashboard from one **Metabase server to another** (cross-server cloning). Located in **cross_metabase** directory |

## Requirements

- Python 3.8+
- `requests` library (`pip install requests`)
- Metabase 0.50+ (tested on 0.58)
- **Admin access** — the Metabase user specified in the script must have admin privileges

## Finding IDs

### Dashboard ID

Open the dashboard in your browser and look at the URL:

```
https://metabase.your-company.com/dashboard/73
                                             ^^
                                          Dashboard ID
```

### Database ID

1. Go to **Admin settings** (gear icon in the top right) → **Databases**
2. Click on the database you need
3. Look at the URL:

```
https://metabase.your-company.com/admin/databases/9
                                                ^
                                             Database ID
```

Alternatively, open any question built on that database and check its `dataset_query` in the browser dev tools — the `database` field contains the ID.

## Quick Start

### clone_dashboard_interactive_en.py

1. Open the script and edit the **SETTINGS** section at the top:

```python
METABASE_URL = "https://metabase.your-company.com/"
MB_USERNAME = "your@email.com"
MB_PASSWORD = "your-password"
```

2. Run: `python clone_dashboard_interactive_en.py`

Prompts for:
- **Target database ID** — the database to clone the dashboard into
- **New dashboard name** — name for the cloned dashboard

Loops so you can clone to multiple databases in one run.

### clone_view_simple_en.py

1. Open the script and edit the **SETTINGS** section at the top.
2. Run: `python clone_view_simple_en.py`

Prompts for:
- **Target view name** — the view/table to swap in (same schema as source)
- **New dashboard name**

All other settings (source view, schema, database, dashboard ID) are hardcoded in the script.

### clone_cross_metabase_en.py (Cross-Server)

Clones a dashboard from one Metabase instance to another (e.g. from production to a local dev server). Includes full visualization transfer with card reference fixing, column name remapping, and sanitization.

**1. Edit `config_cross.json`:**

```json
{
    "source": {
        "metabase_url": "https://metabase.your-company.com/",
        "username": "your@email.com",
        "password": "your-password",
        "database_id": 11,
        "dashboard_id": 87
    },
    "target": {
        "metabase_url": "http://localhost:5001/",
        "username": "your@email.com",
        "password": "your-password"
    },
    "name_suffix": " (cloned)"
}
```

| Field | Description |
|-------|-------------|
| `source.metabase_url` | URL of the Metabase server where the original dashboard lives |
| `source.username` / `password` | Credentials for the source server |
| `source.database_id` | Database ID on the source server (the DB the dashboard queries) |
| `source.dashboard_id` | ID of the dashboard to clone |
| `target.metabase_url` | URL of the Metabase server to clone into |
| `target.username` / `password` | Credentials for the target server |
| `name_suffix` | Suffix appended to cloned card and model names |

**2. Run:**

```bash
python clone_cross_metabase_en.py
```

**3. Follow the prompts:**

- **Target database ID** — the database ID on the **target** server (the script maps tables/fields by schema + name between source and target DBs)
- **New dashboard name** — name for the cloned dashboard and its collection

The script loops, so you can clone the same dashboard to multiple target databases in one run.

**How it works:**

1. Connects to both source and target Metabase instances
2. Builds a metadata mapping (table/field IDs) between the source DB and the target DB by matching schema + table name + field name
3. Clones models recursively (handles nested `source-card` references)
4. Clones each card with remapped IDs
5. Transfers visualization settings with:
   - Card reference replacement (`"card:OLD"` → `"card:NEW"`)
   - Column name remapping in graph dimensions, metrics, column_settings, etc.
   - Sanitization of references to columns that don't exist in the target
6. Creates dashboard with tabs, cards, and parameters (filters)
7. Creates a new collection on the target server

## Supported Query Types

All three types are fully supported:

- **GUI questions** — standard MBQL queries built through the query builder. Table and field IDs are remapped to the target.
- **Models** — cards of type `model` used as data sources (`source-card`). Models are cloned recursively, including nested model references, and all dependent cards are updated to point to the cloned models.
- **Native SQL queries** — SQL questions are cloned as-is. The SQL text itself is not rewritten, but card metadata, visualization settings, and dashboard bindings are preserved.

## What Gets Cloned

- Dashboard cards (queries) with remapped table/field IDs
- Models (`source-card` references) — cloned recursively with nested dependencies
- Native SQL queries — cloned and added to the dashboard
- Dashboard tabs (preserved layout)
- Dashboard parameters (filters) with remapped field bindings
- Card positions, sizes, and visualization settings
- Visualization settings: graph dimensions, metrics, column settings, card references
- New collection created under the same parent, named after the new dashboard

## How It Works

1. Builds a metadata mapping (table ID → table ID, field ID → field ID) between source and target
2. Detects and clones any model cards referenced via `source-card` (recursively handles nested models)
3. Clones each dashboard card, remapping all IDs in the MBQL query (both legacy and v0.58+ formats)
4. Creates a new dashboard with the same tabs
5. Adds cards to the dashboard with correct tab assignments
6. Transfers dashboard parameters (filters) with remapped field references

## Limitations

- Both source and target views/tables must have matching schema and column names for GUI-based questions
- Tables/columns not found in the target are skipped (warnings are printed)
- Native SQL queries are copied as-is — table and column names inside the SQL text are **not** automatically rewritten. If the target database uses different names, edit the SQL manually after cloning.

## Your ideas

<img src="https://github.com/user-attachments/assets/c1d76dea-68ef-40f2-8328-044d861add8a?raw=true" width="300" />

Let me know if you are looking for any update needed

## License

MIT
