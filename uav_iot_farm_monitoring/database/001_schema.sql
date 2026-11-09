IF DB_ID(N'FarmIoT_DB') IS NULL
    CREATE DATABASE FarmIoT_DB;
GO

USE FarmIoT_DB;
GO

IF OBJECT_ID(N'dbo.users', N'U') IS NULL
CREATE TABLE dbo.users (
    id INT IDENTITY(1,1) PRIMARY KEY,
    username NVARCHAR(80) NOT NULL UNIQUE,
    password_hash NVARCHAR(255) NOT NULL,
    role NVARCHAR(30) NOT NULL CHECK (role IN (N'admin', N'operator')),
    is_active BIT NOT NULL CONSTRAINT DF_users_active DEFAULT 1,
    created_at DATETIME2 NOT NULL CONSTRAINT DF_users_created DEFAULT SYSUTCDATETIME()
);
GO

IF OBJECT_ID(N'dbo.farms', N'U') IS NULL
CREATE TABLE dbo.farms (
    id INT IDENTITY(1,1) PRIMARY KEY,
    name NVARCHAR(150) NOT NULL UNIQUE,
    description NVARCHAR(500) NULL,
    created_at DATETIME2 NOT NULL CONSTRAINT DF_farms_created DEFAULT SYSUTCDATETIME()
);
GO

IF OBJECT_ID(N'dbo.zones', N'U') IS NULL
CREATE TABLE dbo.zones (
    id INT IDENTITY(1,1) PRIMARY KEY,
    farm_id INT NOT NULL,
    name NVARCHAR(150) NOT NULL,
    latitude DECIMAL(10,7) NULL,
    longitude DECIMAL(10,7) NULL,
    CONSTRAINT FK_zones_farm FOREIGN KEY (farm_id) REFERENCES dbo.farms(id),
    CONSTRAINT UQ_zones_farm_name UNIQUE (farm_id, name)
);
GO

IF OBJECT_ID(N'dbo.sensor_nodes', N'U') IS NULL
CREATE TABLE dbo.sensor_nodes (
    id INT IDENTITY(1,1) PRIMARY KEY,
    sensor_id NVARCHAR(80) NOT NULL UNIQUE,
    name NVARCHAR(150) NOT NULL,
    zone_id INT NOT NULL,
    api_key NVARCHAR(150) NOT NULL UNIQUE,
    is_active BIT NOT NULL CONSTRAINT DF_sensor_nodes_active DEFAULT 1,
    CONSTRAINT FK_sensor_nodes_zone FOREIGN KEY (zone_id) REFERENCES dbo.zones(id)
);
GO

IF OBJECT_ID(N'dbo.gateways', N'U') IS NULL
CREATE TABLE dbo.gateways (
    id INT IDENTITY(1,1) PRIMARY KEY,
    gateway_id NVARCHAR(80) NOT NULL UNIQUE,
    name NVARCHAR(150) NOT NULL,
    api_key NVARCHAR(150) NOT NULL UNIQUE,
    is_active BIT NOT NULL CONSTRAINT DF_gateways_active DEFAULT 1,
    last_seen_at DATETIME2 NULL
);
GO

IF OBJECT_ID(N'dbo.collection_missions', N'U') IS NULL
CREATE TABLE dbo.collection_missions (
    id INT IDENTITY(1,1) PRIMARY KEY,
    farm_id INT NOT NULL,
    gateway_id INT NOT NULL,
    name NVARCHAR(150) NOT NULL,
    status NVARCHAR(40) NOT NULL CONSTRAINT DF_mission_status DEFAULT N'ready',
    operator_note NVARCHAR(1000) NULL,
    created_at DATETIME2 NOT NULL CONSTRAINT DF_mission_created DEFAULT SYSUTCDATETIME(),
    CONSTRAINT FK_missions_farm FOREIGN KEY (farm_id) REFERENCES dbo.farms(id),
    CONSTRAINT FK_missions_gateway FOREIGN KEY (gateway_id) REFERENCES dbo.gateways(id)
);
GO

IF OBJECT_ID(N'dbo.mission_points', N'U') IS NULL
CREATE TABLE dbo.mission_points (
    id INT IDENTITY(1,1) PRIMARY KEY,
    mission_id INT NOT NULL,
    sensor_node_id INT NOT NULL,
    sort_order INT NOT NULL,
    status NVARCHAR(40) NOT NULL CONSTRAINT DF_mission_points_status DEFAULT N'pending',
    error_reason NVARCHAR(500) NULL,
    CONSTRAINT FK_mission_points_mission FOREIGN KEY (mission_id) REFERENCES dbo.collection_missions(id),
    CONSTRAINT FK_mission_points_node FOREIGN KEY (sensor_node_id) REFERENCES dbo.sensor_nodes(id),
    CONSTRAINT UQ_mission_point_order UNIQUE (mission_id, sort_order)
);
GO

IF OBJECT_ID(N'dbo.measurements', N'U') IS NULL
CREATE TABLE dbo.measurements (
    id BIGINT IDENTITY(1,1) PRIMARY KEY,
    sensor_node_id INT NOT NULL,
    gateway_id INT NULL,
    sequence_no BIGINT NOT NULL,
    temperature DECIMAL(6,2) NULL,
    air_humidity DECIMAL(6,2) NULL,
    pressure DECIMAL(8,2) NULL,
    light DECIMAL(10,2) NULL,
    soil_moisture DECIMAL(6,2) NULL,
    source NVARCHAR(30) NOT NULL CONSTRAINT DF_measurements_source DEFAULT N'physical',
    quality NVARCHAR(30) NOT NULL CONSTRAINT DF_measurements_quality DEFAULT N'valid',
    measured_at DATETIME2 NOT NULL,
    collected_at DATETIME2 NULL,
    received_at DATETIME2 NOT NULL CONSTRAINT DF_measurements_received DEFAULT SYSUTCDATETIME(),
    CONSTRAINT FK_measurements_node FOREIGN KEY (sensor_node_id) REFERENCES dbo.sensor_nodes(id),
    CONSTRAINT FK_measurements_gateway FOREIGN KEY (gateway_id) REFERENCES dbo.gateways(id),
    CONSTRAINT UQ_measurements_node_sequence UNIQUE (sensor_node_id, sequence_no)
);
GO

IF OBJECT_ID(N'dbo.alerts', N'U') IS NULL
CREATE TABLE dbo.alerts (
    id INT IDENTITY(1,1) PRIMARY KEY,
    sensor_node_id INT NULL,
    alert_type NVARCHAR(80) NOT NULL,
    severity NVARCHAR(30) NOT NULL,
    status NVARCHAR(30) NOT NULL CONSTRAINT DF_alerts_status DEFAULT N'open',
    message NVARCHAR(500) NOT NULL,
    created_at DATETIME2 NOT NULL CONSTRAINT DF_alerts_created DEFAULT SYSUTCDATETIME(),
    handled_at DATETIME2 NULL,
    handled_by INT NULL,
    note NVARCHAR(1000) NULL,
    CONSTRAINT FK_alerts_node FOREIGN KEY (sensor_node_id) REFERENCES dbo.sensor_nodes(id),
    CONSTRAINT FK_alerts_user FOREIGN KEY (handled_by) REFERENCES dbo.users(id)
);
GO
