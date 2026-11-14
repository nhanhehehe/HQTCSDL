# HQTCSDL

Repository: https://github.com/nhanhehehe/HQTCSDL

## Team workflow

- `main`: stable releases; do not push directly. Promote reviewed, tested changes from `develop` using a pull request.
- `develop`: integration branch. Create task branches from its latest commit and open PRs back to it.
- Branch names: `<type>/<issue-number>-<short-description>`, e.g. `feat/123-ingestion-validation` (example issue number).
- Read [CONTRIBUTING.md](CONTRIBUTING.md) before starting work.

## Existing code

The inherited application is under [uav_iot_farm_monitoring/](uav_iot_farm_monitoring/). Its presence on main does not certify compliance with the current requirements or contract. The repository currently includes a plain JavaScript frontend, an older sync endpoint/ACK format and mission functionality; these need a separate review against the current design before reuse. Existing SQLite tests do not establish SQL Server compatibility.

This workflow change preserves the application and its history. Do not rework business logic in a repository-setup PR.

## Design references

- [03.1 Integration contract](https://bengame2006.atlassian.net/wiki/spaces/HQTCSDL/pages/13729794)
- [03.3 Architecture](https://bengame2006.atlassian.net/wiki/spaces/HQTCSDL/pages/14188568)
- [03.4 Shared database design](https://bengame2006.atlassian.net/wiki/spaces/HQTCSDL/pages/14352390)
- [03.5 Flow design](https://bengame2006.atlassian.net/wiki/spaces/HQTCSDL/pages/14319653)
- [03.6 Engineering foundation](https://bengame2006.atlassian.net/wiki/spaces/HQTCSDL/pages/14647366)

The initial CI checks PR routing and branch naming only. Application build, integration, SQL Server and hardware checks are not yet configured or certified. Repository rules must separately enforce review and required checks; this README and a green workflow alone do not block direct pushes.
