# iTaxonomy — EIS Taxonomy Manager

A local web application and Python high-volume classification engine based on **Statement of Work: Acquired Company Taxonomy Development** (October 27, 2026). It supports source-file profiling, Client-owned Level 1 plus Vendor-managed Levels 2–4, reusable deterministic rules, bulk classification, rule traceability, exception resolution, SME approval, sampling, metrics, audit history, and Excel/CSV output.

## Run it

Requirements: Python 3.10+ and `openpyxl` for Excel upload/export. CSV needs only the Python standard library.

```powershell
py -m pip install -r requirements.txt
py server.py
```

Or on Windows, double-click `run.bat` (it installs `openpyxl` if it is missing). Then open **http://127.0.0.1:8765**. Keep the terminal window open while using the app. `Ctrl+C` stops it. The app binds to localhost by default. Set `EIS_PORT`, `EIS_HOST`, or `EIS_DB` to change the port, bind address, or SQLite database path.

Run the included engine checks with `py -m unittest -v test_engine` from this folder.

No login or cloud AI service is configured: the application is intended for a single trusted local operator. Do not bind it to a network interface without adding authentication, access controls, HTTPS, and an approved data protection design.

If the page reports `ERR_EMPTY_RESPONSE` for `/api/state`, confirm the Python server terminal is still running and use the same local address printed there. Stop it with `Ctrl+C`, restart with `py server.py`, and check the terminal for a traceback; API errors now also return a JSON error message when possible.

## First use

1. For a walkthrough, select **Load demo workspace**. Demo records, rules, and paths are visibly named or defined as illustrative. The demo uses examples present in the SOW; it is not Allied Bearings' approved Level 1 or taxonomy.
2. For project data, upload an Excel `.xlsx`/`.xlsm` or CSV with one SKU per row, a SKU identifier, and a product description. Column matching recognizes common names (SKU/item/material/part number; description/product description). Optional fields include MPN, Product Series, Product Type Code, Active/Status, and Sales. Confirm the detected columns and active status meaning against each source file.
3. Add only the Client-provided Level 1 values, then define contiguous paths and a one-line definition for each taxonomy path/node. The app starts with no Client taxonomy.
4. Create deterministic rules using description, SKU, MPN, series, or product type code and contains, prefix, exact, or regular-expression matching. Each rule maps to a full path. Lower numeric priority wins. Equal top-priority matches to different paths become a conflict exception.
5. Approve the taxonomy and rules after SME/Client review. A rule can be approved only when its full Level 1–4 path exactly matches an approved taxonomy path; the Rules table shows the reason a rule remains pending. Bulk classification refuses to run without approved rules. Run by file or across uploaded files.
6. Resolve unmatched/conflicting rows manually (the decision and reviewer note are audited), or draft a reusable rule from the pattern suggestions, confirm its path, and approve it. Manual decisions are not counted toward rule-based coverage.
7. Approve classified results for handover. Draw up to 200 random active classified records per file, have an SME record whether each is correct, and record the expected path for incorrect samples. The app reports measured coverage and accuracy alongside the SOW targets.
8. Export Excel or CSV. Exports retain the source filename, original row number and SKU, description, MPN, active status, Level 1–4, classification status, rule ID, rule trace and SME decision details. This tool does not load into client systems.

### AEROS sample workbook field mapping

For `PRODUCTTAXONOMY AEROS.xlsx`, upload the `PRODUCT` sheet. The app recognizes `PRODUCTID` as the SKU identifier, `PRODUCTNAME` as description, `Activebit` as active status, and `ACTIVESALES` as sales. The file has 870 rows and all 870 are marked active. It has no populated MPN, product type, product family, or Level 1–4 taxonomy values; `PRODUCTKEY` is a distinct internal key, not an MPN. The `Complete List` sheet is empty. Product names can supply marker candidates for SME review, but the workbook does not supply an approved hierarchy to enter in the taxonomy form.

## SOW interpretation and explicit assumptions

- The SOW says the Client defines Level 1; the Vendor builds Levels 2–4 beneath it. Accordingly, there is no seeded Client taxonomy or client business rule. The demo's BEARINGS examples are illustrative examples quoted or adapted from the SOW only and are not approved Client content.
- “AI-assisted pattern discovery” is implemented locally and transparently: repeated series/type-code markers are surfaced as rule hypotheses, and an exception recommendation finds the closest classified description in the same file using token overlap. The suggested existing path, example SKU and overlap score are shown to an SME. The SME must explicitly accept a suggestion as a manual decision; this does not create/apply a rule or count as rule-based coverage. No generative model or external service is called, and no taxonomy path is inferred when there is no similar classified example.
- Inactive rows are retained, excluded from classification and from the rule coverage denominator, in line with the SOW. When a file has no recognized Active/Status field, all its rows are treated as active; inspect the upload inventory.
- Rule coverage is active rows currently classified/approved by a rule divided by active rows. A row unresolved as an exception is not covered. Spot-check accuracy uses only recorded sample decisions, so it is blank until at least one check is recorded. The SOW target calls for 200 per file; partial samples are possible and the sample count is displayed.
- The UI can manage paths and offers a definition field per path. The Client remains responsible for naming conventions, taxonomy approval and product-specialist validation.
- Excel `.xls` legacy files are not accepted; save them as `.xlsx` or CSV first. The server holds each upload in SQLite and calculates classifications in-process; a single source file can contain about 90,000 rows. Upload parsing currently loads the source file into memory before inserting rows, so provide suitable memory and disk for larger files. XLSX reads the first worksheet; CSV rows are parsed with the Python standard library.
- There is no authentication, concurrent multi-user workflow, external AI endpoint, ERP integration, or automated Client system load. This is a working local MVP, not a hosted multi-tenant production service.

## Delivered SOW-aligned outputs

- File inventory, detected columns, marker group counts, SKU counts and sales input retention.
- Taxonomy path list, definitions and approval state; reusable rule table with priorities and approval state.
- Classified source rows with deterministic rule ID and a human-readable match trace.
- Exception queue for unmatched or conflicting SKUs and audited manual SME decisions.
- Random spot-check entry and accuracy metrics, rule coverage metrics and action audit trail.
- Load-ready Excel/CSV export keyed by the original SKU identifier.
- This setup and maintenance guide. The app does not claim the 90% coverage or 98% accuracy targets have been met until measured on Client data.

## Project layout

- `server.py` - Python HTTP API and classification engine
- `index.html` - web application interface
- `demo.csv` - illustrative small sample
- Taxonomy and rules tabs accept CSV/XLSX imports; CSV templates are available in the app. Bulk classification accepts source file uploads directly.
- Unwanted rules can be deleted individually from the rules table; the deletion is audited. Existing classified rows keep their recorded path and trace until classification is run again.
- `requirements.txt` - optional Excel dependency
- `run.bat` - Windows launcher
- `data/` - local SQLite database, created at runtime (excluded from the project ZIP)

## Maintenance and audit

Add or revise rules through **Taxonomy & rules**, review the new mapping, then approve. Re-run the relevant file so the current active-rule set is applied consistently. Re-review samples after rule changes. The audit page records uploads, rule/taxonomy creation and approval, classification, SME decisions, spot checks and final approvals. Back up the SQLite file before replacing it or moving the application. Use export files as deliverables; importing/exporting does not alter a Client system.

## Vercel deployment (iTaxonomy)

This folder is the Vercel-prepared copy, branded **iTaxonomy**. The original local app remains separate. The hosted build uses the same classification and review logic, serves the browser application from `index.html`, and uses PostgreSQL when `DATABASE_URL` is set. For local use, it continues to use SQLite.

### Deploy steps

1. Create a private GitHub repository named `iTaxonomy` and upload the contents of this folder to its repository root. Do not upload a `.env` file, database dump, or client workbook.
2. In Vercel, choose **Add New → Project**, import the `iTaxonomy` repository, and keep the project root set to `./`. The included `vercel.json` routes API requests to the Python ASGI function.
3. In the Vercel project, open **Storage / Marketplace** and add a PostgreSQL provider such as Neon. Connect it to the project and select the Preview and Production environments as needed. Confirm that the provider adds `DATABASE_URL` to the Vercel project.
4. Deploy a Preview first. The API creates its tables in the connected database on startup. Open the preview URL and confirm the workspace loads; load demo data and verify a small upload, classification, and export.
5. After the preview works, deploy to Production from the selected production branch. Keep the database branch/environment separate from preview if the provider offers it.
6. In Vercel project settings, enable Deployment Protection or otherwise restrict access. This MVP has no user login or role management, so it is not suitable for a public, unrestricted deployment with client records.

### Hosted constraints to plan for

- Vercel Functions cap request and response bodies at 4.5 MB. The current browser uploads send files through the API, so each source, taxonomy, or rules file must remain below that limit. Larger source files need direct-to-object-storage uploads and an asynchronous import/classification worker before production use. [Vercel function limits](https://vercel.com/docs/functions/limitations)
- The current bulk classification runs inside one function request. Validate actual file sizes and runtime against the Vercel plan before using it for the SOW's high-volume files. For reliable 90,000-row processing, move ingestion/classification to a background worker or managed job service and have the UI poll job status.
- iTaxonomy currently has no authentication, user roles, or multi-user conflict control. Protect the deployment at the Vercel project level during evaluation; implement application authentication and SME roles before broader client access.
- Configure `DATABASE_URL` only in Vercel's Environment Variables or through the database integration. Never place credentials in source control. Preview and Production environment variables apply to new deployments.
- Existing local SQLite records are not copied to hosted PostgreSQL. Export the needed data, then import it through the application's taxonomy, rule, and source-file uploads.

### Local check of the Vercel copy

```powershell
py -m pip install -r requirements.txt
py -m unittest -v test_engine
py server.py
```

Local run uses SQLite unless `DATABASE_URL` is set. Avoid pointing local testing at a production database.

### Vercel troubleshooting

If the overview reports that the workspace is unavailable, open the Vercel project → **Storage** and confirm PostgreSQL is connected to the same project and deployment environment. In **Settings → Environment Variables**, verify `DATABASE_URL` exists for that environment, then redeploy so the function receives it. If an API call still fails, inspect the failed `/api/...` request and its Vercel Function Logs; the hosted copy now displays the HTTP status or database startup error in the page message.

### Vercel API routes

Each hosted API endpoint has its own Python function file under `api/` (for example, `api/state.py` serves `/api/state` and `api/upload.py` serves `/api/upload`). There is no catch-all rewrite. Keep the repository root set to the folder containing `vercel.json`, `api/`, `vercel_adapter.py`, and `server.py`. After redeploy, `/api/state` should return a JSON object containing `files`, `taxonomy`, and `rules`.

### Vercel database requirement

Vercel's application directory is read-only, so hosted runs must use PostgreSQL. SQLite is kept for local Windows use only. The database layer accepts `DATABASE_URL`, `POSTGRES_URL`, `POSTGRES_URL_NON_POOLING`, or `POSTGRES_PRISMA_URL`; the Marketplace integration typically supplies `DATABASE_URL`. Connect the provider to this Vercel project and the Production environment, then redeploy. If the function responds with “PostgreSQL is not connected,” the selected deployment does not have a Postgres URL available.
