from sqlalchemy import text


def get_node_report_context(session, node_id: str) -> dict | None:
    row = (
        session.execute(
            text(
                "SELECT n.node_id, n.device_code, n.name AS node_name, "
                "z.zone_id, z.zone_code, z.name AS zone_name, "
                "f.farm_id, f.farm_code, f.name AS farm_name "
                "FROM dbo.Node AS n "
                "JOIN dbo.Zone AS z ON z.zone_id = n.zone_id "
                "JOIN dbo.Farm AS f ON f.farm_id = z.farm_id "
                "WHERE n.node_id = :id"
            ),
            {"id": int(node_id)},
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def aggregate_node_measurements(
    session, *, node_id: int, lo: int, hi: int
) -> tuple[int, list[dict]]:
    if lo > hi:
        n_total = 0
        metric_rows = (
            session.execute(
                text(
                    "SELECT metric_code, unit, "
                    "CAST(0 AS BIGINT) AS seen_count, "
                    "CAST(0 AS BIGINT) AS n_valid, "
                    "CAST(0 AS BIGINT) AS bad_rows, "
                    "NULL AS min_value, NULL AS max_value, NULL AS sum_value "
                    "FROM dbo.MetricDefinition "
                    "WHERE metric_code IN "
                    "('temperature', 'airHumidity', 'pressure', 'light', 'soilMoisture') "
                    "ORDER BY CASE metric_code "
                    "WHEN 'temperature' THEN 1 WHEN 'airHumidity' THEN 2 "
                    "WHEN 'pressure' THEN 3 WHEN 'light' THEN 4 ELSE 5 END"
                )
            )
            .mappings()
            .all()
        )
        return n_total, [dict(r) for r in metric_rows]

    n_total = int(
        session.execute(
            text(
                "SELECT COUNT_BIG(*) FROM dbo.Measurement "
                "WHERE node_id = :node_id AND t_node IS NOT NULL "
                "AND t_node >= :lo AND t_node <= :hi"
            ),
            {"node_id": int(node_id), "lo": int(lo), "hi": int(hi)},
        ).scalar_one()
    )

    metric_rows = (
        session.execute(
            text(
                "WITH H AS ("
                "  SELECT measurement_id FROM dbo.Measurement "
                "  WHERE node_id = :node_id AND t_node IS NOT NULL "
                "  AND t_node >= :lo AND t_node <= :hi"
                "), S AS ("
                "  SELECT v.metric_code, "
                "  COUNT_BIG(*) AS seen_count, "
                "  COUNT_BIG(CASE WHEN v.read_status = 'OK' AND v.value IS NOT NULL THEN 1 END) "
                "    AS n_valid, "
                "  COUNT_BIG(CASE WHEN NOT ("
                "    (v.read_status = 'OK' AND v.value IS NOT NULL) "
                "    OR (v.read_status IN ('READ_ERROR', 'INVALID_VALUE') AND v.value IS NULL)"
                "  ) THEN 1 END) AS bad_rows, "
                "  MIN(CASE WHEN v.read_status = 'OK' AND v.value IS NOT NULL THEN v.value END) "
                "    AS min_value, "
                "  MAX(CASE WHEN v.read_status = 'OK' AND v.value IS NOT NULL THEN v.value END) "
                "    AS max_value, "
                "  SUM(CASE WHEN v.read_status = 'OK' AND v.value IS NOT NULL "
                "    THEN CAST(v.value AS DECIMAL(38, 4)) END) AS sum_value "
                "  FROM H JOIN dbo.MeasurementValue AS v ON v.measurement_id = H.measurement_id "
                "  GROUP BY v.metric_code"
                ") "
                "SELECT d.metric_code, d.unit, "
                "ISNULL(s.seen_count, 0) AS seen_count, "
                "ISNULL(s.n_valid, 0) AS n_valid, "
                "ISNULL(s.bad_rows, 0) AS bad_rows, "
                "s.min_value, s.max_value, s.sum_value "
                "FROM dbo.MetricDefinition AS d "
                "LEFT JOIN S AS s ON s.metric_code = d.metric_code "
                "WHERE d.metric_code IN "
                "('temperature', 'airHumidity', 'pressure', 'light', 'soilMoisture') "
                "ORDER BY CASE d.metric_code "
                "WHEN 'temperature' THEN 1 WHEN 'airHumidity' THEN 2 "
                "WHEN 'pressure' THEN 3 WHEN 'light' THEN 4 ELSE 5 END"
            ),
            {"node_id": int(node_id), "lo": int(lo), "hi": int(hi)},
        )
        .mappings()
        .all()
    )
    return n_total, [dict(r) for r in metric_rows]
