SET XACT_ABORT ON;

BEGIN TRANSACTION;

INSERT INTO dbo.MetricDefinition (
    metric_code,
    unit,
    min_value,
    max_value,
    decimal_places
)
SELECT
    seed.metric_code,
    seed.unit,
    seed.min_value,
    seed.max_value,
    seed.decimal_places
FROM (
    VALUES
        ('temperature', N'°C',         -40.00,    85.00, 2),
        ('airHumidity', N'%RH',          0.00,   100.00, 2),
        ('pressure',    N'hPa',        300.00,  1100.00, 2),
        ('light',       N'lux',          0.00, 65535.00, 1),
        ('soilMoisture', N'% tương đối', 0.00,   100.00, 1)
) AS seed (
    metric_code,
    unit,
    min_value,
    max_value,
    decimal_places
)
WHERE NOT EXISTS (
    SELECT 1
    FROM dbo.MetricDefinition AS existing
    WHERE existing.metric_code = seed.metric_code
);
-- Tạo Farm nếu chưa tồn tại
IF NOT EXISTS (
    SELECT 1
    FROM dbo.Farm
    WHERE farm_code = N'FARM-01'
)
BEGIN
    INSERT INTO dbo.Farm (farm_code, name, description)
    VALUES (N'FARM-01', N'Farm demo', NULL);
END;

-- Lấy ID thực tế, không giả định ID luôn bằng 1
DECLARE @farm_id BIGINT;

SELECT @farm_id = farm_id
FROM dbo.Farm
WHERE farm_code = N'FARM-01';

-- Không âm thầm dùng Zone đã thuộc Farm khác
IF EXISTS (
    SELECT 1
    FROM dbo.Zone
    WHERE zone_code IN (N'INDOOR', N'OUTDOOR')
      AND farm_id <> @farm_id
)
BEGIN
    THROW 50001, N'Zone seed đang thuộc Farm khác.', 1;
END;

-- Tạo các Zone còn thiếu
INSERT INTO dbo.Zone (farm_id, zone_code, name, description)
SELECT
    @farm_id,
    seed.zone_code,
    seed.name,
    NULL
FROM (
    VALUES
        (N'INDOOR', N'Trong nhà'),
        (N'OUTDOOR', N'Ngoài trời')
) AS seed (zone_code, name)
WHERE NOT EXISTS (
    SELECT 1
    FROM dbo.Zone AS existing
    WHERE existing.zone_code = seed.zone_code
);
COMMIT TRANSACTION;