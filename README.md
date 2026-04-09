# Metabase Dashboard Cloner

Clone Metabase dashboards across databases or swap source views within the same database. Preserves tabs, models, dashboard filters, and card layout.

## Scripts

| File | Use case |
|------|----------|
| `clone_dashboard_interactive_en.py` | Clone a dashboard to a **different database** (same schema, different DB) |
| `clone_view_simple_en.py` | Clone a dashboard within the **same database**, replacing the source view/table with another |

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

1. Open the script you need and edit the **SETTINGS** section at the top:

```python
METABASE_URL = "https://metabase.your-company.com/"
MB_USERNAME = "your@email.com"
MB_PASSWORD = "your-password"
```

2. Run the script:

```bash
python clone_dashboard_interactive_en.py
# or
python clone_view_simple_en.py
```

3. Follow the interactive prompts.

### clone_dashboard_interactive_en.py

Prompts for:
- **Target database ID** — the database to clone the dashboard into
- **New dashboard name** — name for the cloned dashboard

Loops so you can clone to multiple databases in one run.

### clone_view_simple_en.py

Prompts for:
- **Target view name** — the view/table to swap in (same schema as source)
- **New dashboard name**

All other settings (source view, schema, database, dashboard ID) are hardcoded in the script.

## Supported Query Types

All three types are fully supported:

- **GUI questions** — standard MBQL queries built through the query builder. Table and field IDs are remapped to the target.
- **Models** — cards of type `model` used as data sources (`source-card`). Models are cloned recursively, including nested model references, and all dependent cards are updated to point to the cloned models.
- **Native SQL queries** — SQL questions are cloned as-is. The SQL text itself is not rewritten, but card metadata, visualization settings, and dashboard bindings are preserved.

## What Gets Cloned

- ✅ Dashboard cards (queries) with remapped table/field IDs
- ✅ Models (`source-card` references) — cloned recursively with nested dependencies
- ✅ Native SQL queries — cloned and added to the dashboard
- ✅ Dashboard tabs (preserved layout)
- ✅ Dashboard parameters (filters) with remapped field bindings
- ✅ Card positions, sizes, and visualization settings
- ✅ New collection created under the same parent, named after the new dashboard

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
