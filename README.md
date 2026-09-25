# stat

Data-only branch. Not part of the product.

`stat.csv` holds the GitHub star history of volcengine/OpenViking: one row per UTC day since 2026-01-05, columns `date,new,total`.
`update_stars.py` appends to it incrementally. The `Star Stats` workflow on `main` runs the script on every published release and commits the result here.

Read it from any page (CORS-enabled, cached up to 5 minutes):

    https://raw.githubusercontent.com/volcengine/OpenViking/stat/stat.csv

`total` counts stars that still existed when each day was fetched. Stars removed later are not subtracted from past rows.
